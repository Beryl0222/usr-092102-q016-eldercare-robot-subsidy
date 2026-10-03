import json
import unittest
from pathlib import Path

from src.contracts import AGGREGATE_TYPES, EVENT_AGGREGATE, EVENT_TYPES


class SchemaSyncTest(unittest.TestCase):
    def test_python_registry_matches_json_schema(self) -> None:
        schema = json.loads(
            (Path(__file__).parents[1] / "contracts" / "domain.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(schema["properties"]["event_type"]["enum"]), set(EVENT_TYPES))
        self.assertEqual(set(schema["properties"]["aggregate_type"]["enum"]), set(AGGREGATE_TYPES))
        # 每个事件的归属聚合都在聚合枚举内
        for agg in EVENT_AGGREGATE.values():
            self.assertIn(agg, AGGREGATE_TYPES)


if __name__ == "__main__":
    unittest.main()
