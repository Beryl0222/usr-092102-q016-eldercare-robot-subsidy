import unittest
from datetime import date

from src.model import (
    LeaseQuote,
    PolicyRule,
    PurchaseQuote,
    SupportKind,
    calc_subsidy,
    care_level_at_least,
    prorate_reversal,
)


class RuleTest(unittest.TestCase):
    def test_purchase_subsidy_uses_discounted_price_and_item_cap(self) -> None:
        policy = PolicyRule(
            policy_code="PUR-EXO", version="2026", region="310100",
            kind=SupportKind.PURCHASE, effective_from=date(2026, 1, 1), effective_to=None,
            min_age=60, purchase_subsidy_bp=3000, per_item_cap=3_000_000, annual_cap=3_000_000,
            certified_categories=frozenset({"EXO_A"}),
        )
        # 优惠后价 20000 元 × 30% = 6000 元
        self.assertEqual(calc_subsidy(policy, PurchaseQuote(discounted_price=20_000_00)), 6_000_00)
        # 单件上限裁剪
        self.assertEqual(
            calc_subsidy(PolicyRule(**{**policy.__dict__, "per_item_cap": 2_000_00}),
                         PurchaseQuote(discounted_price=20_000_00)),
            2_000_00,
        )

    def test_lease_uses_catalog_daily_rate_and_days(self) -> None:
        policy = PolicyRule(
            policy_code="LEASE-CARE", version="2026", region="310100",
            kind=SupportKind.LEASE, effective_from=date(2026, 1, 1), effective_to=None,
            min_age=70, min_care_level="moderate",
            lease_catalog_daily_cap=3000, annual_cap=12_000_00,
            certified_categories=frozenset({"CARE_ROBOT"}),
        )
        # 合同日租 50 元超过目录价 30 元，按 30 元 × 73 天
        self.assertEqual(calc_subsidy(policy, LeaseQuote(catalog_daily_rate=5000, days=73)), 219_000)

    def test_eligibility_checks_age_care_and_low_income(self) -> None:
        policy = PolicyRule(
            policy_code="X", version="v", region="r", kind=SupportKind.LEASE,
            effective_from=date(2026, 1, 1), effective_to=None,
            min_age=70, min_care_level="moderate", low_income_required=True,
        )
        self.assertEqual(policy.check_eligibility(age=78, care_level="severe", low_income=True), [])
        problems = policy.check_eligibility(age=65, care_level="mild", low_income=False)
        self.assertEqual(len(problems), 3)

    def test_proration_only_reverses_unfulfilled_part(self) -> None:
        # 全年 10950 元，实服 73/365 天，冲回 292 天 = 8760 元
        self.assertEqual(prorate_reversal(10_950_00, days_paid=365, days_fulfilled=73), 8_760_00)
        # 履行天数超出已付天数时不产生负数冲正
        self.assertEqual(prorate_reversal(1_000_00, days_paid=30, days_fulfilled=31), 0)

    def test_care_level_ordering(self) -> None:
        self.assertTrue(care_level_at_least("severe", "moderate"))
        self.assertFalse(care_level_at_least("mild", "moderate"))


if __name__ == "__main__":
    unittest.main()
