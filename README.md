# 养老机器人补贴履约

本仓库保存养老机器人补贴履约的领域词汇、事件约定与履约台账代码，便于民政、社区、
财政、电商与设备厂商在后续开发中统一对象身份和版本语义。

## 目录

- `contracts/domain.schema.json`：领域事件信封及稳定枚举。
- `contracts/event-payloads.md`：各类事件的 `payload` 约定（资格、交付、服务、维修、
  召回、退货、发放、冲正、身份关联）。
- `data/sample.json`：一条可用于联调的中文业务样例。
- `data/audit_case.json`：财政抽查案例——同一老人经电商领取外骨骼购置补贴、以另一套
  证件经社区申请看护机器人租赁补贴，租赁设备退货后资金仍按全年结算；含两个政策版本
  的参考数据与全部履约事件，可整体重放核对。
- `src/`：事件校验、领域对象、事件溯源台账、核对报表与分级视图。
- `tests/`：领域资料与业务规则的一致性检查。

## 领域要点

- **事件标识沿用契约**：只用 schema 已登记的 5 类事件、4 类聚合；业务细分经
  `payload.kind` 表达。台账状态由事件重放得出，资格确认、发放、维修、冲正均可重放。
- **履约记录维度**：政策版本与地区、年龄与照护等级、低收入资格、认证品类、设备序列号、
  购置或租赁合同、优惠后价格、实际服务期、维修召回、退货回收、资金流水。
- **补贴计算**：购置 = min(优惠后价格 × 购置比例, 单件上限, 年度剩余额度)；
  租赁 = 目录租赁价 × 服务天数 / 服务期天数，同受单件上限与年度上限约束。
- **身份与互斥**：不按姓名去重；跨渠道同人只形成待核对关联，经确认后才合并额度、
  阻断同组互斥资金。不同互斥组的政策允许合法并用。
- **付款与冲正**：交付与持续服务分别触发付款；退货或停用只冲回未履行部分，
  已履行的服务期保留。
- **分级可见**：设备厂商不见照护等级等无关医疗明细与资金信息；社区不见完整财政资料；
  老人或代办人可查剩余额度与故障期间安排；财政可从任何支出追到实际使用，
  召回时定位设备当前责任方而非最初购买人。

## 本地检查

```bash
python3 -m unittest discover -s tests
```

## 快速上手

```python
from src.ledger import Ledger

ledger = Ledger()
ledger.register_policy({...})                      # 登记政策版本（不可改写）
ledger.verify_eligibility(applicant_ref="A-1", ...) # 资格确认，自动形成待核对关联
ledger.deliver_device(serial_no="EXO-1", ...)       # 交付；购置类同时触发付款
ledger.confirm_service(serial_no="CR-1", ...)       # 确认服务期；租赁类按目录价付款
ledger.record_stop(serial_no="CR-1", kind="returned", ...)
ledger.settle_stop(serial_no="CR-1", reason="return_unfulfilled", ...)  # 只冲回未履行部分

from src.audit import reconciliation_report
from src.views import applicant_portal, vendor_portal, community_portal, finance_trace
```
