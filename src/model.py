"""养老机器人补贴履约的领域对象与计算规则。

履约记录围绕：政策版本与地区、年龄与照护等级、低收入资格、认证品类、
设备序列号、购置或租赁合同、优惠后价格、实际服务期、维修召回、退货回收
与资金流水建立。金额一律用 Decimal 保留两位小数，服务期按天计量。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
DAY_SECONDS = Decimal(86400)


def money(value) -> Decimal:
    """把外部输入（int/float/str/Decimal）规整为两位小数金额。"""
    raw = value if isinstance(value, Decimal) else Decimal(str(value))
    return raw.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"时间必须带时区：{value}")
    return dt


def parse_day(value: str) -> date:
    return date.fromisoformat(value)


def days_between(start: datetime, end: datetime) -> Decimal:
    return Decimal(str((end - start).total_seconds())) / DAY_SECONDS


def age_on(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


@dataclass(frozen=True)
class CategoryRule:
    """认证品类的补贴规则：购置按比例，租赁按目录价，均受单件上限约束。"""

    mode: str  # "purchase" 购置 | "rental" 租赁
    per_item_cap: Decimal  # 单件上限
    purchase_ratio: Decimal | None = None  # 购置比例
    catalog_period_price: Decimal | None = None  # 目录租赁价（每服务期）
    period_days: int = 30  # 租赁服务期长度（天）

    @staticmethod
    def from_dict(spec: dict) -> "CategoryRule":
        return CategoryRule(
            mode=spec["mode"],
            per_item_cap=money(spec["per_item_cap"]),
            purchase_ratio=Decimal(str(spec["purchase_ratio"])) if spec.get("purchase_ratio") is not None else None,
            catalog_period_price=money(spec["catalog_period_price"]) if spec.get("catalog_period_price") is not None else None,
            period_days=int(spec.get("period_days", 30)),
        )

    def quote_purchase(self, price_after_discount) -> Decimal:
        """购置补贴 = min(优惠后价格 × 购置比例, 单件上限)。"""
        if self.mode != "purchase" or self.purchase_ratio is None:
            raise ValueError("该品类不是购置类")
        return min(money(money(price_after_discount) * self.purchase_ratio), self.per_item_cap)

    def quote_rental(self, days: Decimal) -> Decimal:
        """租赁补贴 = 目录租赁价 × 实际服务天数 / 服务期天数。"""
        if self.mode != "rental" or self.catalog_period_price is None:
            raise ValueError("该品类不是租赁类")
        return money(self.catalog_period_price * Decimal(days) / Decimal(self.period_days))


@dataclass(frozen=True)
class Policy:
    """补贴政策的一个版本。版本一旦登记不得改写，保证事件可重放。"""

    policy_id: str
    version: int
    region: str  # 适用地区
    min_age: int  # 最低年龄
    care_levels: tuple[str, ...]  # 覆盖的照护等级
    low_income_required: bool  # 是否要求低收入资格
    annual_cap: Decimal  # 年度上限（每申请人、每政策、每年）
    exclusion_group: str  # 互斥资金组：同组不同政策不得兼得
    categories: dict[str, CategoryRule]  # 认证品类目录

    @staticmethod
    def from_dict(spec: dict) -> "Policy":
        return Policy(
            policy_id=spec["policy_id"],
            version=int(spec["version"]),
            region=spec["region"],
            min_age=int(spec["min_age"]),
            care_levels=tuple(spec["care_levels"]),
            low_income_required=bool(spec["low_income_required"]),
            annual_cap=money(spec["annual_cap"]),
            exclusion_group=spec["exclusion_group"],
            categories={name: CategoryRule.from_dict(rule) for name, rule in spec["categories"].items()},
        )

    def eligibility_problems(self, *, region: str, age: int, care_level: str, low_income: bool, category: str) -> list[str]:
        problems = []
        if region != self.region:
            problems.append(f"地区 {region} 不在政策适用范围 {self.region}")
        if age < self.min_age:
            problems.append(f"年龄 {age} 岁未达 {self.min_age} 岁门槛")
        if care_level not in self.care_levels:
            problems.append(f"照护等级 {care_level} 不在覆盖范围")
        if self.low_income_required and not low_income:
            problems.append("该政策要求低收入资格")
        if category not in self.categories:
            problems.append(f"品类 {category} 不在认证目录")
        return problems


@dataclass
class Verification:
    """一次资格确认的记录（ELIGIBILITY_VERIFIED 的 payload）。"""

    channel: str  # 申请渠道（电商 / 社区 / …）
    document_ref: str  # 证件引用（散列，不存明文）
    name: str
    birth_date: date
    age: int
    care_level: str
    low_income: bool
    region: str
    policy_id: str
    policy_version: int
    category: str
    eligible: bool
    problems: tuple[str, ...]
    agent_refs: tuple[str, ...] = ()  # 代办人引用
    at: datetime | None = None


@dataclass
class ApplicantState:
    ref: str
    verifications: list[Verification] = field(default_factory=list)


@dataclass
class Association:
    """跨渠道身份关联。只能先形成待核对关联，经确认后才用于合并与阻断。"""

    pair: tuple[str, str]  # 排序后的两个申请人引用
    status: str  # pending 待核对 | confirmed 已确认 | rejected 已排除
    evidence: str
    by: str
    at: datetime


@dataclass
class Repair:
    start: datetime
    arrangement: str  # 故障期间安排（备用机、顺延等）
    note: str
    end: datetime | None = None


@dataclass
class DeviceState:
    serial_no: str  # 设备序列号（聚合 id）
    category: str
    contract_id: str  # 购置或租赁合同
    contract_type: str  # purchase | rental
    price_after_discount: Decimal  # 优惠后价格
    applicant_ref: str
    party: str  # 当前责任方（召回时以此为准，而非最初购买人）
    original_party: str
    policy_id: str
    policy_version: int
    delivered_at: datetime
    service_periods: list[tuple[datetime, datetime]] = field(default_factory=list)  # 实际服务期
    repairs: list[Repair] = field(default_factory=list)
    recalls: list[dict] = field(default_factory=list)
    party_history: list[tuple[str, datetime]] = field(default_factory=list)
    stopped: dict | None = None  # {kind: returned|deactivated, at, recovered_by, note}

    @property
    def status(self) -> str:
        if self.stopped:
            return self.stopped["kind"]
        if any(r.end is None for r in self.repairs):
            return "repairing"
        return "in_service"


@dataclass
class Reversal:
    amount: Decimal
    reason: str
    at: datetime
    note: str


@dataclass
class FundingState:
    """一笔资金流水（聚合 id = funding_id）：v1 发放，后续版本为冲正。"""

    funding_id: str
    applicant_ref: str
    policy_id: str
    policy_version: int
    serial_no: str
    contract_id: str
    trigger: str  # delivery 交付触发 | service_period 持续服务触发
    amount: Decimal
    exclusion_group: str
    released_at: datetime
    period: tuple[datetime, datetime] | None = None  # 服务期触发时的结算区间
    reversals: list[Reversal] = field(default_factory=list)

    @property
    def reversed_total(self) -> Decimal:
        return sum((r.amount for r in self.reversals), ZERO)

    @property
    def net(self) -> Decimal:
        return self.amount - self.reversed_total


def reversal_due(entry: FundingState, stop: datetime) -> Decimal:
    """停用时应冲回的总额：购置退货冲回全额；租赁只冲回未履行部分。

    已履行部分（停用日之前的服务期）保留，未履行部分按天数比例计算。
    """
    if entry.period is None:
        return entry.amount
    start, end = entry.period
    if stop >= end:
        return ZERO
    full = days_between(start, end)
    remaining = days_between(max(start, stop), end)
    return money(entry.amount * remaining / full)
