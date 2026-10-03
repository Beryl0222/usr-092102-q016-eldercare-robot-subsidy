"""履约核对：把每笔补贴资金同设备实际服务期对账。

财政抽查的入口。回答三类问题：
- 已结算资金中，哪些区间没有实际服务确认或发生在停用之后（应冲回未履行部分）；
- 跨渠道待核对身份关联有哪些，确认后是否构成互斥资金冲突；
- 同人（已确认关联合并后）是否突破年度上限。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .ledger import Ledger
from .model import ZERO, days_between, money, reversal_due


def _coverage_days(periods: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> Decimal:
    """服务期并集在 [start, end] 内覆盖的天数。"""
    clips = []
    for s, e in periods:
        lo, hi = max(s, start), min(e, end)
        if hi > lo:
            clips.append((lo, hi))
    clips.sort()
    total = ZERO
    cur = None
    for s, e in clips:
        if cur is None:
            cur = [s, e]
        elif s <= cur[1]:
            cur[1] = max(cur[1], e)
        else:
            total += days_between(cur[0], cur[1])
            cur = [s, e]
    if cur is not None:
        total += days_between(cur[0], cur[1])
    return total


def reconciliation_report(ledger: Ledger, as_of: datetime | None = None) -> dict:
    """全量核对报告。as_of 缺省取台账中最晚事件时间。"""
    if as_of is None:
        as_of = max((parse for parse in (_event_time(e) for e in ledger.events) if parse), default=None)
    entries = [_entry_row(ledger, f, as_of) for f in ledger.fundings]
    return {
        "as_of": as_of.isoformat() if as_of else None,
        "funding_entries": entries,
        "identity": _identity_section(ledger),
        "exclusion_violations": _exclusion_violations(ledger),
        "cap_overruns": _cap_overruns(ledger),
    }


def _event_time(event: dict) -> datetime:
    from .model import parse_dt

    return parse_dt(event["occurred_at"])


def _entry_row(ledger: Ledger, funding, as_of: datetime | None) -> dict:
    row = {
        "funding_id": funding.funding_id,
        "applicant_ref": funding.applicant_ref,
        "policy_id": funding.policy_id,
        "policy_version": funding.policy_version,
        "serial_no": funding.serial_no,
        "trigger": funding.trigger,
        "amount": funding.amount,
        "reversed": funding.reversed_total,
        "net": funding.net,
    }
    device = ledger.device(funding.serial_no)
    stop_at = device.stopped["at"] if device and device.stopped else None
    if funding.period is not None:
        start, end = funding.period
        settled = days_between(start, end)
        elapsed_end = min(end, *(t for t in (stop_at, as_of) if t is not None))
        elapsed = days_between(start, max(start, elapsed_end))
        confirmed_elapsed = _coverage_days(device.service_periods, start, elapsed_end) if device else ZERO
        unconfirmed = max(ZERO, elapsed - confirmed_elapsed)
        post_stop_due = reversal_due(funding, stop_at) if stop_at else ZERO
        row.update(
            {
                "period": {"start": start.isoformat(), "end": end.isoformat()},
                "settled_days": settled,
                "elapsed_days": elapsed,
                "confirmed_days": confirmed_elapsed,
                "unconfirmed_elapsed_days": unconfirmed,
                "unconfirmed_elapsed_amount": money(funding.amount * unconfirmed / settled) if settled else ZERO,
                "post_stop_days": days_between(stop_at, end) if stop_at and stop_at < end else ZERO,
                "post_stop_reversal_due": post_stop_due,
                "post_stop_reversal_outstanding": max(ZERO, post_stop_due - funding.reversed_total),
            }
        )
    elif stop_at is not None:
        # 购置类退货：应冲回全额（扣除已冲正部分）
        row.update(
            {
                "post_stop_reversal_due": reversal_due(funding, stop_at),
                "post_stop_reversal_outstanding": max(ZERO, reversal_due(funding, stop_at) - funding.reversed_total),
            }
        )
    return row


def _identity_section(ledger: Ledger) -> dict:
    pending = [
        {"pair": list(a.pair), "evidence": a.evidence, "by": a.by}
        for a in ledger.associations
        if a.status == "pending"
    ]
    confirmed = [
        {"pair": list(a.pair), "evidence": a.evidence, "by": a.by}
        for a in ledger.associations
        if a.status == "confirmed"
    ]
    # 待核对关联两侧若已在同一互斥组发生资金，列为待核对冲突（不阻断）
    pending_conflicts = []
    for a in ledger.associations:
        if a.status != "pending":
            continue
        left, right = a.pair
        groups: dict[str, set[str]] = {}
        for f in ledger.fundings:
            if f.applicant_ref in (left, right) and f.net > 0:
                groups.setdefault(f.exclusion_group, set()).add(f.applicant_ref)
        for group, refs in groups.items():
            if refs == {left, right}:
                pending_conflicts.append({"pair": [left, right], "exclusion_group": group})
    return {"pending": pending, "confirmed": confirmed, "pending_conflicts": pending_conflicts}


def _exclusion_violations(ledger: Ledger) -> list[dict]:
    """已确认同人集合内，同一互斥组下不同政策仍有净额资金。"""
    clusters: set[frozenset[str]] = set()
    for f in ledger.fundings:
        clusters.add(frozenset(ledger.cluster_refs(f.applicant_ref)))
    violations = []
    for cluster in clusters:
        by_group: dict[str, list] = {}
        for f in ledger.fundings:
            if f.applicant_ref in cluster and f.net > 0 and f.exclusion_group:
                by_group.setdefault(f.exclusion_group, []).append(f)
        for group, fundings in by_group.items():
            policies = {f.policy_id for f in fundings}
            if len(policies) > 1:
                violations.append(
                    {
                        "cluster": sorted(cluster),
                        "exclusion_group": group,
                        "policies": sorted(policies),
                        "funding_ids": [f.funding_id for f in fundings],
                    }
                )
    return violations


def _cap_overruns(ledger: Ledger) -> list[dict]:
    usage: dict[tuple[frozenset[str], str, int], Decimal] = {}
    versions: dict[tuple[frozenset[str], str, int], int] = {}
    for f in ledger.fundings:
        cluster = frozenset(ledger.cluster_refs(f.applicant_ref))
        key = (cluster, f.policy_id, f.released_at.year)
        usage[key] = usage.get(key, ZERO) + f.net
        versions[key] = f.policy_version
    overruns = []
    for (cluster, policy_id, year), used in usage.items():
        policy = ledger.find_policy(policy_id, versions[(cluster, policy_id, year)])
        if policy is not None and used > policy.annual_cap:
            overruns.append(
                {
                    "cluster": sorted(cluster),
                    "policy_id": policy_id,
                    "year": year,
                    "annual_cap": policy.annual_cap,
                    "used": used,
                }
            )
    return overruns
