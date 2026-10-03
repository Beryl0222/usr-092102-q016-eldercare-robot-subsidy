"""事件溯源的养老机器人补贴履约台账。

所有资格确认、交付、服务确认、发放、维修与冲正都以
contracts/domain.schema.json 登记的事件标识写入（事件类型与聚合类型
不新增、不改名；业务细分通过 payload.kind 表达），台账状态由事件重放得出。

外部系统（电商、社区、财政）产生的事件按 append 入账，只校验信封与
聚合版本顺序；命令方法（verify_eligibility / deliver_device / …）在
入账前执行资格、上限与互斥等业务规则。两者共用同一份状态，保证重放一致。
"""
from __future__ import annotations

from .model import (
    ZERO,
    ApplicantState,
    Association,
    DeviceState,
    FundingState,
    Policy,
    Repair,
    Reversal,
    Verification,
    age_on,
    days_between,
    money,
    parse_day,
    parse_dt,
    reversal_due,
)
from .validator import validate_event


class LedgerError(Exception):
    """业务规则或事件流水错误。"""


class Ledger:
    def __init__(self) -> None:
        self._policies: dict[tuple[str, int], Policy] = {}
        self._applicants: dict[str, ApplicantState] = {}
        self._associations: dict[tuple[str, str], Association] = {}
        self._devices: dict[str, DeviceState] = {}
        self._fundings: dict[str, FundingState] = {}
        self._versions: dict[tuple[str, str], int] = {}
        self._events: list[dict] = []

    # ---------- 政策参考数据 ----------

    def register_policy(self, spec: dict) -> Policy:
        """登记政策版本。同一 (policy_id, version) 不得改写，保证重放确定。"""
        policy = Policy.from_dict(spec)
        key = (policy.policy_id, policy.version)
        if key in self._policies and self._policies[key] != policy:
            raise LedgerError(f"政策 {key} 已登记且内容不同，政策版本不可改写")
        self._policies[key] = policy
        return policy

    def policy(self, policy_id: str, version: int) -> Policy:
        try:
            return self._policies[(policy_id, version)]
        except KeyError:
            raise LedgerError(f"未登记的政策版本：{policy_id} v{version}") from None

    def find_policy(self, policy_id: str, version: int) -> Policy | None:
        return self._policies.get((policy_id, version))

    # ---------- 事件入账与重放 ----------

    def append(self, event: dict) -> dict:
        errors = validate_event(event)
        if errors:
            raise LedgerError("；".join(errors))
        key = (event["aggregate_type"], event["aggregate_id"])
        expected = self._versions.get(key, 0) + 1
        if event["version"] != expected:
            raise LedgerError(f"聚合 {key} 的版本应为 {expected}，实际为 {event['version']}")
        self._apply(event)
        self._versions[key] = expected
        self._events.append(event)
        return event

    def append_all(self, events: list[dict]) -> None:
        for event in events:
            self.append(event)

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def _apply(self, event: dict) -> None:
        handler = {
            "ELIGIBILITY_VERIFIED": self._apply_eligibility,
            "DEVICE_DELIVERED": self._apply_delivered,
            "SERVICE_CONFIRMED": self._apply_service,
            "SUBSIDY_RELEASED": self._apply_released,
            "FUNDS_REVERSED": self._apply_reversed,
        }[event["event_type"]]
        handler(event)

    def _apply_eligibility(self, event: dict) -> None:
        payload = event.get("payload") or {}
        ref = event["aggregate_id"]
        if payload.get("kind") == "identity_association":
            pair = tuple(sorted((ref, payload["with"])))
            self._associations[pair] = Association(
                pair=pair,
                status=payload["status"],
                evidence=payload.get("evidence", ""),
                by=payload.get("by", ""),
                at=parse_dt(event["occurred_at"]),
            )
            return
        applicant = self._applicants.setdefault(ref, ApplicantState(ref=ref))
        applicant.verifications.append(
            Verification(
                channel=payload["channel"],
                document_ref=payload["document_ref"],
                name=payload["name"],
                birth_date=parse_day(payload["birth_date"]),
                age=int(payload["age"]),
                care_level=payload["care_level"],
                low_income=bool(payload["low_income"]),
                region=payload["region"],
                policy_id=payload["policy_id"],
                policy_version=int(payload["policy_version"]),
                category=payload["category"],
                eligible=bool(payload["eligible"]),
                problems=tuple(payload.get("problems", ())),
                agent_refs=tuple(payload.get("agent_refs", ())),
                at=parse_dt(event["occurred_at"]),
            )
        )

    def _apply_delivered(self, event: dict) -> None:
        payload = event["payload"]
        serial = event["aggregate_id"]
        self._devices[serial] = DeviceState(
            serial_no=serial,
            category=payload["category"],
            contract_id=payload["contract_id"],
            contract_type=payload["contract_type"],
            price_after_discount=money(payload.get("price_after_discount", 0)),
            applicant_ref=payload["applicant_ref"],
            party=payload["party"],
            original_party=payload["party"],
            policy_id=payload["policy_id"],
            policy_version=int(payload["policy_version"]),
            delivered_at=parse_dt(event["occurred_at"]),
        )

    def _apply_service(self, event: dict) -> None:
        payload = event["payload"]
        device = self._devices[event["aggregate_id"]]
        kind = payload.get("kind", "service_period")
        at = parse_dt(event["occurred_at"])
        if kind == "service_period":
            period = payload["period"]
            device.service_periods.append((parse_dt(period["start"]), parse_dt(period["end"])))
        elif kind == "repair_start":
            device.repairs.append(Repair(start=at, arrangement=payload.get("arrangement", ""), note=payload.get("note", "")))
        elif kind == "repair_end":
            open_repairs = [r for r in device.repairs if r.end is None]
            if open_repairs:
                open_repairs[-1].end = at
        elif kind == "transferred":
            device.party_history.append((device.party, at))
            device.party = payload["party"]
        elif kind == "recall":
            device.recalls.append({"recall_id": payload.get("recall_id", ""), "at": at, "note": payload.get("note", "")})
        elif kind in ("returned", "deactivated"):
            device.stopped = {"kind": kind, "at": at, "recovered_by": payload.get("recovered_by", ""), "note": payload.get("note", "")}
        else:
            raise LedgerError(f"未知的设备服务事件 kind：{kind}")

    def _apply_released(self, event: dict) -> None:
        payload = event["payload"]
        period = payload.get("period")
        self._fundings[event["aggregate_id"]] = FundingState(
            funding_id=event["aggregate_id"],
            applicant_ref=payload["applicant_ref"],
            policy_id=payload["policy_id"],
            policy_version=int(payload["policy_version"]),
            serial_no=payload["serial_no"],
            contract_id=payload["contract_id"],
            trigger=payload["trigger"],
            amount=money(payload["amount"]),
            exclusion_group=payload.get("exclusion_group", ""),
            released_at=parse_dt(event["occurred_at"]),
            period=(parse_dt(period["start"]), parse_dt(period["end"])) if period else None,
        )

    def _apply_reversed(self, event: dict) -> None:
        payload = event["payload"]
        funding = self._fundings[event["aggregate_id"]]
        funding.reversals.append(
            Reversal(
                amount=money(payload["amount"]),
                reason=payload.get("reason", ""),
                at=parse_dt(event["occurred_at"]),
                note=payload.get("note", ""),
            )
        )

    # ---------- 查询 ----------

    def applicant(self, ref: str) -> ApplicantState | None:
        return self._applicants.get(ref)

    def device(self, serial_no: str) -> DeviceState | None:
        return self._devices.get(serial_no)

    def funding(self, funding_id: str) -> FundingState | None:
        return self._fundings.get(funding_id)

    @property
    def fundings(self) -> list[FundingState]:
        return list(self._fundings.values())

    @property
    def devices(self) -> list[DeviceState]:
        return list(self._devices.values())

    @property
    def associations(self) -> list[Association]:
        return list(self._associations.values())

    def cluster_refs(self, ref: str) -> set[str]:
        """已确认身份关联合并出的同人集合（待核对关联不参与合并）。"""
        seen = {ref}
        frontier = [ref]
        while frontier:
            current = frontier.pop()
            for assoc in self._associations.values():
                if assoc.status == "confirmed" and current in assoc.pair:
                    other = assoc.pair[0] if assoc.pair[1] == current else assoc.pair[1]
                    if other not in seen:
                        seen.add(other)
                        frontier.append(other)
        return seen

    def annual_usage(self, refs: set[str], policy_id: str, year: int):
        """同人集合在某政策某年度已占用的额度（发放净额，冲正释放额度）。"""
        total = ZERO
        for funding in self._fundings.values():
            if funding.applicant_ref in refs and funding.policy_id == policy_id and funding.released_at.year == year:
                total += funding.net
        return total

    # ---------- 命令 ----------

    def verify_eligibility(
        self,
        *,
        applicant_ref: str,
        channel: str,
        document_ref: str,
        name: str,
        birth_date: str,
        care_level: str,
        low_income: bool,
        region: str,
        policy_id: str,
        policy_version: int,
        category: str,
        at: str,
        agent_refs: tuple[str, ...] = (),
    ) -> dict:
        """资格确认：按政策版本核验地区、年龄、照护等级、低收入资格与认证品类。

        核验后自动排查跨渠道同人线索，只形成待核对关联——不按姓名去重，
        也不因此拒绝合法并用的其他政策。
        """
        policy = self.policy(policy_id, policy_version)
        birth = parse_day(birth_date)
        age = age_on(birth, parse_dt(at).date())
        problems = policy.eligibility_problems(
            region=region, age=age, care_level=care_level, low_income=low_income, category=category
        )
        event = self._emit(
            "ELIGIBILITY_VERIFIED",
            "applicant_eligibility",
            applicant_ref,
            at,
            f"资格确认 {applicant_ref} 申请 {policy_id} {category}：{'通过' if not problems else '未通过'}",
            {
                "kind": "eligibility",
                "channel": channel,
                "document_ref": document_ref,
                "name": name,
                "birth_date": birth_date,
                "age": age,
                "care_level": care_level,
                "low_income": low_income,
                "region": region,
                "policy_id": policy_id,
                "policy_version": policy_version,
                "category": category,
                "eligible": not problems,
                "problems": problems,
                "agent_refs": list(agent_refs),
            },
        )
        self._suggest_associations(applicant_ref, name, birth, document_ref, at)
        return event

    def _suggest_associations(self, ref: str, name: str, birth, document_ref: str, at: str) -> None:
        for other_ref, other in list(self._applicants.items()):
            if other_ref == ref:
                continue
            for v in other.verifications:
                if v.name == name and v.birth_date == birth and v.document_ref != document_ref:
                    self._ensure_pending_association(ref, other_ref, "姓名与出生日期一致，证件与申请渠道不同", at)
                    break
                if v.document_ref == document_ref:
                    self._ensure_pending_association(ref, other_ref, "证件引用一致但申请人标识不同", at)
                    break

    def _ensure_pending_association(self, a: str, b: str, evidence: str, at: str) -> None:
        pair = tuple(sorted((a, b)))
        if pair in self._associations:
            return
        self._emit(
            "ELIGIBILITY_VERIFIED",
            "applicant_eligibility",
            a,
            at,
            f"跨渠道身份待核对：{a} 与 {b}",
            {"kind": "identity_association", "with": b, "status": "pending", "evidence": evidence, "by": "system-auto"},
        )

    def confirm_association(self, ref: str, other: str, *, by: str, evidence: str, at: str) -> dict:
        """确认待核对关联。确认后同人额度合并、互斥资金才开始阻断。"""
        pair = tuple(sorted((ref, other)))
        assoc = self._associations.get(pair)
        if assoc is None or assoc.status != "pending":
            raise LedgerError(f"关联 {pair} 不存在或不处于待核对状态")
        return self._emit(
            "ELIGIBILITY_VERIFIED",
            "applicant_eligibility",
            ref,
            at,
            f"身份关联已确认：{ref} 与 {other}",
            {"kind": "identity_association", "with": other, "status": "confirmed", "evidence": evidence, "by": by},
        )

    def reject_association(self, ref: str, other: str, *, by: str, evidence: str, at: str) -> dict:
        pair = tuple(sorted((ref, other)))
        assoc = self._associations.get(pair)
        if assoc is None or assoc.status != "pending":
            raise LedgerError(f"关联 {pair} 不存在或不处于待核对状态")
        return self._emit(
            "ELIGIBILITY_VERIFIED",
            "applicant_eligibility",
            ref,
            at,
            f"身份关联已排除：{ref} 与 {other}",
            {"kind": "identity_association", "with": other, "status": "rejected", "evidence": evidence, "by": by},
        )

    def deliver_device(
        self,
        *,
        serial_no: str,
        category: str,
        contract_id: str,
        contract_type: str,
        price_after_discount,
        applicant_ref: str,
        party: str,
        policy_id: str,
        policy_version: int,
        at: str,
    ) -> list[dict]:
        """设备交付。购置类在交付时触发付款；租赁类交付不付款，待服务确认。"""
        policy = self.policy(policy_id, policy_version)
        rule = policy.categories.get(category)
        if rule is None:
            raise LedgerError(f"品类 {category} 不在政策 {policy_id} 的认证目录")
        if rule.mode != contract_type:
            raise LedgerError(f"合同类型 {contract_type} 与品类规则 {rule.mode} 不符")
        if serial_no in self._devices:
            raise LedgerError(f"设备序列号 {serial_no} 已交付")
        self._require_eligible(applicant_ref, policy_id, category)
        events = [
            self._emit(
                "DEVICE_DELIVERED",
                "device_service",
                serial_no,
                at,
                f"设备 {serial_no}（{category}）已交付 {party}",
                {
                    "category": category,
                    "contract_id": contract_id,
                    "contract_type": contract_type,
                    "price_after_discount": str(money(price_after_discount)),
                    "applicant_ref": applicant_ref,
                    "party": party,
                    "policy_id": policy_id,
                    "policy_version": policy_version,
                },
            )
        ]
        if rule.mode == "purchase":
            events.append(
                self._release(
                    applicant_ref=applicant_ref,
                    policy=policy,
                    serial_no=serial_no,
                    contract_id=contract_id,
                    trigger="delivery",
                    period=None,
                    raw_amount=rule.quote_purchase(price_after_discount),
                    at=at,
                )
            )
        return events

    def confirm_service(self, *, serial_no: str, start: str, end: str, at: str, note: str = "") -> list[dict]:
        """确认一段实际服务期。租赁类按目录租赁价触发该期付款。"""
        device = self._require_device(serial_no)
        start_dt, end_dt = parse_dt(start), parse_dt(end)
        if end_dt <= start_dt:
            raise LedgerError("服务期结束必须晚于开始")
        if device.stopped and start_dt >= device.stopped["at"]:
            raise LedgerError(f"设备 {serial_no} 已停用，不得确认停用后的服务")
        for s, e in device.service_periods:
            if start_dt < e and end_dt > s:
                raise LedgerError(f"服务期与已确认的 {s.isoformat()}~{e.isoformat()} 重叠")
        events = [
            self._emit(
                "SERVICE_CONFIRMED",
                "device_service",
                serial_no,
                at,
                f"设备 {serial_no} 服务期 {start}~{end} 已确认",
                {"kind": "service_period", "period": {"start": start, "end": end}, "note": note},
            )
        ]
        policy = self.policy(device.policy_id, device.policy_version)
        rule = policy.categories[device.category]
        if rule.mode == "rental":
            item_used = sum((f.net for f in self._fundings.values() if f.serial_no == serial_no), ZERO)
            item_remaining = rule.per_item_cap - item_used
            if item_remaining <= 0:
                raise LedgerError(f"设备 {serial_no} 已达单件上限 {rule.per_item_cap}")
            raw = min(rule.quote_rental(days_between(start_dt, end_dt)), item_remaining)
            events.append(
                self._release(
                    applicant_ref=device.applicant_ref,
                    policy=policy,
                    serial_no=serial_no,
                    contract_id=device.contract_id,
                    trigger="service_period",
                    period={"start": start, "end": end},
                    raw_amount=raw,
                    at=at,
                )
            )
        return events

    def record_repair_start(self, *, serial_no: str, at: str, arrangement: str, note: str = "") -> dict:
        self._require_device(serial_no)
        return self._emit(
            "SERVICE_CONFIRMED", "device_service", serial_no, at,
            f"设备 {serial_no} 开始维修：{arrangement}",
            {"kind": "repair_start", "arrangement": arrangement, "note": note},
        )

    def record_repair_end(self, *, serial_no: str, at: str, note: str = "") -> dict:
        self._require_device(serial_no)
        return self._emit(
            "SERVICE_CONFIRMED", "device_service", serial_no, at,
            f"设备 {serial_no} 维修结束",
            {"kind": "repair_end", "note": note},
        )

    def transfer_responsibility(self, *, serial_no: str, party: str, at: str, note: str = "") -> dict:
        """转移设备责任方。召回时以当前责任方为准，而非最初购买人。"""
        device = self._require_device(serial_no)
        if device.stopped:
            raise LedgerError(f"设备 {serial_no} 已停用，不得转移责任")
        return self._emit(
            "SERVICE_CONFIRMED", "device_service", serial_no, at,
            f"设备 {serial_no} 责任方由 {device.party} 转移为 {party}",
            {"kind": "transferred", "party": party, "note": note},
        )

    def record_recall(self, *, serial_no: str, recall_id: str, at: str, note: str = "") -> dict:
        self._require_device(serial_no)
        return self._emit(
            "SERVICE_CONFIRMED", "device_service", serial_no, at,
            f"设备 {serial_no} 召回 {recall_id}，当前责任方 {self._devices[serial_no].party}",
            {"kind": "recall", "recall_id": recall_id, "note": note},
        )

    def record_stop(self, *, serial_no: str, kind: str, at: str, recovered_by: str = "", note: str = "") -> dict:
        """退货回收或停用。只冲回未履行部分，见 settle_stop。"""
        if kind not in ("returned", "deactivated"):
            raise LedgerError("kind 只能是 returned（退货回收）或 deactivated（停用）")
        device = self._require_device(serial_no)
        if device.stopped:
            raise LedgerError(f"设备 {serial_no} 已停用")
        label = "退货回收" if kind == "returned" else "停用"
        return self._emit(
            "SERVICE_CONFIRMED", "device_service", serial_no, at,
            f"设备 {serial_no} {label}",
            {"kind": kind, "recovered_by": recovered_by, "note": note},
        )

    def settle_stop(self, *, serial_no: str, reason: str, at: str, note: str = "") -> list[dict]:
        """停用结算：对该设备每笔资金只冲回未履行部分，已履行部分保留。"""
        device = self._require_device(serial_no)
        if not device.stopped:
            raise LedgerError(f"设备 {serial_no} 未停用，无需冲正")
        stop_at = device.stopped["at"]
        events = []
        for funding in self._fundings.values():
            if funding.serial_no != serial_no:
                continue
            additional = reversal_due(funding, stop_at) - funding.reversed_total
            if additional > 0:
                events.append(
                    self._emit(
                        "FUNDS_REVERSED",
                        "funding_entry",
                        funding.funding_id,
                        at,
                        f"设备 {serial_no} 停用，冲回未履行部分 {additional}",
                        {
                            "amount": str(additional),
                            "reason": reason,
                            "serial_no": serial_no,
                            "applicant_ref": funding.applicant_ref,
                            "note": note,
                        },
                    )
                )
        return events

    def reverse_funds(self, *, funding_id: str, amount, reason: str, at: str, note: str = "") -> dict:
        funding = self._fundings.get(funding_id)
        if funding is None:
            raise LedgerError(f"资金流水 {funding_id} 不存在")
        amount = money(amount)
        if amount <= 0 or amount > funding.net:
            raise LedgerError(f"冲正金额 {amount} 超出可冲回余额 {funding.net}")
        return self._emit(
            "FUNDS_REVERSED",
            "funding_entry",
            funding_id,
            at,
            f"资金 {funding_id} 冲正 {amount}：{reason}",
            {
                "amount": str(amount),
                "reason": reason,
                "serial_no": funding.serial_no,
                "applicant_ref": funding.applicant_ref,
                "note": note,
            },
        )

    # ---------- 命令内部规则 ----------

    def _emit(self, event_type: str, aggregate_type: str, aggregate_id: str, at: str, summary: str, payload: dict) -> dict:
        version = self._versions.get((aggregate_type, aggregate_id), 0) + 1
        return self.append(
            {
                "event_id": f"{aggregate_id}-v{version}",
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "occurred_at": at,
                "version": version,
                "summary": summary,
                "payload": payload,
            }
        )

    def _require_device(self, serial_no: str) -> DeviceState:
        device = self._devices.get(serial_no)
        if device is None:
            raise LedgerError(f"设备 {serial_no} 不存在")
        return device

    def _require_eligible(self, applicant_ref: str, policy_id: str, category: str) -> None:
        applicant = self._applicants.get(applicant_ref)
        if applicant is None:
            raise LedgerError(f"申请人 {applicant_ref} 未完成资格确认")
        for v in applicant.verifications:
            if v.policy_id == policy_id and v.category == category and v.eligible:
                return
        raise LedgerError(f"申请人 {applicant_ref} 在政策 {policy_id} 下无有效的 {category} 资格确认")

    def _release(self, *, applicant_ref, policy, serial_no, contract_id, trigger, period, raw_amount, at) -> dict:
        refs = self.cluster_refs(applicant_ref)
        for other in self._fundings.values():
            if (
                other.applicant_ref in refs
                and other.exclusion_group == policy.exclusion_group
                and other.policy_id != policy.policy_id
                and other.net > 0
            ):
                raise LedgerError(
                    f"互斥资金阻断：{other.funding_id}（{other.policy_id}）与 {policy.policy_id} "
                    f"同属 {policy.exclusion_group} 组且身份关联已确认"
                )
        year = parse_dt(at).year
        remaining = policy.annual_cap - self.annual_usage(refs, policy.policy_id, year)
        if remaining <= 0:
            raise LedgerError(f"政策 {policy.policy_id} {year} 年度上限 {policy.annual_cap} 已用完")
        amount = min(money(raw_amount), remaining)
        seq = 1
        while f"F-{seq:04d}" in self._fundings:
            seq += 1
        return self._emit(
            "SUBSIDY_RELEASED",
            "funding_entry",
            f"F-{seq:04d}",
            at,
            f"补贴发放 {amount}（{policy.policy_id}，{trigger}）",
            {
                "applicant_ref": applicant_ref,
                "policy_id": policy.policy_id,
                "policy_version": policy.version,
                "serial_no": serial_no,
                "contract_id": contract_id,
                "trigger": trigger,
                "period": period,
                "amount": str(amount),
                "exclusion_group": policy.exclusion_group,
            },
        )
