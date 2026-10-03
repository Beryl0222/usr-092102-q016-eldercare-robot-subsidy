import json
import unittest
from pathlib import Path

from src.validator import validate_event


class ContractTest(unittest.TestCase):
    def test_sample_matches_envelope(self) -> None:
        sample = json.loads((Path(__file__).parents[1] / "data" / "sample.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_event(sample), [])

    def test_unknown_event_rejected(self) -> None:
        errors = validate_event({"event_type": "NOPE", "aggregate_type": "funding_entry"})
        self.assertTrue(any("未知事件类型" in e for e in errors))

    def test_event_must_belong_to_declared_aggregate(self) -> None:
        errors = validate_event({"event_type": "SUBSIDY_RELEASED", "aggregate_type": "device_service"})
        self.assertTrue(any("必须归属于聚合 funding_entry" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
