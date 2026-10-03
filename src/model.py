"""补贴规则：政策版本、资格门槛与金额计算。

所有金额以分为单位的整数传递，避免浮点误差。
购置补贴与租赁补贴是两类不同政策通道；是否互斥由政策互斥组在
身份关联确认后判定（见 src/identity.py），合法并用不得拒绝。
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class SupportKind(str, Enum):
    PURCHASE = "purchase"  # 购置（外骨骼等）
    LEASE = "lease"        # 租赁（社区看护机器人等）


# 照护等级序：数字越大依赖程度越高
CARE_LEVEL_ORDER = ("none", "mild", "moderate", "severe", "disabled")


def care_level_at_least(actual: str, required: str) -> bool:
    return CARE_LEVEL_ORDER.index(actual) >= CARE_LEVEL_ORDER.index(required)


@dataclass(frozen=True)
class PolicyRule:
    """一个政策版本在一个地区的规则快照（不可变，旧版本永久留存以备重放）。"""

    policy_code: str
    version: str
    region: str
    kind: SupportKind
    effective_from: date
    effective_to: date | None
    min_age: int
    min_care_level: str = "none"
    low_income_required: bool = False
    # 认证品类白名单（目录），元素为 category_code
    certified_categories: frozenset[str] = field(default_factory=frozenset)
    # 购置：按优惠后价格补贴的比例（基点，万分比；2000 = 20%）
    purchase_subsidy_bp: int = 0
    # 租赁：目录日租价上限（分/天），超出部分不补
    lease_catalog_daily_cap: int = 0
    per_item_cap: int = 0          # 单件（单台合同）上限，分
    annual_cap: int = 0            # 年度上限，分
    mutex_group: str | None = None  # 互斥组标识；同组补贴同一自然人不可兼得

    def in_effect(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day < self.effective_to)

    def category_allowed(self, category_code: str) -> bool:
        return category_code in self.certified_categories

    def check_eligibility(self, *, age: int, care_level: str, low_income: bool) -> list[str]:
        """返回不满足项的说明列表；空列表表示资格通过。"""
        problems: list[str] = []
        if age < self.min_age:
            problems.append(f"年龄 {age} 低于政策要求 {self.min_age}")
        if not care_level_at_least(care_level, self.min_care_level):
            problems.append(f"照护等级 {care_level} 低于要求 {self.min_care_level}")
        if self.low_income_required and not low_income:
            problems.append("政策要求低收入资格，申请人不具备")
        return problems


def _bp(amount: int, bp: int) -> int:
    return amount * bp // 10000


@dataclass(frozen=True)
class PurchaseQuote:
    discounted_price: int  # 优惠后价格（分），补贴基数


@dataclass(frozen=True)
class LeaseQuote:
    catalog_daily_rate: int  # 合同采用的目录租赁价（分/天）
    days: int                # 实际服务天数（停用/退货后不计）


def calc_subsidy(policy: PolicyRule, quote: PurchaseQuote | LeaseQuote) -> int:
    """按政策自身规则计算单件补贴：先算规则金额，再套单件上限。

    年度上限在 FundingLedger 中跨件累计后裁剪，不在这里处理。
    """
    if policy.kind is SupportKind.PURCHASE:
        assert isinstance(quote, PurchaseQuote)
        raw = _bp(quote.discounted_price, policy.purchase_subsidy_bp)
    else:
        assert isinstance(quote, LeaseQuote)
        daily = min(quote.catalog_daily_rate, policy.lease_catalog_daily_cap)
        raw = daily * quote.days
    if policy.per_item_cap:
        raw = min(raw, policy.per_item_cap)
    return max(raw, 0)


def prorate_reversal(released: int, days_paid: int, days_fulfilled: int) -> int:
    """停用/退货冲正：只冲回未履行部分，已履行服务保留。"""
    if days_paid <= 0:
        return 0
    unfulfilled = max(days_paid - days_fulfilled, 0)
    return released * unfulfilled // days_paid
