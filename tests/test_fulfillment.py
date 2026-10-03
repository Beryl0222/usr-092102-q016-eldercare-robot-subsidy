import unittest

from src.audit import reconciliation_report
from src.ledger import Ledger, LedgerError
from src.model import money
from src.views import AccessDenied, applicant_portal, recall_responsibilities

EXO_POLICY = {
    "policy_id": "P-EXO-2026",
    "version": 1,
    "region": "滨江市",
    "min_age": 65,
    "care_levels": ["中度", "重度"],
    "low_income_required": False,
    "annual_cap": 20000,
    "exclusion_group": "mobility_device",
    "categories": {"exoskeleton": {"mode": "purchase", "purchase_ratio": 0.5, "per_item_cap": 15000}},
}
CARE_POLICY = {
    "policy_id": "P-CARE-2026",
    "version": 1,
    "region": "滨江市",
    "min_age": 60,
    "care_levels": ["轻度", "中度", "重度"],
    "low_income_required": True,
    "annual_cap": 12000,
    "exclusion_group": "care_service",
    "categories": {
        "care_robot": {"mode": "rental", "catalog_period_price": 800, "period_days": 30, "per_item_cap": 9600}
    },
}
CHAIR_POLICY = {
    "policy_id": "P-CHAIR-2026",
    "version": 1,
    "region": "滨江市",
    "min_age": 65,
    "care_levels": ["中度", "重度"],
    "low_income_required": False,
    "annual_cap": 5000,
    "exclusion_group": "mobility_device",
    "categories": {"wheelchair": {"mode": "purchase", "purchase_ratio": 0.5, "per_item_cap": 3000}},
}

T0 = "2026-01-05T09:00:00+08:00"


def make_ledger() -> Ledger:
    ledger = Ledger()
    ledger.register_policy(EXO_POLICY)
    ledger.register_policy(CARE_POLICY)
    ledger.register_policy(CHAIR_POLICY)
    return ledger


def verify(ledger: Ledger, ref: str, *, policy_id="P-EXO-2026", category="exoskeleton", at=T0, **overrides):
    params = {
        "applicant_ref": ref,
        "channel": "ecommerce",
        "document_ref": f"doc-{ref}",
        "name": "王秀兰",
        "birth_date": "1954-03-12",
        "care_level": "中度",
        "low_income": True,
        "region": "滨江市",
        "policy_id": policy_id,
        "policy_version": 1,
        "category": category,
        "at": at,
    }
    params.update(overrides)
    return ledger.verify_eligibility(**params)


def deliver_exo(ledger: Ledger, ref: str, serial: str, price=24000, at="2026-01-08T10:00:00+08:00"):
    return ledger.deliver_device(
        serial_no=serial,
        category="exoskeleton",
        contract_id=f"CT-{serial}",
        contract_type="purchase",
        price_after_discount=price,
        applicant_ref=ref,
        party=ref,
        policy_id="P-EXO-2026",
        policy_version=1,
        at=at,
    )


def deliver_care_robot(ledger: Ledger, ref: str, serial: str, at="2026-01-12T09:00:00+08:00"):
    return ledger.deliver_device(
        serial_no=serial,
        category="care_robot",
        contract_id=f"CT-{serial}",
        contract_type="rental",
        price_after_discount=0,
        applicant_ref=ref,
        party=ref,
        policy_id="P-CARE-2026",
        policy_version=1,
        at=at,
    )


class EligibilityTest(unittest.TestCase):
    def test_eligibility_rules(self):
        ledger = make_ledger()
        event = verify(ledger, "A-1")
        self.assertTrue(event["payload"]["eligible"])
        self.assertEqual(event["payload"]["age"], 71)

        too_young = verify(ledger, "A-2", birth_date="1970-01-01")
        self.assertFalse(too_young["payload"]["eligible"])
        self.assertIn("年龄 56 岁未达 65 岁门槛", too_young["payload"]["problems"])

        wrong_care = verify(ledger, "A-3", care_level="轻度")
        self.assertFalse(wrong_care["payload"]["eligible"])

        not_low_income = verify(ledger, "A-4", policy_id="P-CARE-2026", category="care_robot", low_income=False)
        self.assertFalse(not_low_income["payload"]["eligible"])
        self.assertIn("该政策要求低收入资格", not_low_income["payload"]["problems"])

        wrong_region = verify(ledger, "A-5", region="邻市")
        self.assertFalse(wrong_region["payload"]["eligible"])

        uncertified = verify(ledger, "A-6", category="massage_chair")
        self.assertFalse(uncertified["payload"]["eligible"])
        self.assertIn("不在认证目录", uncertified["payload"]["problems"][0])

    def test_delivery_requires_eligible_verification(self):
        ledger = make_ledger()
        with self.assertRaises(LedgerError):
            deliver_exo(ledger, "A-9", "EXO-1")
        verify(ledger, "A-9", care_level="轻度")  # 未通过
        with self.assertRaises(LedgerError):
            deliver_exo(ledger, "A-9", "EXO-1")

    def test_policy_version_immutable(self):
        ledger = make_ledger()
        changed = dict(EXO_POLICY, annual_cap=99999)
        with self.assertRaises(LedgerError):
            ledger.register_policy(changed)
        ledger.register_policy(EXO_POLICY)  # 相同内容重复登记允许


class SubsidyCalculationTest(unittest.TestCase):
    def test_purchase_ratio_and_item_cap(self):
        ledger = make_ledger()
        verify(ledger, "A-1")
        events = deliver_exo(ledger, "A-1", "EXO-1", price=24000)
        self.assertEqual(events[-1]["payload"]["amount"], "12000.00")

        verify(ledger, "A-2")
        events = deliver_exo(ledger, "A-2", "EXO-2", price=40000)
        self.assertEqual(events[-1]["payload"]["amount"], "15000.00")  # 单件上限

    def test_annual_cap_limits_remaining_quota(self):
        ledger = make_ledger()
        verify(ledger, "A-1")
        first = deliver_exo(ledger, "A-1", "EXO-1", price=24000)
        self.assertEqual(first[-1]["payload"]["amount"], "12000.00")
        second = deliver_exo(ledger, "A-1", "EXO-2", price=24000, at="2026-03-01T10:00:00+08:00")
        self.assertEqual(second[-1]["payload"]["amount"], "8000.00")  # 年度上限 20000 剩余 8000
        with self.assertRaises(LedgerError):
            deliver_exo(ledger, "A-1", "EXO-3", price=24000, at="2026-04-01T10:00:00+08:00")

    def test_rental_payment_follows_service_not_delivery(self):
        ledger = make_ledger()
        verify(ledger, "A-1", policy_id="P-CARE-2026", category="care_robot")
        events = deliver_care_robot(ledger, "A-1", "CR-1")
        self.assertEqual([e["event_type"] for e in events], ["DEVICE_DELIVERED"])  # 交付不触发租赁付款

        events = ledger.confirm_service(
            serial_no="CR-1",
            start="2026-01-12T00:00:00+08:00",
            end="2026-02-11T00:00:00+08:00",
            at="2026-02-11T18:00:00+08:00",
        )
        release = events[-1]
        self.assertEqual(release["event_type"], "SUBSIDY_RELEASED")
        self.assertEqual(release["payload"]["trigger"], "service_period")
        self.assertEqual(release["payload"]["amount"], "800.00")  # 目录租赁价 800/30 天

        events = ledger.confirm_service(
            serial_no="CR-1",
            start="2026-02-11T00:00:00+08:00",
            end="2026-02-26T00:00:00+08:00",
            at="2026-02-26T18:00:00+08:00",
        )
        self.assertEqual(events[-1]["payload"]["amount"], "400.00")  # 15 天按天折算

    def test_service_period_overlap_rejected(self):
        ledger = make_ledger()
        verify(ledger, "A-1", policy_id="P-CARE-2026", category="care_robot")
        deliver_care_robot(ledger, "A-1", "CR-1")
        ledger.confirm_service(
            serial_no="CR-1",
            start="2026-01-12T00:00:00+08:00",
            end="2026-02-11T00:00:00+08:00",
            at="2026-02-11T18:00:00+08:00",
        )
        with self.assertRaises(LedgerError):
            ledger.confirm_service(
                serial_no="CR-1",
                start="2026-02-01T00:00:00+08:00",
                end="2026-03-01T00:00:00+08:00",
                at="2026-03-01T18:00:00+08:00",
            )


class ReversalTest(unittest.TestCase):
    def _rental_with_upfront_settlement(self):
        """租赁设备按全年一次性结算（外部系统写入），随后退货。"""
        ledger = make_ledger()
        verify(ledger, "A-1", policy_id="P-CARE-2026", category="care_robot")
        deliver_care_robot(ledger, "A-1", "CR-1")
        ledger.append(
            {
                "event_id": "ext-1",
                "event_type": "SUBSIDY_RELEASED",
                "aggregate_type": "funding_entry",
                "aggregate_id": "F-EXT-1",
                "occurred_at": "2026-01-12T09:05:00+08:00",
                "version": 1,
                "summary": "外部系统按全年服务结算 9600",
                "payload": {
                    "applicant_ref": "A-1",
                    "policy_id": "P-CARE-2026",
                    "policy_version": 1,
                    "serial_no": "CR-1",
                    "contract_id": "CT-CR-1",
                    "trigger": "service_period",
                    "period": {"start": "2026-01-12T00:00:00+08:00", "end": "2027-01-12T00:00:00+08:00"},
                    "amount": 9600,
                    "exclusion_group": "care_service",
                },
            }
        )
        return ledger

    def test_deactivation_reverses_only_unfulfilled_part(self):
        ledger = self._rental_with_upfront_settlement()
        ledger.record_stop(serial_no="CR-1", kind="returned", at="2026-05-01T00:00:00+08:00", recovered_by="厂商")
        events = ledger.settle_stop(serial_no="CR-1", reason="return_unfulfilled", at="2026-05-03T10:00:00+08:00")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "FUNDS_REVERSED")
        self.assertEqual(events[0]["payload"]["amount"], "6733.15")  # 未履行 256/365 天
        funding = ledger.funding("F-EXT-1")
        self.assertEqual(funding.net, money("2866.85"))  # 已履行 109 天保留
        self.assertEqual(ledger.settle_stop(serial_no="CR-1", reason="return_unfulfilled", at="2026-05-04T10:00:00+08:00"), [])

    def test_purchase_return_reverses_full_amount(self):
        ledger = make_ledger()
        verify(ledger, "A-1")
        deliver_exo(ledger, "A-1", "EXO-1", price=24000)
        ledger.record_stop(serial_no="EXO-1", kind="returned", at="2026-02-01T00:00:00+08:00", recovered_by="厂商")
        events = ledger.settle_stop(serial_no="EXO-1", reason="return_unfulfilled", at="2026-02-02T10:00:00+08:00")
        self.assertEqual(events[0]["payload"]["amount"], "12000.00")
        self.assertEqual(ledger.funding("F-0001").net, money("0.00"))

    def test_service_after_stop_rejected(self):
        ledger = self._rental_with_upfront_settlement()
        ledger.record_stop(serial_no="CR-1", kind="returned", at="2026-05-01T00:00:00+08:00")
        with self.assertRaises(LedgerError):
            ledger.confirm_service(
                serial_no="CR-1",
                start="2026-05-02T00:00:00+08:00",
                end="2026-06-01T00:00:00+08:00",
                at="2026-06-01T18:00:00+08:00",
            )


class IdentityAndExclusionTest(unittest.TestCase):
    def _two_channel_person(self):
        ledger = make_ledger()
        verify(ledger, "A-EC", document_ref="doc-1")
        verify(
            ledger,
            "A-COM",
            channel="community",
            document_ref="doc-2",
            policy_id="P-CHAIR-2026",
            category="wheelchair",
            at="2026-01-10T09:00:00+08:00",
        )
        return ledger

    def test_cross_channel_identity_forms_pending_association_only(self):
        ledger = self._two_channel_person()
        associations = ledger.associations
        self.assertEqual(len(associations), 1)
        self.assertEqual(associations[0].status, "pending")
        self.assertEqual(ledger.cluster_refs("A-EC"), {"A-EC"})  # 待核对不合并

    def test_same_name_alone_does_not_associate(self):
        ledger = make_ledger()
        verify(ledger, "A-1", birth_date="1954-03-12")
        verify(ledger, "A-2", birth_date="1960-08-01", document_ref="doc-x")  # 同名不同出生日期
        self.assertEqual(ledger.associations, [])

    def test_mutual_exclusion_blocks_only_after_confirmation(self):
        ledger = self._two_channel_person()
        deliver_exo(ledger, "A-EC", "EXO-1", price=24000)  # mobility_device 组净额 12000
        # 待核对阶段：不阻断另一政策同组资金
        ledger.deliver_device(
            serial_no="WC-1",
            category="wheelchair",
            contract_id="CT-WC-1",
            contract_type="purchase",
            price_after_discount=4000,
            applicant_ref="A-COM",
            party="A-COM",
            policy_id="P-CHAIR-2026",
            policy_version=1,
            at="2026-01-11T10:00:00+08:00",
        )
        report = reconciliation_report(ledger)
        self.assertEqual(report["identity"]["pending_conflicts"][0]["exclusion_group"], "mobility_device")
        # 确认身份后：同组不同政策的新发放被阻断
        ledger.confirm_association("A-EC", "A-COM", by="民政局", evidence="户籍一致", at="2026-01-20T09:00:00+08:00")
        self.assertEqual(ledger.cluster_refs("A-EC"), {"A-EC", "A-COM"})
        with self.assertRaises(LedgerError):
            ledger.deliver_device(
                serial_no="WC-2",
                category="wheelchair",
                contract_id="CT-WC-2",
                contract_type="purchase",
                price_after_discount=4000,
                applicant_ref="A-COM",
                party="A-COM",
                policy_id="P-CHAIR-2026",
                policy_version=1,
                at="2026-01-21T10:00:00+08:00",
            )
        violations = reconciliation_report(ledger)["exclusion_violations"]
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["exclusion_group"], "mobility_device")

    def test_legitimate_combination_not_rejected(self):
        """不同互斥组的政策允许合法并用。"""
        ledger = make_ledger()
        verify(ledger, "A-EC", document_ref="doc-1")
        verify(
            ledger,
            "A-COM",
            channel="community",
            document_ref="doc-2",
            policy_id="P-CARE-2026",
            category="care_robot",
            at="2026-01-10T09:00:00+08:00",
        )
        deliver_exo(ledger, "A-EC", "EXO-1", price=24000)
        deliver_care_robot(ledger, "A-COM", "CR-1")
        ledger.confirm_association("A-EC", "A-COM", by="民政局", evidence="户籍一致", at="2026-01-20T09:00:00+08:00")
        # 确认后分属 mobility_device 与 care_service 两组，互不影响
        events = ledger.confirm_service(
            serial_no="CR-1",
            start="2026-01-12T00:00:00+08:00",
            end="2026-02-11T00:00:00+08:00",
            at="2026-02-11T18:00:00+08:00",
        )
        self.assertEqual(events[-1]["payload"]["amount"], "800.00")
        self.assertEqual(reconciliation_report(ledger)["exclusion_violations"], [])


class RecallAndAccessTest(unittest.TestCase):
    def test_recall_finds_current_party_not_original_buyer(self):
        ledger = make_ledger()
        verify(ledger, "A-1")
        deliver_exo(ledger, "A-1", "EXO-1")
        ledger.transfer_responsibility(
            serial_no="EXO-1", party="滨江社区康复站", at="2026-04-15T11:00:00+08:00", note="白天在康复站训练"
        )
        ledger.record_recall(serial_no="EXO-1", recall_id="RC-1", at="2026-05-10T09:00:00+08:00")
        rows = recall_responsibilities(ledger)
        self.assertEqual(rows[0]["current_party"], "滨江社区康复站")
        self.assertEqual(rows[0]["original_party"], "A-1")

    def test_applicant_portal_access(self):
        ledger = make_ledger()
        verify(ledger, "A-1", agent_refs=("A-SON",))
        deliver_exo(ledger, "A-1", "EXO-1")
        with self.assertRaises(AccessDenied):
            applicant_portal(ledger, "A-1", requester="stranger")
        portal = applicant_portal(ledger, "A-1", requester="A-SON")
        self.assertEqual(portal["quotas"][0]["remaining"], money("8000.00"))

    def test_replay_determinism(self):
        ledger = make_ledger()
        verify(ledger, "A-1")
        deliver_exo(ledger, "A-1", "EXO-1")
        ledger.transfer_responsibility(serial_no="EXO-1", party="康复站", at="2026-04-15T11:00:00+08:00")
        replayed = make_ledger()
        replayed.append_all(ledger.events)
        self.assertEqual(
            applicant_portal(ledger, "A-1")["quotas"],
            applicant_portal(replayed, "A-1")["quotas"],
        )
        self.assertEqual(reconciliation_report(ledger), reconciliation_report(replayed))


if __name__ == "__main__":
    unittest.main()
