# 事件负载约定

本文件在 `domain.schema.json` 的事件信封之上约定 `payload` 结构。事件类型与聚合类型沿用
schema 中已登记的标识，不新增、不改名；维修、召回、退货、身份关联等业务细分通过
`payload.kind` 表达，使每次资格确认、发放、维修和冲正都能按事件重放。

## ELIGIBILITY_VERIFIED（aggregate: applicant_eligibility）

资格确认（`kind` 缺省或 `"eligibility"`）：

| 字段 | 说明 |
| --- | --- |
| `channel` | 申请渠道（ecommerce / community / …） |
| `document_ref` | 证件引用（散列，不存明文） |
| `name` / `birth_date` / `age` | 姓名、出生日期、确认时年龄 |
| `care_level` | 照护等级 |
| `low_income` | 低收入资格 |
| `region` | 申请地区（与政策适用地区核对） |
| `policy_id` / `policy_version` / `category` | 政策版本与认证品类 |
| `eligible` / `problems` | 结论与未通过原因 |
| `agent_refs` | 代办人引用（可选） |

跨渠道身份关联（`kind: "identity_association"`）：`with`（对方申请人引用）、
`status`（`pending` 待核对 / `confirmed` 已确认 / `rejected` 已排除）、`evidence`、`by`。
跨渠道身份只能形成待核对关联；经确认后才合并同人额度并阻断互斥资金。

## DEVICE_DELIVERED（aggregate: device_service，id 为设备序列号）

`category`、`contract_id`、`contract_type`（purchase / rental）、`price_after_discount`
（优惠后价格）、`applicant_ref`、`party`（交付时责任方）、`policy_id`、`policy_version`。
购置类在交付时触发付款；租赁类交付不付款，待服务确认。

## SERVICE_CONFIRMED（aggregate: device_service）

| `kind` | 含义与关键字段 |
| --- | --- |
| `service_period` | 实际服务期确认，`period.start` / `period.end`；租赁类触发该期付款 |
| `repair_start` / `repair_end` | 维修起止；`arrangement` 记录故障期间安排（备用机、顺延等） |
| `transferred` | 责任方转移，`party` 为新责任方；召回以当前责任方为准 |
| `recall` | 召回登记，`recall_id` |
| `returned` / `deactivated` | 退货回收 / 停用，`recovered_by`；只冲回未履行部分 |

## SUBSIDY_RELEASED（aggregate: funding_entry，version=1）

`applicant_ref`、`policy_id`、`policy_version`、`serial_no`、`contract_id`、
`trigger`（delivery / service_period）、`period`（服务期触发时的结算区间）、
`amount`、`exclusion_group`（互斥资金组）。
购置补贴 = min(优惠后价格 × 购置比例, 单件上限, 年度剩余额度)；
租赁补贴 = 目录租赁价 × 服务天数 / 服务期天数，同受单件上限与年度上限约束。

## FUNDS_REVERSED（aggregate: funding_entry，version≥2）

`amount`、`reason`（如 return_unfulfilled）、`serial_no`、`applicant_ref`、`note`。
冲正与原始发放落在同一 funding_entry 聚合上，按 version 顺序重放。
