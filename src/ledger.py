"""资金台账投影：发放、年度额度、冲正与支出追溯。"""

from dataclasses import dataclass, field

from src.model import PolicyRule, prorate_reversal


@dataclass
class FundingEntry:
    entry_id: str
    eligibility_id: str
    device_service_id: str
    policy: PolicyRule
    releases: list[dict] = field(default_factory=list)
    reversals: list[dict] = field(default_factory=list)

    @property
    def released_total(self) -> int:
        return sum(r["amount"] for r in self.releases)

    @property
    def reversed_total(self) -> int:
        return sum(r["amount"] for r in self.reversals)

    @property
    def net_paid(self) -> int:
        return self.released_total - self.reversed_total


class LedgerError(ValueError):
    pass


class FundingLedger:
    def __init__(self) -> None:
        self._entries: dict[str, FundingEntry] = {}
        # (eligibility_id, 年度) -> 已占用净额
        self._annual_used: dict[tuple[str, int], int] = {}

    def release(self, *, entry_id: str, eligibility_id: str, device_service_id: str,
                policy: PolicyRule, amount: int, trigger: str,
                days_paid: int | None, event_id: str) -> FundingEntry:
        if amount <= 0:
            raise LedgerError(f"发放金额必须为正：{event_id}")
        if trigger not in ("delivery", "service"):
            raise LedgerError("发放触发只能是 delivery（交付）或 service（持续服务）")

        entry = self._entries.get(entry_id)
        if entry is None:
            entry = FundingEntry(entry_id, eligibility_id, device_service_id, policy)
            self._entries[entry_id] = entry

        key = (eligibility_id, policy_year_key(policy))
        used = self._annual_used.get(key, 0)
        remaining = max(policy.annual_cap - used, 0) if policy.annual_cap else amount
        if policy.annual_cap and amount > remaining:
            # 超年度上限部分不得发放；不静默裁剪，直接拒绝以便上游更正事件
            raise LedgerError(
                f"发放 {amount} 分超过 {eligibility_id} 年度剩余额度 {remaining} 分"
            )

        entry.releases.append({
            "amount": amount, "trigger": trigger,
            "days_paid": days_paid, "event_id": event_id,
        })
        self._annual_used[key] = used + amount
        return entry

    def reverse(self, *, entry_id: str, amount: int, reason: str,
                days_paid: int | None, days_fulfilled: int | None, event_id: str) -> None:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise LedgerError(f"冲正找不到原始支出：{entry_id}")
        if amount <= 0:
            raise LedgerError("冲正金额必须为正")
        if amount > entry.net_paid:
            raise LedgerError(
                f"冲正 {amount} 分超过该笔净发放 {entry.net_paid} 分"
            )
        # 停用/退货按实际服务期校验：只冲回未履行部分
        if days_paid is not None and days_fulfilled is not None:
            expected = prorate_reversal(entry.released_total, days_paid, days_fulfilled)
            if amount > expected:
                raise LedgerError(
                    f"冲正 {amount} 分超过未履行部分应冲 {expected} 分"
                    f"（已付 {days_paid} 天，实际履行 {days_fulfilled} 天）"
                )
        entry.reversals.append({
            "amount": amount, "reason": reason,
            "days_paid": days_paid, "days_fulfilled": days_fulfilled,
            "event_id": event_id,
        })
        key = (entry.eligibility_id, policy_year_key(entry.policy))
        self._annual_used[key] -= amount

    def remaining_annual(self, eligibility_id: str, policy: PolicyRule) -> int:
        used = self._annual_used.get((eligibility_id, policy_year_key(policy)), 0)
        return max(policy.annual_cap - used, 0) if policy.annual_cap else -1  # -1 表示无上限

    def entries_for(self, eligibility_id: str) -> list[FundingEntry]:
        return [e for e in self._entries.values() if e.eligibility_id == eligibility_id]

    def trace(self, entry_id: str) -> dict:
        """财政追溯：一笔支出 -> 合同设备 -> 序列号（序列号由调用方关联设备投影）。"""
        e = self._entries[entry_id]
        return {
            "entry_id": e.entry_id,
            "eligibility_id": e.eligibility_id,
            "device_service_id": e.device_service_id,
            "policy": f"{e.policy.policy_code}@{e.policy.version}/{e.policy.region}",
            "released": e.released_total,
            "reversed": e.reversed_total,
            "net_paid": e.net_paid,
            "releases": e.releases,
            "reversals": e.reversals,
        }

    def all_entries(self) -> list[FundingEntry]:
        return list(self._entries.values())


def policy_year_key(policy: PolicyRule) -> str:
    return f"{policy.policy_code}@{policy.version}"
