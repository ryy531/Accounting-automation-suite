# `RealEstate/` ExampleCo 项目规则

`RealEstate/` 对应 ExampleCo 地产部门，是 ExampleCo Group 的独立工具型项目，不是会计平台 4 张核心表的一部分。

## 边界

- 不把 ExampleCo 数据塞进 Companies / People / Engagements / Events
- 不把 ExampleCo 的状态机、客户字段、交易流程硬接到 Payroll / T1 / Bookkeeping 等会计业务
- 可复用 `_lib/` 的公共能力，例如 AI、email、logger、配置读取
- 可复用 `data_access.py` 的接口契约思想，但数据后端和数据模型保持独立

## 修改原则

- 先等 Riley 补齐 ExampleCo 业务定义，再做自动化设计
- 涉及地产交易、客户通讯、金额、合同、外部邮件，一律按 C 模式处理
- 不主动把会计平台规则复制到 ExampleCo；只复用明确适合的公共能力
- 不为未来可能需求预先抽象

## 遇到边界不清

以下情况先停下问 Riley:

- ExampleCo 是否要共享某个 `_lib/data_access.py` 接口
- ExampleCo 数据是否需要进入会计平台视图
- ExampleCo 与 Companies / People 的关系如何建模
- 是否涉及客户邮件、钱、合同或对外沟通
