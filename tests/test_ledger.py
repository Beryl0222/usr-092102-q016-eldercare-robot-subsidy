import unittest
from datetime import date

from src.ledger import FundingLedger, LedgerError
from src.model import PolicyRule, SupportKind


def lease_policy(annual_cap: int = 10_000_00) -> PolicyRule:
    return PolicyRule(
        policy_code="LEASE-CARE", version="2026", region="310100",
        kind=SupportKind.LEASE, effective_from=date(2026, 1, 1), effective_to=None,
        min_age=70, lease_catalog_daily_cap=3000, annual_cap=annual_cap,
    )


class LedgerTest(unittest.TestCase):
    def test_annual_cap_blocks_over_release(self) -> None:
        ledger = FundingLedger()
        policy = lease_policy(annual_cap=1_000_00)
        ledger.release(entry_id="F1", eligibility_id="E1", device_service_id="D1",
                       policy=policy, amount=8_00_00, trigger="delivery",
                       days_paid=None, event_id="2026-e1")
        with self.assertRaises(LedgerError):
            ledger.release(entry_id="F2", eligibility_id="E1", device_service_id="D2",
                           policy=policy, amount=3_00_00, trigger="delivery",
                           days_paid=None, event_id="2026-e2")
        self.assertEqual(ledger.remaining_annual("E1", policy), 2_00_00)

    def test_reversal_restores_annual_room(self) -> None:
        ledger = FundingLedger()
        policy = lease_policy(annual_cap=10_000_00)
        ledger.release(entry_id="F1", eligibility_id="E1", device_service_id="D1",
                       policy=policy, amount=9_000_00, trigger="service",
                       days_paid=300, event_id="2026-e1")
        ledger.reverse(entry_id="F1", amount=3_000_00, reason="停用",
                       days_paid=300, days_fulfilled=200, event_id="2026-e2")
        self.assertEqual(ledger.remaining_annual("E1", policy), 4_000_00)

    def test_cannot_reverse_more_than_unfulfilled_part(self) -> None:
        ledger = FundingLedger()
        policy = lease_policy()
        ledger.release(entry_id="F1", eligibility_id="E1", device_service_id="D1",
                       policy=policy, amount=9_000_00, trigger="service",
                       days_paid=300, event_id="2026-e1")
        # 未履行 100 天对应 3000 元，试图冲回 5000 元必须被拒
        with self.assertRaises(LedgerError):
            ledger.reverse(entry_id="F1", amount=5_000_00, reason="停用",
                           days_paid=300, days_fulfilled=200, event_id="2026-e2")

    def test_delivery_and_service_are_the_only_release_triggers(self) -> None:
        ledger = FundingLedger()
        with self.assertRaises(LedgerError):
            ledger.release(entry_id="F1", eligibility_id="E1", device_service_id="D1",
                           policy=lease_policy(), amount=100, trigger="application",
                           days_paid=None, event_id="2026-e1")


if __name__ == "__main__":
    unittest.main()
