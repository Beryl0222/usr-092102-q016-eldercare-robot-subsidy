"""按角色裁剪的查询视图（字段级最小授权）。

- 老人/代办人：可查本人剩余额度、故障期间安排、设备与实际服务期。
- 财政：任何支出可追到实际使用（合同、序列号、服务期、冲正）。
- 民政：资格与政策核对可见；不直接操作资金流水。
- 社区：可登记服务与故障，但无权查看完整财政资料。
- 厂商：只看到与自己设备相关的运维信息，不得读取无关医疗明细。
"""

from collections.abc import Callable
from typing import Any

# 资格档案中的敏感字段
_MEDICAL_FIELDS = ("medical_details",)
_FINANCE_FIELDS = ("funding",)

Role = str
ELDER = "elder"          # 老人或代办人（视图按本人 eligibility_id 过滤）
FINANCE = "finance"      # 财政
CIVIL = "civil_affairs"  # 民政
COMMUNITY = "community"  # 社区
VENDOR = "vendor"        # 设备厂商


def _redact_eligibility(rec: dict, role: Role, viewer_eligibility: str | None) -> dict:
    out = dict(rec)
    verified = []
    for v in rec.get("verified", []):
        v = dict(v)
        if role in (VENDOR, COMMUNITY):
            # 厂商与社区只见资格结论（通过/不通过），不见医疗明细与证件全文
            for f in _MEDICAL_FIELDS:
                v.pop(f, None)
            v.pop("document_ref", None)
        if role == VENDOR:
            v.pop("low_income", None)
        verified.append(v)
    out["verified"] = verified
    return out


def elder_view(state: dict, eligibility_id: str) -> dict:
    """老人/代办人视图：本人剩余额度、设备状态、故障期间安排。"""
    devices = [d for d in state["devices"].values() if d["eligibility_id"] == eligibility_id]
    entries = state["funding"].entries_for(eligibility_id)
    quotas: dict[str, int] = {}
    for e in entries:
        k = f"{e.policy.policy_code}@{e.policy.version}/{e.policy.region}"
        quotas[k] = state["funding"].remaining_annual(eligibility_id, e.policy)
    return {
        "eligibility_id": eligibility_id,
        "remaining_annual_fen": quotas,  # -1 表示该政策无年度上限
        "devices": [
            {
                "device_service_id": d["device_service_id"],
                "serial_no": d["serial_no"],
                "status": d["status"],
                "service_periods": d["service_periods"],
                "fault_arrangements": [
                    {
                        "fault_from": p["fault_from"],
                        "fault_to": p["fault_to"],
                        "loaner_arranged": p["loaner_arranged"],
                        "arrangement": p["arrangement"],
                    }
                    for p in d["fault_periods"]
                ],
                "returned_at": d["returned_at"],
            }
            for d in devices
        ],
    }


def finance_trace_view(state: dict, entry_id: str) -> dict:
    """财政视图：支出 -> 设备序列号 -> 实际服务期与冲正，并给出召回当前责任方。"""
    trace = state["funding"].trace(entry_id)
    dev = state["devices"][trace["device_service_id"]]
    return {
        **trace,
        "contract_id": dev["contract_id"],
        "serial_no": dev["serial_no"],
        "category_code": dev["category_code"],
        "delivered_at": dev.get("delivered_at"),
        "service_periods": dev["service_periods"],
        "returned_at": dev["returned_at"],
        "recycled_at": dev["recycled_at"],
        "current_responsible_party": current_responsible_party(dev),
        "recalls": dev["recalls"],
    }


def recall_responsibility_view(state: dict, recall_id: str) -> list[dict]:
    """召回时按设备序列号找到当前责任方，而非最初购买/收货人。"""
    hits = []
    for d in state["devices"].values():
        if any(r["recall_id"] == recall_id for r in d["recalls"]):
            hits.append({
                "serial_no": d["serial_no"],
                "device_service_id": d["device_service_id"],
                "current_responsible_party": current_responsible_party(d),
                "responsibility_chain": d["responsible_chain"],
                "status": d["status"],
            })
    return hits


def vendor_view(state: dict, vendor_id: str) -> list[dict]:
    """厂商视图：仅本厂商设备的运维信息；无医疗明细、无资金金额。"""
    out = []
    for d in state["devices"].values():
        parties = {c["party"] for c in d["responsible_chain"]}
        if vendor_id not in parties:
            continue
        out.append({
            "device_service_id": d["device_service_id"],
            "serial_no": d["serial_no"],
            "category_code": d["category_code"],
            "status": d["status"],
            "fault_periods": d["fault_periods"],
            "recalls": d["recalls"],
            "eligibility_id": d["eligibility_id"],
            # 明确不含：年龄、照护等级、医疗明细、低收入状态、任何资金字段
        })
    return out


def community_view(state: dict) -> dict:
    """社区视图：服务与故障履行可见，完整财政资料不可见（金额与流水被剥离）。"""
    return {
        "devices": [
            {
                "device_service_id": d["device_service_id"],
                "eligibility_id": d["eligibility_id"],
                "serial_no": d["serial_no"],
                "status": d["status"],
                "service_periods": d["service_periods"],
                "fault_periods": d["fault_periods"],
                "start_date": d["start_date"],
                "end_date": d["end_date"],
                "returned_at": d["returned_at"],
                # 不含：补贴金额、资金条目、年度额度、医疗明细
            }
            for d in state["devices"].values()
        ],
        "pending_identity_links": [
            {
                "link_id": lid,
                "eligibility_a": l.eligibility_a,
                "eligibility_b": l.eligibility_b,
                "reasons": l.reasons,
            }
            for lid, l in state["identity"]._links.items()
            if l.status.value == "suggested"
        ],
    }


def current_responsible_party(device: dict) -> str:
    chain = device["responsible_chain"]
    if not chain:
        return device["vendor_id"]
    return chain[-1]["party"]
