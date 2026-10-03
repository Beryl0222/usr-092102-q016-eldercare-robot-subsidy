"""跨渠道身份核对与互斥处理。

核心约束（题述）：
- 不能只按姓名去重：姓名只参与产生"待核对关联"，绝不自动阻断。
- 跨渠道（电商订单 / 社区申请，证件信息不同）只形成待核对关联。
- 互斥资金经人工确认后才阻断；核对排除（同一老人合法并用不同政策）
  或政策本身允许并用时，不阻断。
"""

from dataclasses import dataclass, field
from enum import Enum


class LinkStatus(str, Enum):
    SUGGESTED = "suggested"   # 待核对
    CONFIRMED = "confirmed"   # 确认同一自然人
    DISMISSED = "dismissed"   # 排除：非同一人，或证据不足


class MutexResolution(str, Enum):
    PENDING = "pending"
    BLOCK = "block"                 # 确认为互斥重复申领，阻断后续资金
    ALLOW_COEXIST = "allow_coexist"  # 政策可并用 / 非互斥通道，放行


def is_mutex_pair(mutex_group_a: str | None, mutex_group_b: str | None) -> bool:
    """两项政策是否构成互斥：同属一个互斥组才互斥；组为空或不同均可并用。"""
    return mutex_group_a is not None and mutex_group_a == mutex_group_b


@dataclass
class IdentityLink:
    """两份申请档案之间的待核对关联。"""

    link_id: str
    eligibility_a: str
    eligibility_b: str
    reasons: tuple[str, ...]          # 例如 ("姓名+出生日期相近", "联系方式相同")
    status: LinkStatus = LinkStatus.SUGGESTED
    resolution: MutexResolution = MutexResolution.PENDING
    decided_by: str | None = None
    note: str = ""

    @property
    def blocks_funds(self) -> bool:
        return (
            self.status is LinkStatus.CONFIRMED
            and self.resolution is MutexResolution.BLOCK
        )


@dataclass
class IdentityRegistry:
    _links: dict[str, IdentityLink] = field(default_factory=dict)

    def suggest(self, link: IdentityLink) -> IdentityLink:
        if link.link_id in self._links:
            raise ValueError(f"关联已存在：{link.link_id}")
        self._links[link.link_id] = link
        return link

    def confirm(self, link_id: str, *, decided_by: str, resolution: MutexResolution, note: str = "") -> None:
        link = self._require(link_id)
        if link.status is not LinkStatus.SUGGESTED:
            raise ValueError(f"关联已处置：{link_id}")
        link.status = LinkStatus.CONFIRMED
        link.resolution = resolution
        link.decided_by = decided_by
        link.note = note

    def dismiss(self, link_id: str, *, decided_by: str, note: str = "") -> None:
        link = self._require(link_id)
        if link.status is not LinkStatus.SUGGESTED:
            raise ValueError(f"关联已处置：{link_id}")
        link.status = LinkStatus.DISMISSED
        link.resolution = MutexResolution.ALLOW_COEXIST
        link.decided_by = decided_by
        link.note = note

    def is_blocked(self, eligibility_id: str) -> bool:
        return any(
            l.blocks_funds and eligibility_id in (l.eligibility_a, l.eligibility_b)
            for l in self._links.values()
        )

    def pending_links_for(self, eligibility_id: str) -> list[IdentityLink]:
        return [
            l for l in self._links.values()
            if l.status is LinkStatus.SUGGESTED
            and eligibility_id in (l.eligibility_a, l.eligibility_b)
        ]

    def _require(self, link_id: str) -> IdentityLink:
        if link_id not in self._links:
            raise KeyError(f"未知关联：{link_id}")
        return self._links[link_id]
