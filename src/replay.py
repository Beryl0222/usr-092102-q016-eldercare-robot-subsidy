"""事件存储与重放。

事件只追加、不可变；重放按 occurred_at 排序回放全部事件，重建
政策、资格、设备与资金四类聚合状态。任何状态（剩余额度、冲正金额、
召回责任方）都必须能由事件流重放得到，而不是依赖现场数据库。
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from src.contracts import EVENT_AGGREGATE


@dataclass
class StoredEvent:
    seq: int
    payload: dict[str, Any]

    @property
    def event_id(self) -> str:
        return self.payload["event_id"]

    @property
    def occurred_at(self) -> datetime:
        return datetime.fromisoformat(self.payload["occurred_at"])


class EventStore:
    def __init__(self) -> None:
        self._events: list[StoredEvent] = []
        self._ids: set[str] = set()
        self._versions: dict[str, int] = {}

    def append(self, payload: dict[str, Any]) -> StoredEvent:
        eid = payload.get("event_id")
        if not eid:
            raise ValueError("事件缺少 event_id")
        if eid in self._ids:
            raise ValueError(f"event_id 重复：{eid}")

        etype = payload.get("event_type")
        if etype not in EVENT_AGGREGATE:
            raise ValueError(f"未知事件类型：{etype}")
        atype = payload.get("aggregate_type")
        agg_id = payload.get("aggregate_id")
        if atype != EVENT_AGGREGATE[etype]:
            raise ValueError(f"{etype} 必须归属于聚合 {EVENT_AGGREGATE[etype]}，实际为 {atype}")
        if not agg_id:
            raise ValueError("事件缺少 aggregate_id")

        version = payload.get("version")
        if not isinstance(version, int) or version < 1:
            raise ValueError("version 必须是正整数")
        expect = self._versions.get(agg_id, 0) + 1
        if version != expect:
            raise ValueError(f"聚合 {agg_id} 版本号应为 {expect}，实际为 {version}")

        stored = StoredEvent(seq=len(self._events), payload=payload)
        self._events.append(stored)
        self._ids.add(eid)
        self._versions[agg_id] = version
        return stored

    def replay(self) -> list[StoredEvent]:
        """按发生时间（并列时按追加顺序）返回事件。"""
        return sorted(self._events, key=lambda e: (e.occurred_at, e.seq))

    def load_many(self, payloads: list[dict[str, Any]]) -> None:
        for p in payloads:
            self.append(p)


# 以 _d 结尾的处理器接收 state 与事件 payload，直接就地更新。
Handler = Callable[[dict[str, Any], dict[str, Any]], None]


def _parse_day(value: str) -> date:
    return date.fromisoformat(value[:10])


def _policy_from_payload(d: dict[str, Any]) -> Any:
    from src.model import PolicyRule, SupportKind

    return PolicyRule(
        policy_code=d["policy_code"],
        version=d["policy_version"],
        region=d["region"],
        kind=SupportKind(d["kind"]),
        effective_from=_parse_day(d["effective_from"]),
        effective_to=_parse_day(d["effective_to"]) if d.get("effective_to") else None,
        min_age=d.get("min_age", 0),
        min_care_level=d.get("min_care_level", "none"),
        low_income_required=d.get("low_income_required", False),
        certified_categories=frozenset(d.get("certified_categories", ())),
        purchase_subsidy_bp=d.get("purchase_subsidy_bp", 0),
        lease_catalog_daily_cap=d.get("lease_catalog_daily_cap", 0),
        per_item_cap=d.get("per_item_cap", 0),
        annual_cap=d.get("annual_cap", 0),
        mutex_group=d.get("mutex_group"),
    )


def _policy_key(d: dict[str, Any]) -> str:
    return f"{d['policy_code']}@{d['policy_version']}/{d['region']}"


def build_state(store: EventStore) -> dict[str, Any]:
    """重放事件流，返回可查询的履约状态与投影。"""
    from src.identity import IdentityLink, IdentityRegistry, LinkStatus, MutexResolution
    from src.ledger import FundingLedger

    state: dict[str, Any] = {
        "policies": {},
        "categories": set(),
        "eligibilities": {},
        "devices": {},
        "identity": IdentityRegistry(),
        "funding": FundingLedger(),
        "mutex_reviews": [],
    }

    def eligibility_record(d: dict[str, Any]) -> dict[str, Any]:
        return state["eligibilities"].setdefault(d["aggregate_id"], {
            "eligibility_id": d["aggregate_id"],
            "channels": [],
            "verified": [],
        })

    for ev in store.replay():
        d = ev.payload
        t = d["event_type"]

        if t == "POLICY_PUBLISHED":
            state["policies"][_policy_key(d)] = _policy_from_payload(d)

        elif t == "CATEGORY_CERTIFIED":
            state["categories"].add((d["policy_code"], d["policy_version"], d["category_code"]))

        elif t == "ELIGIBILITY_VERIFIED":
            rec = eligibility_record(d)
            rec["channels"].append(d["channel"])
            rec["verified"].append({
                "policy_key": f"{d['policy_code']}@{d['policy_version']}/{d['region']}",
                "age": d["age"],
                "care_level": d["care_level"],
                "low_income": d.get("low_income", False),
                "medical_details": d.get("medical_details", []),
                "document_ref": d["document_ref"],  # 证件材料的引用，不是材料全文
                "valid_from": d["valid_from"],
                "valid_to": d.get("valid_to"),
                "event_id": d["event_id"],
            })

        elif t == "CONTRACT_REGISTERED":
            state["devices"][d["aggregate_id"]] = {
                "device_service_id": d["aggregate_id"],
                "contract_id": d["contract_id"],
                "kind": d["kind"],
                "serial_no": d["serial_no"],
                "category_code": d["category_code"],
                "eligibility_id": d["eligibility_id"],
                "vendor_id": d["vendor_id"],
                "discounted_price": d.get("discounted_price"),
                "catalog_daily_rate": d.get("catalog_daily_rate"),
                "start_date": d.get("start_date"),
                "end_date": d.get("end_date"),
                "status": "contracted",
                "responsible_chain": [],
                "fault_periods": [],
                "service_periods": [],
                "returned_at": None,
                "recycled_at": None,
                "recalls": [],
            }

        elif t == "DEVICE_DELIVERED":
            dev = state["devices"][d["aggregate_id"]]
            dev["status"] = "in_service"
            dev["delivered_at"] = d["occurred_at"][:10]
            dev["responsible_chain"].append({
                "party": d["responsible_party"],
                "since": d["occurred_at"][:10],
                "reason": "交付",
            })

        elif t == "SERVICE_CONFIRMED":
            dev = state["devices"][d["aggregate_id"]]
            dev["service_periods"].append({
                "period_start": d["period_start"],
                "period_end": d["period_end"],
                "days_fulfilled": d["days_fulfilled"],
                "event_id": d["event_id"],
            })

        elif t == "FAULT_REPORTED":
            dev = state["devices"][d["aggregate_id"]]
            dev["status"] = "fault"
            dev["fault_periods"].append({
                "fault_from": d["fault_from"],
                "fault_to": None,
                "loaner_arranged": d.get("loaner_arranged", False),
                "arrangement": d.get("arrangement", ""),
            })

        elif t == "REPAIR_COMPLETED":
            dev = state["devices"][d["aggregate_id"]]
            dev["status"] = "in_service"
            dev["fault_periods"][-1]["fault_to"] = d["resumed_at"]

        elif t == "RECALL_ISSUED":
            dev = state["devices"][d["aggregate_id"]]
            dev["recalls"].append({
                "recall_id": d["recall_id"],
                "issued_at": d["occurred_at"][:10],
                "reason": d.get("reason", ""),
            })
            dev["status"] = "recalled"

        elif t == "RESPONSIBLE_PARTY_CHANGED":
            dev = state["devices"][d["aggregate_id"]]
            dev["responsible_chain"].append({
                "party": d["to_party"],
                "since": d["effective_at"],
                "reason": d.get("reason", ""),
            })

        elif t == "DEVICE_RETURNED":
            dev = state["devices"][d["aggregate_id"]]
            dev["status"] = "returned"
            dev["returned_at"] = d["occurred_at"][:10]
            if d.get("recycled"):
                dev["recycled_at"] = d["occurred_at"][:10]

        elif t == "SERVICE_TERMINATED":
            dev = state["devices"][d["aggregate_id"]]
            dev["status"] = "terminated"
            dev["end_date"] = d["effective_date"]
            dev["terminate_reason"] = d.get("reason", "")

        elif t == "IDENTITY_LINK_SUGGESTED":
            state["identity"].suggest(IdentityLink(
                link_id=d["aggregate_id"],
                eligibility_a=d["eligibility_a"],
                eligibility_b=d["eligibility_b"],
                reasons=tuple(d.get("reasons", ())),
            ))

        elif t == "IDENTITY_LINK_CONFIRMED":
            resolution = (
                MutexResolution.BLOCK if d["resolution"] == "block"
                else MutexResolution.ALLOW_COEXIST
            )
            state["identity"].confirm(
                d["aggregate_id"], decided_by=d["decided_by"],
                resolution=resolution, note=d.get("note", ""),
            )

        elif t == "IDENTITY_LINK_DISMISSED":
            state["identity"].dismiss(
                d["aggregate_id"], decided_by=d["decided_by"], note=d.get("note", ""),
            )

        elif t == "MUTEX_REVIEW_RESOLVED":
            # 跨部门复核留痕（民政初核、财政复核意见不一致时追加），
            # 聚合即身份关联本身（aggregate_id == link_id）。
            state["mutex_reviews"].append({
                "link_id": d["aggregate_id"],
                "decision": d["decision"],
                "reviewer": d["reviewer"],
                "note": d.get("note", ""),
                "event_id": d["event_id"],
            })

        elif t == "SUBSIDY_RELEASED":
            policy = state["policies"][f"{d['policy_code']}@{d['policy_version']}/{d['region']}"]
            state["funding"].release(
                entry_id=d["aggregate_id"],
                eligibility_id=d["eligibility_id"],
                device_service_id=d["device_service_id"],
                policy=policy,
                amount=d["amount"],
                trigger=d["trigger"],
                days_paid=d.get("days_paid"),
                event_id=d["event_id"],
            )

        elif t == "FUNDS_REVERSED":
            state["funding"].reverse(
                entry_id=d["funding_entry_id"],
                amount=d["amount"],
                reason=d["reason"],
                days_paid=d.get("days_paid"),
                days_fulfilled=d.get("days_fulfilled"),
                event_id=d["event_id"],
            )

    return state
