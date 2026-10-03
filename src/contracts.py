"""领域事件标识的唯一权威清单，与 contracts/domain.schema.json 保持同步。

枚举只允许追加：新事件在末尾新增，不得复用旧标识、不得改变既有含义。
"""

# 事件类型 -> 所属聚合。重放与校验都以此判定事件流归属。
EVENT_AGGREGATE: dict[str, str] = {
    # 既有事件（稳定标识，不得改义）
    "ELIGIBILITY_VERIFIED": "applicant_eligibility",
    "DEVICE_DELIVERED": "device_service",
    "SERVICE_CONFIRMED": "device_service",
    "SUBSIDY_RELEASED": "funding_entry",
    "FUNDS_REVERSED": "funding_entry",
    # 政策与认证目录
    "POLICY_PUBLISHED": "benefit_policy",
    "CATEGORY_CERTIFIED": "benefit_policy",
    # 合同、设备生命周期与责任方链
    "CONTRACT_REGISTERED": "device_service",
    "RESPONSIBLE_PARTY_CHANGED": "device_service",
    "FAULT_REPORTED": "device_service",
    "REPAIR_COMPLETED": "device_service",
    "RECALL_ISSUED": "device_service",
    "DEVICE_RETURNED": "device_service",
    "SERVICE_TERMINATED": "device_service",
    # 跨渠道身份核对（待核对关联 -> 确认/排除 -> 互斥复核）
    "IDENTITY_LINK_SUGGESTED": "applicant_eligibility",
    "IDENTITY_LINK_CONFIRMED": "applicant_eligibility",
    "IDENTITY_LINK_DISMISSED": "applicant_eligibility",
    "MUTEX_REVIEW_RESOLVED": "applicant_eligibility",
}

EVENT_TYPES: tuple[str, ...] = tuple(EVENT_AGGREGATE.keys())

AGGREGATE_TYPES: tuple[str, ...] = (
    "benefit_policy",
    "applicant_eligibility",
    "device_service",
    "funding_entry",
)
