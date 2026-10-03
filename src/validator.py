"""校验领域事件信封的基础字段。"""

from src.contracts import AGGREGATE_TYPES, EVENT_AGGREGATE

REQUIRED = ("event_id", "event_type", "aggregate_type", "aggregate_id", "occurred_at", "version", "summary")


def validate_event(record: dict) -> list[str]:
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    etype = record.get("event_type")
    if etype is not None and etype not in EVENT_AGGREGATE:
        errors.append(f"未知事件类型：{etype}")
    atype = record.get("aggregate_type")
    if atype is not None and atype not in AGGREGATE_TYPES:
        errors.append(f"未知聚合类型：{atype}")
    if etype in EVENT_AGGREGATE and atype in AGGREGATE_TYPES:
        want = EVENT_AGGREGATE[etype]
        if atype != want:
            errors.append(f"{etype} 必须归属于聚合 {want}")
    return errors
