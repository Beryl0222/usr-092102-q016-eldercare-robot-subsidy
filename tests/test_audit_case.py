import json
import unittest
from pathlib import Path

from src.audit import reconciliation_report
from src.ledger import Ledger
from src.model import money
from src.validator import validate_event
from src.views import (
    AccessDenied,
    applicant_portal,
    community_portal,
    finance_trace,
    recall_responsibilities,
    vendor_portal,
)

CASE = json.loads((Path(__file__).parents[1] / "data" / "audit_case.json").read_text(encoding="utf-8"))


def load_case() -> Ledger:
    ledger = Ledger()
    for policy in CASE["policies"]:
        ledger.register_policy(policy)
    ledger.append_all(CASE["events"])
    return ledger


class AuditCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = load_case()
        cls.report = reconciliation_report(cls.ledger)

    def row(self, funding_id):
        return next(r for r in self.report["funding_entries"] if r["funding_id"] == funding_id)

    def test_case_events_match_contract(self):
        for event in CASE["events"]:
            self.assertEqual(validate_event(event), [], event["event_id"])

    def test_returned_rental_unfulfilled_part_reversed(self):
        """已退货的看护机器人：全年 9600 只保留已履行 109 天，冲回 256 天。"""
        row = self.row("F-0002")
        self.assertEqual(row["settled_days"], 365)
        self.assertEqual(row["confirmed_days"], 89)  # 三期服务确认
        self.assertEqual(row["unconfirmed_elapsed_days"], 20)  # 04-11~05-01 待社区补确认
        self.assertEqual(row["unconfirmed_elapsed_amount"], money("526.03"))
        self.assertEqual(row["post_stop_days"], 256)
        self.assertEqual(row["post_stop_reversal_due"], money("6733.15"))
        self.assertEqual(row["post_stop_reversal_outstanding"], money("0.00"))  # 已冲正
        self.assertEqual(row["net"], money("2866.85"))

    def test_purchase_subsidy_unaffected(self):
        """外骨骼未退货，购置补贴 12000 保留。"""
        row = self.row("F-0001")
        self.assertEqual(row["amount"], money("12000.00"))
        self.assertEqual(row["net"], money("12000.00"))
        self.assertNotIn("post_stop_reversal_due", row)

    def test_identity_association_confirmed_but_policies_combined_legally(self):
        """两套证件已确认为同一人，但两项政策分属不同互斥组，合法并用。"""
        identity = self.report["identity"]
        self.assertEqual(identity["pending"], [])
        self.assertEqual(identity["confirmed"][0]["pair"], ["A-WANG-COM", "A-WANG-EC"])
        self.assertEqual(self.report["exclusion_violations"], [])
        self.assertEqual(self.report["cap_overruns"], [])
        self.assertEqual(self.ledger.cluster_refs("A-WANG-COM"), {"A-WANG-EC", "A-WANG-COM"})

    def test_remaining_quota_after_reversal(self):
        portal = applicant_portal(self.ledger, "A-WANG-EC", requester="A-WANG-SON")
        quotas = {q["policy_id"]: q for q in portal["quotas"]}
        self.assertEqual(quotas["P-EXO-2026"]["remaining"], money("8000.00"))
        self.assertEqual(quotas["P-CARE-2026"]["remaining"], money("9133.15"))
        with self.assertRaises(AccessDenied):
            applicant_portal(self.ledger, "A-WANG-EC", requester="A-STRANGER")

    def test_malfunction_arrangement_visible_to_elder(self):
        portal = applicant_portal(self.ledger, "A-WANG-COM")
        arrangements = portal["malfunction_arrangements"]
        self.assertEqual(len(arrangements), 1)
        self.assertEqual(arrangements[0]["serial_no"], "CR-2026-0009")
        self.assertIn("备用机", arrangements[0]["arrangement"])
        self.assertEqual(arrangements[0]["repair_end"], "2026-03-22T09:00:00+08:00")

    def test_finance_traces_expenditure_to_actual_usage(self):
        trace = finance_trace(self.ledger, "F-0002")
        self.assertEqual(trace["funding"]["net"], money("2866.85"))
        self.assertEqual(trace["actual_usage"]["settled_days"], 365)
        self.assertEqual(trace["actual_usage"]["confirmed_days"], 89)
        self.assertEqual(trace["device"]["current_party"], "滨江社区康养服务站")
        self.assertEqual(trace["device"]["stopped"], "returned")
        self.assertEqual(trace["identity_cluster"], ["A-WANG-COM", "A-WANG-EC"])
        self.assertEqual(len(trace["funding"]["reversals"]), 1)

    def test_recall_finds_current_responsible_party(self):
        rows = recall_responsibilities(self.ledger)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["serial_no"], "EXO-2026-0001")
        self.assertEqual(rows[0]["current_party"], "滨江社区康复站")  # 非最初购买人
        self.assertEqual(rows[0]["original_party"], "A-WANG-EC")

    def test_vendor_cannot_see_medical_or_funding_details(self):
        view = vendor_portal(self.ledger, "CR-2026-0009")
        dump = json.dumps(view, ensure_ascii=False)
        for forbidden in ("care_level", "low_income", "amount", "中度", "document_ref", "funding"):
            self.assertNotIn(forbidden, dump)
        self.assertEqual(view["status"], "returned")
        self.assertEqual(view["current_party"], "滨江社区康养服务站")
        self.assertEqual(view["stopped"]["recovered_by"], "安康机器人公司")

    def test_community_cannot_see_fiscal_details(self):
        view = community_portal(self.ledger, "A-WANG-COM")
        dump = json.dumps(view, ensure_ascii=False)
        for forbidden in ("amount", "document_ref", "price_after_discount", "funding", "9600"):
            self.assertNotIn(forbidden, dump)
        self.assertEqual(view["eligibility"][0]["care_level"], "中度")  # 照护安排需要
        care_robot = next(d for d in view["devices"] if d["serial_no"] == "CR-2026-0009")
        self.assertEqual(care_robot["status"], "returned")
        self.assertEqual(len(care_robot["service_periods"]), 3)

    def test_replay_is_deterministic(self):
        replayed = load_case()
        self.assertEqual(reconciliation_report(replayed), self.report)
        self.assertEqual([e["event_id"] for e in replayed.events], [e["event_id"] for e in CASE["events"]])


if __name__ == "__main__":
    unittest.main()
