"""分级视图：同一台账，按角色给出不同可见范围。

- 老人或代办人：剩余额度、设备状态、故障期间安排、本人补贴明细；
- 设备厂商：设备序列号、合同类型、维修召回与当前责任方——不得读取
  照护等级、低收入资格等无关医疗明细，也不可见资金流水；
- 社区：资格状态与服务安排——无权查看完整财政资料（金额与资金流水）；
- 财政：从任何一笔支出追到设备实际使用与责任方。
"""
from __future__ import annotations

from .ledger import Ledger, LedgerError
from .model import ZERO, ApplicantState, DeviceState, days_between


class AccessDenied(LedgerError):
    """访问超出角色可见范围。"""


def applicant_portal(ledger: Ledger, ref: str, *, requester: str | None = None) -> dict:
    """老人或代办人视图：剩余额度与故障期间安排随时可查。"""
    refs = ledger.cluster_refs(ref)
    applicant = ledger.applicant(ref)
    agents = {a for v in (applicant.verifications if applicant else []) for a in v.agent_refs}
    if requester is not None and requester != ref and requester not in agents:
        raise AccessDenied(f"{requester} 不是 {ref} 的代办人，无权查询")
    quotas = []
    seen: set[tuple[str, int, int]] = set()
    for funding in ledger.fundings:
        if funding.applicant_ref not in refs:
            continue
        key = (funding.policy_id, funding.policy_version, funding.released_at.year)
        if key in seen:
            continue
        seen.add(key)
        policy = ledger.policy(funding.policy_id, funding.policy_version)
        used = ledger.annual_usage(refs, funding.policy_id, funding.released_at.year)
        quotas.append(
            {
                "policy_id": funding.policy_id,
                "policy_version": funding.policy_version,
                "year": funding.released_at.year,
                "annual_cap": policy.annual_cap,
                "used": used,
                "remaining": policy.annual_cap - used,
            }
        )
    devices = [d for d in ledger.devices if d.applicant_ref in refs]
    return {
        "applicant_ref": ref,
        "quotas": quotas,
        "devices": [_device_brief(d) for d in devices],
        "malfunction_arrangements": [
            {
                "serial_no": d.serial_no,
                "repair_start": r.start.isoformat(),
                "repair_end": r.end.isoformat() if r.end else None,
                "arrangement": r.arrangement,
                "note": r.note,
            }
            for d in devices
            for r in d.repairs
        ],
        "subsidies": [
            {
                "funding_id": f.funding_id,
                "policy_id": f.policy_id,
                "trigger": f.trigger,
                "amount": f.amount,
                "reversed": f.reversed_total,
                "net": f.net,
            }
            for f in ledger.fundings
            if f.applicant_ref in refs
        ],
        "agents": sorted(agents),
    }


def vendor_portal(ledger: Ledger, serial_no: str) -> dict:
    """设备厂商视图：仅设备与合同、维修召回、当前责任方。

    不含照护等级、年龄、低收入资格等医疗明细，也不含任何资金信息。
    """
    device = ledger.device(serial_no)
    if device is None:
        raise LedgerError(f"设备 {serial_no} 不存在")
    return {
        "serial_no": device.serial_no,
        "category": device.category,
        "contract_id": device.contract_id,
        "contract_type": device.contract_type,
        "status": device.status,
        "current_party": device.party,
        "repairs": [
            {"start": r.start.isoformat(), "end": r.end.isoformat() if r.end else None, "arrangement": r.arrangement}
            for r in device.repairs
        ],
        "recalls": [
            {"recall_id": c["recall_id"], "at": c["at"].isoformat(), "note": c["note"]} for c in device.recalls
        ],
        "stopped": (
            {"kind": device.stopped["kind"], "at": device.stopped["at"].isoformat(), "recovered_by": device.stopped["recovered_by"]}
            if device.stopped
            else None
        ),
    }


def community_portal(ledger: Ledger, ref: str) -> dict:
    """社区视图：资格状态与服务安排，不含资金流水与金额等财政资料。"""
    applicant = ledger.applicant(ref)
    if applicant is None:
        raise LedgerError(f"申请人 {ref} 不存在")
    agents = {a for v in applicant.verifications for a in v.agent_refs}
    devices = [d for d in ledger.devices if d.applicant_ref in ledger.cluster_refs(ref)]
    return {
        "applicant_ref": ref,
        "eligibility": _eligibility_rows(applicant),
        "devices": [
            {
                "serial_no": d.serial_no,
                "category": d.category,
                "status": d.status,
                "service_periods": [
                    {"start": s.isoformat(), "end": e.isoformat()} for s, e in d.service_periods
                ],
                "repairs": _repair_rows(d),
            }
            for d in devices
        ],
        "pending_associations": [
            {"with": a.pair[0] if a.pair[1] == ref else a.pair[1], "evidence": a.evidence, "status": a.status}
            for a in ledger.associations
            if ref in a.pair and a.status == "pending"
        ],
        "agents": sorted(agents),
    }


def finance_trace(ledger: Ledger, funding_id: str) -> dict:
    """财政视图：从一笔支出追到设备、实际服务期与当前责任方。"""
    funding = ledger.funding(funding_id)
    if funding is None:
        raise LedgerError(f"资金流水 {funding_id} 不存在")
    device = ledger.device(funding.serial_no)
    usage = None
    if device is not None and funding.period is not None:
        start, end = funding.period
        confirmed = sum(
            (days_between(max(s, start), min(e, end)) for s, e in device.service_periods if min(e, end) > max(s, start)),
            ZERO,
        )
        usage = {
            "settled_days": days_between(start, end),
            "confirmed_days": confirmed,
        }
    return {
        "funding": {
            "funding_id": funding.funding_id,
            "applicant_ref": funding.applicant_ref,
            "policy_id": funding.policy_id,
            "policy_version": funding.policy_version,
            "trigger": funding.trigger,
            "amount": funding.amount,
            "reversed": funding.reversed_total,
            "net": funding.net,
            "exclusion_group": funding.exclusion_group,
            "period": (
                {"start": funding.period[0].isoformat(), "end": funding.period[1].isoformat()} if funding.period else None
            ),
            "reversals": [
                {"amount": r.amount, "reason": r.reason, "at": r.at.isoformat(), "note": r.note} for r in funding.reversals
            ],
        },
        "device": (
            {
                "serial_no": device.serial_no,
                "category": device.category,
                "contract_id": device.contract_id,
                "contract_type": device.contract_type,
                "status": device.status,
                "original_party": device.original_party,
                "current_party": device.party,
                "stopped": device.stopped["kind"] if device.stopped else None,
            }
            if device
            else None
        ),
        "actual_usage": usage,
        "identity_cluster": sorted(ledger.cluster_refs(funding.applicant_ref)),
    }


def recall_responsibilities(ledger: Ledger) -> list[dict]:
    """召回清单：给出设备当前责任方，而非最初购买人。"""
    rows = []
    for device in ledger.devices:
        for recall in device.recalls:
            rows.append(
                {
                    "serial_no": device.serial_no,
                    "recall_id": recall["recall_id"],
                    "recalled_at": recall["at"].isoformat(),
                    "original_party": device.original_party,
                    "current_party": device.party,
                }
            )
    return rows


def _device_brief(device: DeviceState) -> dict:
    return {
        "serial_no": device.serial_no,
        "category": device.category,
        "contract_type": device.contract_type,
        "status": device.status,
    }


def _repair_rows(device: DeviceState) -> list[dict]:
    return [
        {"start": r.start.isoformat(), "end": r.end.isoformat() if r.end else None, "arrangement": r.arrangement}
        for r in device.repairs
    ]


def _eligibility_rows(applicant: ApplicantState) -> list[dict]:
    return [
        {
            "policy_id": v.policy_id,
            "policy_version": v.policy_version,
            "category": v.category,
            "care_level": v.care_level,
            "eligible": v.eligible,
            "at": v.at.isoformat() if v.at else None,
        }
        for v in applicant.verifications
    ]
