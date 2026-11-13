# Payroll Assistant 架构方案

> 本文档是 Claude Code 开发 payroll-assistant 项目的北极星文档。
> 实施过程中若发现方案不符实际,**先改本文档再改代码**,文档优先。

---

## 1. 项目目标

为 Saskatchewan payroll 工作建立一套半自动工具链,将会计师从"录入 + 计算 + 审查"的三重角色,简化为"审查 AI 产出 + 录入 QB"。

AI 负责:
- 读取客户五花八门的 timesheet(Excel、邮件、微信/Zalo 截图、手写件等)
- 从历史 QB payroll PDF 反推员工当前状态(时薪、工龄、上期 stat pay 时薪等)
- 按 SK 法律规则和公司习惯计算工资
- 生成 QB 录入清单和干净的计算 Excel

## 2. 非目标(明确不做的事)

- **不替代 QB**:QB Desktop 录入仍由人工完成
- **不改变现有文件组织**:共享盘上客户文件夹结构完全不动
- **不建独立员工数据库**:员工信息从历史 QB PDF 现场读取,不维护平行 YAML 档案
- **不自动决策**:所有关键判断(salary 员工时薪、调薪生效日、员工身份合并等)由人确认,AI 只提建议

## 3. 技术约束

- 运行环境:Windows + VS Code + Claude Code
- 语言:Python(已安装)
- 共享盘路径:UNC 格式 `\\file-server\Shared Folder\...`
- **本项目的 `CLAUDE.md` 仅限本目录生效**,不得影响全局 Claude Code 配置或其它项目
- 共享盘断线时脚本必须立即停止报错,不得静默失败或产生错误结果

## 4. 共享盘路径约定

### 4.1 客户文件夹定位规则

- **字母打头公司**:`\\file-server\Shared Folder\1. Accounting - Business Clients\{首字母大写}\{公司全名}\`
  - 示例:`Sample Noodle House Corp` → 在 `S\` 下
- **数字打头公司**:`\\file-server\Shared Folder\1. Accounting - Business Clients\#Number Companies\{公司全名}\`
  - 示例:`101234567 Saskatchewan Ltd. (Sample Nail Studio)...` → 在 `#Number Companies\` 下

### 4.2 客户 Payroll 目录

- 位置:`{客户文件夹}\Payroll\{年份}\`
- 结构不一致:有的有 `timesheet` 子文件夹,有的没有,有的还有其它子结构
- 历史 payroll 文件均为 **QB 生成的 PDF**,格式每期一致

### 4.3 客户规模

- 当前客户数约 164+,按 **200 个客户窗口**设计
- 用**懒加载 + 模糊匹配**定位,不预建索引
- 模糊匹配失败时报错停下,由会计师手动指定路径

## 5. 目录结构

```
payroll-assistant/
├── ARCHITECTURE.md              # 本文件
├── FUTURE_PHASES.md             # 已识别但推迟的功能(Phase A, Phase B)
├── CLAUDE.md                    # 本项目专属工作指引(仅本目录生效)
├── config.yaml                  # 共享盘根路径、字母映射规则、会计师首字母等
├── rules/
│   ├── sk_employment_standards.md    # SK 法律规则(稳定)
│   └── company_conventions.md        # 公司内部习惯(可调整)
├── scripts/
│   ├── resolve_client_path.py        # 客户名 → UNC 路径(懒加载 + 模糊匹配)
│   ├── extract_qb_pdf.py             # 解析 QB paystub PDF
│   ├── build_employee_state.py       # 从最近 N 期 PDF 构建员工当前状态
│   ├── extract_timesheet.py          # AI 辅助读取杂乱 timesheet
│   ├── calculate_payroll.py          # 核心计算器
│   └── generate_outputs.py           # 生成 Excel + QB 录入清单
├── client_notes/                # 可选:仅存 PDF 查不到的补充信息
│   └── {client_id}.yaml         #(如员工别名合并等)
├── staging/                     # 每期工作区(本地,用完即弃)
│   └── {client_id}_{period}/
│       ├── extracted.yaml       # AI 提取结果(供会计师审查)
│       └── qb_input.md          # QB 录入清单
└── cache/                       # PDF 解析缓存(加速二次访问)
```

## 6. Timesheet 位置约定

**Timesheet 不投放到本地 staging/**,而是**每期由会计师在启动任务时指定路径**。

一般位置是客户自己的 Payroll 文件夹下,例如:
- `{客户文件夹}\Payroll\{年份}\timesheet\` (有 timesheet 子文件夹时)
- `{客户文件夹}\Payroll\{年份}\{期号}\` (按期归档时)
- 或会计师临时指定的任何路径

Claude Code 启动任务时必须要求会计师**明确指定本期 timesheet 路径**,不得默认猜测。

若 timesheet 附带客户说明(邮件文本、微信截图等),也一并放在同路径下或通过指定路径提供。

## 7. 输出归档约定

| 产物 | 位置 | 说明 |
|------|------|------|
| 计算 Excel | `{客户文件夹}\Payroll\{年份}\Excel\{period}_payroll_calc.xlsx` | `Excel` 子文件夹不存在则自动创建;表格必须干净,只含计算过程,无多余信息 |
| QB 录入清单 | `payroll-assistant/staging/{client_id}_{period}/qb_input.md` | 留本地,录入 QB 后即可删除,不上共享盘 |
| QB 生成的 paystubs | 原地(由 QB 在会计师录入后生成) | 工具不介入 |

## 8. 每期工作流

```
1. 会计师告诉 Claude Code:
   "处理 {客户} {期号},timesheet 在 {路径}"

2. Claude Code 执行(第一阶段 - 提取):
   a. 通过 resolve_client_path 定位共享盘客户文件夹
   b. 读取最近 N 期 QB paystub PDF(建议 N=3)
   c. build_employee_state:构建当前员工状态
      - 员工列表(含最近出现/消失的异动 flag)
      - 每人时薪
        · hourly 员工:直接从 PDF 读
        · salary 员工:优先查上期 stat pay 记录,无则继续往前找,
          仍无则要求人工指认(不使用当期薪水÷工时反推作为主依据)
      - 最近 28 天工资窗口(用于 stat pay 计算)
      - pay type 判断
   d. extract_timesheet:AI 读取会计师指定路径下的 timesheet 文件,
      输出结构化工时数据
   e. 生成 staging/{client_id}_{period}/extracted.yaml,**暂停**并向会计师展示:
      - 员工异动 flag(疑似入职/离职/调薪)
      - Timesheet 提取结果 + 原文对照
      - 低置信度项
      - 任何无法自动判断的事项(salary 时薪缺失、歧义指令等)

3. 会计师审查 extracted.yaml,修正或确认

4. 会计师对 Claude Code 说:"继续计算"

5. Claude Code 执行(第二阶段 - 计算):
   a. 按 SK 规则 + 公司习惯计算(见第 9、10、11 节)
   b. 生成计算 Excel(写入共享盘 Excel 子文件夹)
   c. 生成 QB 录入清单(本地 staging)
   d. 列出所有审查 flag

6. 会计师照 qb_input.md 录入 QB,录完后手动删除 staging 文件夹
```

**两阶段不允许合并**:必须先停下让人审查 extracted.yaml 再计算。

## 9. 规则文档:SK 法律规则

文件位置:`rules/sk_employment_standards.md`

基于项目中已有的 `sk_employment_standards_cheatsheet.docx` 内容整理,覆盖:

- **加班门槛**:日 > 8 小时、周 > 40 小时、法定假日周 > 32 小时
- **10 个 SK 法定假日**及 2026 年日期
  - 明确哪些**不是**法定假日(Easter Monday、Christmas Eve、Boxing Day、Sept 30 等)
- **Public Holiday Pay** = 假日前 28 天总工资 × 5%
  - "总工资"含基本工资、佣金、年假工资、其他假日工资
  - 不含加班费
- **Premium Pay** = 假日当天工时 × 1.5 倍时薪
- **Holiday Week OT**:假日那周加班门槛降至 32 小时
- **Vacation Pay 比率**:
  - 不满 10 年:年工资 × 3/52(≈5.77%)
  - 满 10 年+:年工资 × 4/52(≈7.69%)
- **特殊行业规则**(餐厅等,本项目主要涉及餐厅和美甲店)

所有计算输出时必须**注明依据的是哪条规则**。

## 10. 规则文档:公司习惯

文件位置:`rules/company_conventions.md`

**必须写死,防止 AI 自由发挥**:

| 场景 | 处理方式 |
|------|---------|
| 客户 timesheet 未提加班指标 | 假日默认未工作,无 Premium Pay |
| 客户 timesheet 有假日工时 | 视为加班,计 Premium Pay |
| 客户只给总工时,不给每日 | **按均摊到每一天处理** |
| 客户说 "no changes" | 沿用上期工时,**不**按日历重算 |
| Salary 员工 stat pay 时薪 | 查上期 stat pay 记录;无则继续往前找;仍无则要求人工指认 |
| 客户指令歧义 | 停下来问人,不要默认解读 |
| 发现上期 payroll 疑似错漏 | 标记 flag,不自动修正 |

## 11. QB 录入约定

QB Desktop 输入时,**只填薪水(Rate)和工时(Qty)两个字段**,YTD 由 QB 自动算。

QB 录入清单(`qb_input.md`)按如下格式组织,每个员工分多行列出:

| 员工 | 行类型 | 薪水 (Rate) | 工时 (Qty) |
|------|--------|-------------|-----------|
| 张三 | Regular | 18.00 | 80.00 |
| 张三 | Overtime | 18.00 | 5.00 |
| 张三 | Stat Pay | 18.00 | 3.25 |

**重要约定**:

- **工时用 decimal 小数**:客户给 8.3 就写 8.3,不做 HH:MM 转换(客户侧已是小数,QB 内部会处理)
- **Stat pay 时长 = Stat pay 总额 ÷ 员工时薪**(QB 限制,必须换算成时长录入)
  - Salary 员工:时薪来自上期 stat pay 记录(见第 10 节)
- **加班另起一行**:薪水仍填基础时薪,QB 内部按 1.5 倍处理
  - ⚠️ **此约定在开发时需与 Riley 再次确认 QB 实际行为**,避免误解
- **Vac pay 自动计算**:若发现异常(金额对不上、比率错误等),以 flag 提醒,不自动修改

## 12. 异动侦测(build_employee_state 的关键功能)

对比最近两期 QB PDF,自动识别:

- **新出现姓名** → flag "疑似新员工,需确认入职日期"
- **消失姓名** → flag "疑似离职,需确认最后工作日"
- **时薪变化** → flag "疑似调薪,需确认生效日期"
- **异常数值**(如远低于最低时薪)→ flag "需人工核实"
  - 参考样本:Sample Nail Studio PDF 中员工 A 出现 $82.50 / 16:00 ≈ $5.16/hr,
    属明显异常(可能是部分周期、新入职、或手工调整)

所有 flag **只提示不决定**,由会计师在审查阶段确认。

## 13. CLAUDE.md 必须包含的内容

- 项目定位和 scope 边界
- **两阶段强制节奏**:提取 → 审查 → 计算,不允许跳过审查
- **何时必须停下问人**的清单:
  - 客户路径模糊匹配命中多个
  - Salary 员工时薪在历史记录中查不到
  - 客户指令含糊(如 "no changes" 但员工数与上期不同)
  - 疑似员工异动但原始 timesheet 未说明
  - 任何低置信度提取
- **规则引用方式**:所有计算必须在输出中注明依据的规则条目
- **文件命名和存放约定**(参见第 7 节)
- **错误处理**:共享盘断线立即停止,不继续跑

## 14. 分阶段实施

### Phase 1 — 地基(先做这部分)

1. 初始化目录结构
2. 写 `extract_qb_pdf.py`,并用 Sample Nail Studio 样本 PDF 测试通过
   - 验证点:能正确提取姓名、薪水、工时、VacPay 模式、YTD
   - Sample Nail Studio 样本特别注意:工时格式为 `HH:MM`(51:54 = 51 小时 54 分钟),
     但 QB 录入端用 decimal,本脚本只需忠实提取 PDF 原数据即可
3. 写 `rules/sk_employment_standards.md` 和 `rules/company_conventions.md`
4. 写 `CLAUDE.md`

### Phase 2 — 核心流程

5. `resolve_client_path.py` + `build_employee_state.py`
6. `calculate_payroll.py`
   - 先做 hourly 员工的 regular + OT + stat pay + vacation pay
   - salary 员工 stat pay 逻辑后加
7. `generate_outputs.py`(Excel + QB 录入清单)
8. **选一个试点客户**走一遍完整流程

### Phase 3 — AI 提取层

9. `extract_timesheet.py` + AI 调用
10. 异动侦测 flag 完善

### Phase 4 — 铺开

11. 多客户试跑,迭代规则文档
12. 常见 timesheet 格式识别模板

### 已识别但推迟的功能(详见 `FUTURE_PHASES.md`)

以下功能已识别需求但暂不在当前实施范围,等 Phase 1-4 主线跑顺后再启动:

- **Phase A — 客户到期提醒**:读主表预测下期 pay date,生成客户联系消息草稿,
  Windows Task Scheduler 定时触发。**直接对应老板"主动联系客户"的需求**。
- **Phase B — Payroll Tracker 主表自动写入**:每期 payroll 完成后自动更新
  `5. Payroll {YYYY.MM}.xlsx`,免手填"Done + R"。

两个 Phase 都涉及读写共享盘上的主表文件:
`\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\3. Task\{年份}\{年份.月份}\5. Payroll {年份.月份}.xlsx`

启动前须**复查该文件当时的实际格式**,不假设今天看到的结构稳定不变。

## 15. 需要会计师持续参与的事项

- 选 1 个试点客户(建议员工数中等、历史 PDF 齐全)
- Phase 1 完成后审查 PDF 提取结果是否准确
- `rules/` 两份文档的最终审定,特别是公司习惯部分
- 每期处理时的审查和决策(这是系统设计上就**不**自动化的部分)
- QB 加班行的薪水字段具体怎么填 — 需要在 Phase 2 前确认

## 16. 关键风险与已知限制

| 风险 | 应对 |
|------|------|
| Timesheet 格式差异极大,AI 提取可能出错 | 必须展示原文对照,置信度低的标 flag;渐进建立常见格式模板 |
| Salary 员工时薪历史追不到 | 停下来问会计师,不猜 |
| 员工别名(中英文+昵称)合并歧义 | 在 `client_notes/{client_id}.yaml` 存补充信息 |
| QB 加班行薪水字段的真实行为 | 开发前向会计师确认 |
| 共享盘断线 | 脚本立即报错 |
| 多人并发改同一期 | 本工具单人使用,不支持并发;若多人需协调 |
| 主表(`5. Payroll YYYY.MM.xlsx`)格式随时间漂移 | Phase A/B 启动前重新解析,不假设格式稳定;若格式大改,先更新 FUTURE_PHASES.md 的解析逻辑再写代码 |

---

*End of Architecture Document*
