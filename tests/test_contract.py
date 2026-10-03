import json
import unittest
from pathlib import Path

from src.validator import validate_event


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        sample = json.loads((Path(__file__).parents[1] / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_unknown_event_type_rejected(self) -> None:
        sample = json.loads((Path(__file__).parents[1] / "data" / "sample.json").read_text(encoding="utf-8"))
        sample["event_type"] = "REPAIR_RECORDED"
        self.assertTrue(any("event_type" in e for e in validate_event(sample)))

    def test_unknown_aggregate_type_rejected(self) -> None:
        sample = json.loads((Path(__file__).parents[1] / "data" / "sample.json").read_text(encoding="utf-8"))
        sample["aggregate_type"] = "medical_record"
        self.assertTrue(any("aggregate_type" in e for e in validate_event(sample)))

    def test_occurred_at_must_be_datetime(self) -> None:
        sample = json.loads((Path(__file__).parents[1] / "data" / "sample.json").read_text(encoding="utf-8"))
        sample["occurred_at"] = "2026年9月20日"
        self.assertTrue(any("occurred_at" in e for e in validate_event(sample)))


if __name__ == "__main__":
    unittest.main()
