"""校验领域事件信封的基础字段与稳定枚举。

枚举取值与 contracts/domain.schema.json 保持一致；事件的业务细分
（维修、召回、退货、身份关联等）通过 payload.kind 表达，不在此校验。
"""
from datetime import datetime

REQUIRED = ("event_id", "event_type", "aggregate_type", "aggregate_id", "occurred_at", "version", "summary")

EVENT_TYPES = (
    "ELIGIBILITY_VERIFIED",
    "DEVICE_DELIVERED",
    "SERVICE_CONFIRMED",
    "SUBSIDY_RELEASED",
    "FUNDS_REVERSED",
)

AGGREGATE_TYPES = (
    "benefit_policy",
    "applicant_eligibility",
    "device_service",
    "funding_entry",
)


def validate_event(record: dict) -> list[str]:
    errors = [f"缺少字段：{name}" for name in REQUIRED if name not in record]
    if "event_id" in record and not _non_empty_str(record["event_id"]):
        errors.append("event_id 必须是非空字符串")
    if "event_type" in record and record["event_type"] not in EVENT_TYPES:
        errors.append(f"event_type 必须是 {EVENT_TYPES} 之一")
    if "aggregate_type" in record and record["aggregate_type"] not in AGGREGATE_TYPES:
        errors.append(f"aggregate_type 必须是 {AGGREGATE_TYPES} 之一")
    if "aggregate_id" in record and not _non_empty_str(record["aggregate_id"]):
        errors.append("aggregate_id 必须是非空字符串")
    if "occurred_at" in record and not _is_datetime(record["occurred_at"]):
        errors.append("occurred_at 必须是 ISO 8601 时间")
    if "version" in record and (not isinstance(record["version"], int) or record["version"] < 1):
        errors.append("version 必须是正整数")
    if "summary" in record and not _non_empty_str(record["summary"]):
        errors.append("summary 必须是非空字符串")
    return errors


def _non_empty_str(value) -> bool:
    return isinstance(value, str) and len(value) > 0


def _is_datetime(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True
