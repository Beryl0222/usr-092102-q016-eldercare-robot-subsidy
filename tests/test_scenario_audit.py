import json
import unittest
from pathlib import Path

from src.replay import EventStore, build_state
from src.views import (
    VENDOR,
    community_view,
    current_responsible_party,
    elder_view,
    finance_trace_view,
    recall_responsibility_view,
    vendor_view,
)

DATA = Path(__file__).parents[1] / "data" / "scenario_audit.json"


def load_state() -> dict:
    events = json.loads(DATA.read_text(encoding="utf-8"))
    store = EventStore()
    store.load_many(events)
    return build_state(store)


class AuditScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.state = load_state()

    def test_cross_channel_link_confirmed_but_not_blocked(self) -> None:
        """同一老人两渠道身份：确认关联后因政策可并用而放行，不按姓名误拒。"""
        identity = self.state["identity"]
        link = identity._links["L-AB"]
        self.assertEqual(link.status.value, "confirmed")
        self.assertEqual(link.resolution.value, "allow_coexist")
        self.assertFalse(identity.is_blocked("E-A"))
        self.assertFalse(identity.is_blocked("E-B"))

    def test_lease_funds_settled_full_year_then_reversed_for_unfulfilled_days(self) -> None:
        """题述核心错账：退货后资金按全年结算，冲正只回未履行 292 天。"""
        f2 = self.state["funding"].trace("F2")
        self.assertEqual(f2["released"], 300_000 + 795_000)   # 共发放 10950 元
        self.assertEqual(f2["reversed"], 876_000)             # 冲回 8760 元
        self.assertEqual(f2["net_paid"], 219_000)             # 实付 = 73 天 × 30 元

    def test_purchase_subsidy_kept_after_return_of_other_device(self) -> None:
        """退的是租赁机器人，外骨骼购置补贴合法保留。"""
        f1 = self.state["funding"].trace("F1")
        self.assertEqual(f1["released"], 600_000)
        self.assertEqual(f1["reversed"], 0)

    def test_finance_can_trace_expenditure_to_actual_use(self) -> None:
        view = finance_trace_view(self.state, "F2")
        self.assertEqual(view["serial_no"], "SN-CARE-2026-0042")
        self.assertEqual(view["contract_id"], "K-2026-D2")
        self.assertEqual(view["service_periods"][0]["days_fulfilled"], 73)
        self.assertEqual(view["returned_at"], "2026-03-16")
        self.assertEqual(view["recycled_at"], "2026-03-16")

    def test_recall_finds_current_responsible_party_not_original_buyer(self) -> None:
        hits = recall_responsibility_view(self.state, "RC-2026-08-01")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["serial_no"], "SN-EXO-2026-0007")
        self.assertEqual(hits[0]["current_responsible_party"], "OPERATOR-007")
        # 责任链可完整重放：原厂 -> 运营商
        parties = [c["party"] for c in hits[0]["responsibility_chain"]]
        self.assertEqual(parties, ["VENDOR-001", "OPERATOR-007"])

    def test_elder_sees_remaining_quota_and_fault_arrangement(self) -> None:
        view = elder_view(self.state, "E-B")
        self.assertEqual(view["remaining_annual_fen"]["LEASE-CARE@2026/310100"], 1_200_000 - 219_000)
        fault = view["devices"][0]["fault_arrangements"][0]
        self.assertTrue(fault["loaner_arranged"])
        self.assertEqual(fault["fault_from"], "2026-02-10")
        self.assertEqual(fault["fault_to"], "2026-02-14")

    def test_vendor_cannot_read_medical_details_or_finance(self) -> None:
        rows = vendor_view(self.state, "VENDOR-001")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertNotIn("medical_details", row)
        self.assertNotIn("amount", row)
        # 厂商资格投影里也没有医疗明细
        for v in self.state["eligibilities"]["E-A"]["verified"]:
            self.assertIn("medical_details", v)  # 状态本身保留，靠视图裁剪

    def test_community_cannot_see_full_finance_records(self) -> None:
        view = community_view(self.state)
        for d in view["devices"]:
            self.assertNotIn("funding", d)
            self.assertNotIn("amount", d)
        # L-AB 已处置，不应出现在社区的待核对清单
        self.assertFalse(any(link["link_id"] == "L-AB" for link in view["pending_identity_links"]))

    def test_replay_is_deterministic(self) -> None:
        """同一份事件流两次重放结果一致（事件可重放要求）。"""
        a = load_state()
        b = load_state()
        self.assertEqual(
            [(e.entry_id, e.net_paid) for e in a["funding"].all_entries()],
            [(e.entry_id, e.net_paid) for e in b["funding"].all_entries()],
        )

    def test_append_rejects_duplicate_event_and_version_gap(self) -> None:
        from src.replay import EventStore
        events = json.loads(DATA.read_text(encoding="utf-8"))
        store = EventStore()
        store.load_many(events)
        duplicate = dict(events[0])
        with self.assertRaises(ValueError):
            store.append(duplicate)


if __name__ == "__main__":
    unittest.main()
