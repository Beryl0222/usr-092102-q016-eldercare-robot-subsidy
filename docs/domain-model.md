# 养老机器人补贴履约领域模型

本文档约定事件负载字段、状态语义与角色边界。事件信封（`event_id`、
`event_type`、`aggregate_type`、`aggregate_id`、`occurred_at`、`version`、
`summary`）以 `contracts/domain.schema.json` 为准；事件标识只追加、不改义。

## 四类聚合

| 聚合 | 含义 | 关键标识 |
|---|---|---|
| `benefit_policy` | 政策版本 + 地区规则快照、认证品类目录 | `policy_code@version/region` |
| `applicant_eligibility` | 老人在某渠道的资格档案；跨渠道关联也挂此聚合 | `E-*`、关联 `L-*` |
| `device_service` | 合同与设备序列号、交付、服务、故障维修、召回、退货回收、责任方链 | `D-*` |
| `funding_entry` | 单笔资金的发放与冲正流水 | `F-*` |

同一聚合内事件 `version` 从 1 连续递增；`event_id` 全库唯一。事件只追加，
任何当前状态（剩余额度、冲正金额、召回责任方）都由重放事件流得到。

## 规则与计算（金额单位：分）

- **购置补贴**：`min(优惠后价格 × purchase_subsidy_bp/10000, 单件上限)`。
- **租赁补贴**：`min(合同日租价, 目录日租上限) × 实际服务天数`，再套单件上限。
- **年度上限**：按 `(资格档案, 政策版本)` 跨件累计；超额发放直接报错，不静默裁剪；
  冲正后额度恢复。
- **停用/退货冲正**：`已发放 × (已付天数 − 实际履行天数) / 已付天数`，
  只冲回未履行部分；已履行服务（含备用机保障期间）保留。
- 资格门槛：年龄、最低照护等级（none < mild < moderate < severe < disabled）、
  低收入资格按各政策自身规则判定。
- 认证品类：只有目录内 `category_code` 的设备可享受对应政策。

## 跨渠道身份与互斥

姓名**不参与自动去重**。两渠道档案（电商身份证 / 社区户口本）只产生
`IDENTITY_LINK_SUGGESTED`（待核对关联）：

1. 待核对状态不阻断任何资金。
2. 民政核实后发出 `IDENTITY_LINK_CONFIRMED`（同一人，给出
   `block` 或 `allow_coexist`）或 `IDENTITY_LINK_DISMISSED`（非同一人/证据不足）。
3. 互斥判定同时要求：同一自然人 **且** 政策同属一个 `mutex_group`。
   购置与租赁分属不同互斥组时，即使同一老人也合法并用。
4. `MUTEX_REVIEW_RESOLVED` 供财政复核留痕；只有结论为 block 时资金通道才阻断。

## 付款触发

- `DEVICE_DELIVERED` → `trigger=delivery` 的发放。
- `SERVICE_CONFIRMED`（按实际服务期分段确认）→ `trigger=service` 的发放。
- `DEVICE_RETURNED` / `SERVICE_TERMINATED` 本身不动钱；随后由
  `FUNDS_REVERSED` 按未履行天数冲回。

## 召回责任链

`DEVICE_DELIVERED` 与 `RESPONSIBLE_PARTY_CHANGED` 形成责任链；
召回（`RECALL_ISSUED`）时取链上最后一方为**当前责任方**，
可能是运营商而非最初购买人。

## 事件负载字段（信封之外）

| 事件 | 负载关键字段 |
|---|---|
| `POLICY_PUBLISHED` | kind, effective_from/to, min_age, min_care_level, low_income_required, certified_categories, purchase_subsidy_bp, lease_catalog_daily_cap, per_item_cap, annual_cap, mutex_group |
| `CATEGORY_CERTIFIED` | policy_code, policy_version, category_code |
| `ELIGIBILITY_VERIFIED` | channel, policy_code/version/region, age, care_level, low_income, medical_details, document_ref（引用而非全文）, valid_from/to |
| `CONTRACT_REGISTERED` | contract_id, kind, serial_no, category_code, eligibility_id, vendor_id, discounted_price 或 catalog_daily_rate, start/end_date |
| `DEVICE_DELIVERED` | responsible_party |
| `SERVICE_CONFIRMED` | period_start, period_end, days_fulfilled |
| `FAULT_REPORTED` | fault_from, loaner_arranged, arrangement |
| `REPAIR_COMPLETED` | resumed_at |
| `RECALL_ISSUED` | recall_id, reason |
| `RESPONSIBLE_PARTY_CHANGED` | to_party, effective_at, reason |
| `DEVICE_RETURNED` | recycled |
| `SERVICE_TERMINATED` | effective_date, reason |
| `IDENTITY_LINK_SUGGESTED` | eligibility_a, eligibility_b, reasons[] |
| `IDENTITY_LINK_CONFIRMED` | resolution(block/allow_coexist), decided_by, note |
| `IDENTITY_LINK_DISMISSED` | decided_by, note |
| `MUTEX_REVIEW_RESOLVED` | decision, reviewer, note |
| `SUBSIDY_RELEASED` | policy_code/version/region, eligibility_id, device_service_id, amount, trigger(delivery/service), days_paid |
| `FUNDS_REVERSED` | funding_entry_id, amount, reason, days_paid, days_fulfilled |

## 角色与字段级访问控制

| 角色 | 可见 | 不可见 |
|---|---|---|
| 老人/代办人 | 本人剩余年度额度、设备状态、实际服务期、故障期间备用机安排 | 他人档案 |
| 财政 | 任一支出 → 合同/序列号/实际服务期/冲正全链追溯、召回当前责任方 | （凭授权）医疗明细 |
| 民政 | 资格档案、政策与互斥复核 | 不直接操作资金流水 |
| 社区 | 服务履行、故障登记、待核对关联清单 | 补贴金额、资金流水、年度额度、医疗明细 |
| 厂商 | 本厂商责任设备的序列号、故障、召回、维修状态 | 医疗明细、低收入状态、证件引用、任何资金字段 |

裁剪在 `src/views.py` 完成：状态层保留全量数据用于重放，视图层按角色剥离，
厂商永远拿不到与自己设备无关的档案。

## 抽查场景样例

`data/scenario_audit.json` 是题述错账的完整可重放事件流：

- 同一老人在电商（外骨骼购置）与社区（机器人租赁）各有一份资格档案；
- 姓名一致产生待核对关联，确认同一人后因互斥组不同而放行并办；
- 租赁设备 3 月 16 日退租回收，但资金已按全年 365 天结算 10950 元；
- 实际履行 73 天（含 2/10–2/14 备用机期间），冲正 292 天 = 8760 元，实付 2190 元；
- 外骨骼未退货，购置补贴 6000 元保留；后发生召回，责任方已从原厂
  移交运营商，召回视图返回运营商。
