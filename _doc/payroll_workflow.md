# Payroll 处理流程梳理

> 本文档梳理 SK payroll 处理的完整流程,作为业务参考和工具开发依据。
> 不同于 ARCHITECTURE.md(给 Claude Code 的开发指引),本文档是面向流程本身的描述。

---

## 流程总览

```
[1] 客户发来 timesheet
       ↓
[2] 定位客户文件夹,读取历史 QB PDF
       ↓
[3] 构建员工当前状态(时薪、工龄、上期 stat pay 等)
       ↓
[4] 解析 timesheet,提取本期工时和特殊情况
       ↓
[5] 【审查检查点】确认提取无误、异动 flag、salary 时薪等
       ↓
[6] 按 SK 规则 + 公司习惯计算工资
       ↓
[7] 生成计算 Excel(归档共享盘) + QB 录入清单(本地)
       ↓
[8] 录入 QB
       ↓
[9] QB 生成 paystubs,自动归档到客户文件夹
```

**强制节奏**:第 5 步是不可跳过的人工审查检查点。提取与计算之间必须有人确认,不允许端到端自动化。

---

## 第 1 步:客户 Timesheet 接收

### 输入形式

客户提交 timesheet 的方式没有统一格式,目前观察到的有:

- Excel 文件(格式各异,客户自制)
- 邮件正文文本(直接写在邮件 body 里)
- 微信截图(图片)
- Zalo 截图(图片)
- 手写工时表照片
- 偶尔附带的客户口头说明("这周小王放假"、"no changes this week"等)

### 工时单位约定

**客户写的工时是 decimal 小数**(如 `8.3` 表示 8.3 小时)。后续 QB 录入也用 decimal,**不做 HH:MM 转换**。

> 注意:历史 QB paystub PDF 里的工时是 `HH:MM` 格式(如 `51:54` = 51 小时 54 分钟)。这只是 QB 的显示格式,与 timesheet 输入端无关。

### 客户附带说明的常见用语

| 客户说法 | 含义 | 处理方式 |
|---|---|---|
| "no changes" / "same as last time" | 沿用上期工时 | 直接复制上期数据,**不**按日历重新算 |
| "X 这周没上班" | 该员工本期无工时 | 工时为 0 |
| "X 加班了 Y 小时" | 计入加班工时 | 见加班规则 |
| 提到假日工时 | 视为假日加班 | 计入 Premium Pay |
| 未提加班指标 + 无假日工时 | 假日默认未工作 | 不计 Premium Pay |

---

## 第 2 步:客户文件夹定位

### 路径规则

共享盘根:`\\file-server\Shared Folder\1. Accounting - Business Clients\`

**字母打头的公司**:`{首字母大写}\{公司全名}\`
- 例:`Sample Noodle House Corp` → `S\Sample Noodle House Corp\`

**数字打头的公司**:`#Number Companies\{公司全名}\`
- 例:`101234567 Saskatchewan Ltd. (Sample Nail Studio)_Jane Doe_Monthly_SB+`

### Payroll 子目录

`{客户文件夹}\Payroll\{年份}\`

子结构不一致:
- 有的有独立 `timesheet` 子文件夹
- 有的没有,timesheet 直接放在年份目录下
- 有的还有更深的子文件夹结构

### 历史 PDF 位置

历史 payroll 文件均为 **QB Desktop 生成的 PDF**,放在 `Payroll\{年份}\` 下。每期 1 个 PDF,每页对应 1 个员工的 paystub。

---

## 第 3 步:构建员工当前状态

### 信息来源

**没有员工花名册文件**。员工信息全部从历史 QB paystub PDF 现场提取。

### 需要提取的字段

每个员工需要知道:

| 字段 | 来源 | 说明 |
|---|---|---|
| 姓名 | PDF 顶部 | 注意客户可能用别名/昵称指同一人 |
| 时薪(hourly 员工) | PDF 中 Earnings 行 | 直接读 |
| 时薪(salary 员工) | **优先**:上期 stat pay 记录里的时薪<br>**次选**:再往前找有 stat pay 的期 | 详见下方"salary 员工时薪规则" |
| 工龄 | 从最早一期 PDF 反推 | 影响 vacation pay 比率(3/52 vs 4/52) |
| Pay type | hourly vs salary | 看 Earnings 行的标签("Salary" 字样即 salary) |
| VacPay 模式 | Earnings 中是否有 "VacPay-Paid Out" | 两种模式见下 |
| 最近 28 天工资总和 | 从近 1-2 期 PDF 拼接 | 用于本期 stat pay 计算 |

### Salary 员工时薪规则(重要)

公司习惯:**salary 员工的 stat pay 时薪不是从当期薪水反推**,而是沿用历史记录中的固定时薪。

**查找顺序**:

1. 最近一期有 stat pay 的 PDF → 读那一期的 stat pay 时薪
2. 找不到 → 继续往前翻
3. 仍找不到 → **停下,要求人工指认**,不要从当期薪水 ÷ 工时反推

> 反推的结果只能作为参考提示(比如"建议时薪约 $25/hr"),不能作为最终值。

### VacPay 两种模式

**模式 A:Paid Out(每期付清)**
- PDF 中 Earnings 部分显示 `VacPay-Paid Out` 行
- 比率:不满 10 年用 3/52(≈5.77%);满 10 年用 4/52(≈7.69%)

**模式 B:Accrued(累计)**
- PDF 中显示 `Sick Hours and Vacation Pay Earned YTD Used Available` 区块
- 不每期付出,累计在账上

工具需识别每个员工是哪种模式,计算逻辑不同。

### 异动侦测

对比最近两期 PDF,自动 flag:

| 现象 | flag 内容 |
|---|---|
| 员工姓名新出现 | 疑似新员工,需确认入职日期 |
| 员工姓名消失 | 疑似离职,需确认最后工作日 |
| 时薪变化 | 疑似调薪,需确认生效日期 |
| 异常时薪(如远低于最低时薪) | 需人工核实(如 Sample Nail Studio 样本中 $5.16/hr 的异常记录) |

所有 flag 仅提示,**最终判断在人**。

---

## 第 4 步:解析本期 Timesheet

### Timesheet 路径由用户每次指定

不假设固定位置。常见位置(供参考):

- `{客户文件夹}\Payroll\{年份}\timesheet\`
- `{客户文件夹}\Payroll\{年份}\{期号}\`
- 任何会计师指定的路径

### 提取的字段

每个员工需提取:

| 字段 | 类型 | 备注 |
|---|---|---|
| 员工姓名(或别名) | 字符串 | 与员工状态做匹配 |
| 本期总工时 | decimal 数 | 客户给的就是 decimal |
| 假日工时(如有) | decimal 数 | 见下"假日工时判断" |
| 是否有加班 | bool | 见下"加班判断" |
| 特殊情况备注 | 字符串 | 如"小王本期休假"等 |

### 假日工时判断

客户通常**不会精确说明假日那天工时是多少**,需结合上下文推断。公司习惯:

- 客户 timesheet 未提加班指标 + 总工时正常 → 假日默认未工作,无 Premium Pay
- 客户 timesheet 有明确"假日工时 X 小时" → 计入 Premium Pay
- 总工时偏高且本期内有假日 → flag 提醒人工确认

### 加班判断

公司只给总工时,**不给每日明细**。所以:

- **按均摊到每一天处理** —— 总工时 ÷ 周期天数 = 日均工时
- 日均 > 8 小时,或周均 > 40 小时(假日周 > 32 小时)→ 计入 OT
- 客户主动说"X 加班了 Y 小时" → 直接采用

---

## 第 5 步:人工审查(强制检查点)

### 审查内容

工具生成 `extracted.yaml` 后停下,人工逐项确认:

1. **员工身份**:AI 提取的员工是否对应正确的员工档案?(尤其当客户用别名时)
2. **异动 flag**:疑似新入职/离职/调薪是否属实?生效日期是?
3. **Salary 时薪**:历史中找不到时薪的员工,人工指认正确值
4. **歧义指令**:客户说的话是否有多种理解?如"no changes"但员工数与上期不同
5. **低置信度提取**:截图模糊、手写难辨等情况
6. **疑似上期错漏**:工具如果发现上期可能算错,标 flag 让人决定是否补救

### 必须停下问人的清单

- 客户路径模糊匹配命中多个
- Salary 员工时薪在历史记录中查不到
- 客户指令含糊
- 疑似员工异动但原始 timesheet 未说明
- 任何低置信度提取
- 共享盘断线

---

## 第 6 步:计算工资

### SK 法律规则要点

#### 加班(Overtime)

| 标准 | 门槛 |
|---|---|
| 每日 | > 8 小时 |
| 每周 | > 40 小时 |
| **法定假日所在周** | **> 32 小时**(不含假日当天工时) |

加班费率:1.5 倍时薪。

#### 法定假日(2026 年 SK 共 10 个)

| 假日 | 日期 |
|---|---|
| New Year's Day | 1/1 |
| Family Day | 2/16 |
| Good Friday | 4/3 |
| Victoria Day | 5/18 |
| Canada Day | 7/1 |
| Saskatchewan Day | 8/3 |
| Labour Day | 9/7 |
| Thanksgiving | 10/12 |
| Remembrance Day | 11/11 |
| Christmas Day | 12/25 |

**不是** SK 法定假日(易混淆):Easter Monday、Christmas Eve、Boxing Day、Sept 30 (Truth & Reconciliation Day,只对联邦监管单位适用)。

#### 法定假日的三笔钱

**① Public Holiday Pay(人人都有)**
- 公式:**前 28 天总工资 × 5%**
- "总工资"含:基本工资 + 佣金 + 年假工资 + 其他假日工资
- **不含**:加班费

**② Premium Pay(假日当天上班才有)**
- 公式:**假日当天工时 × 1.5 倍时薪**
- 与 ① 叠加发放

**③ Holiday Week OT**
- 假日所在周 OT 门槛降至 32 小时(见上加班表)

#### Vacation Pay

| 工龄 | 比率 | 百分比 |
|---|---|---|
| 不满 10 年 | 3/52 | ≈5.77% |
| 满 10 年+ | 4/52 | ≈7.69% |

"工资"含:基本 + 佣金 + 奖金 + 加班费 + 假日工资 + 之前的假期工资。

### 公司习惯规则(必须严格遵守)

| 场景 | 处理方式 |
|---|---|
| 客户 timesheet 未提加班指标 | 假日默认未工作,无 Premium Pay |
| 客户 timesheet 有假日工时 | 视为加班,计 Premium Pay |
| 客户只给总工时,不给每日 | **按均摊到每一天处理**计算 OT |
| 客户说 "no changes" | 沿用上期工时,**不**按日历重算 |
| Salary 员工 stat pay 时薪 | 查上期 stat pay 记录 → 往前找 → 仍无则要求人工指认 |
| 客户指令歧义 | 停下来问人,不要默认解读 |
| 发现上期 payroll 疑似错漏 | 标记 flag,**不**自动修正 |

### 计算输出要求

每一项金额必须**注明依据的规则条目**,如:
- "Stat Pay = 前 28 天工资 × 5% (依据:SK Employment Standards 第 X 条)"
- "Salary 时薪沿用 2026-03-31 那期记录(依据:公司习惯第 5 条)"

---

## 第 7 步:输出生成

### 计算 Excel(归档用)

**位置**:`{客户文件夹}\Payroll\{年份}\Excel\{period}_payroll_calc.xlsx`
- 子文件夹 `Excel\` 不存在则自动创建
- 文件名格式:`2026-04-30_payroll_calc.xlsx`

**内容要求**:
- 干净的计算过程表格
- 不含多余说明、注释、commentary
- 颜色约定:蓝色 = 硬编码输入,黑色 = 公式,绿色/黄色背景 = 计算结果
- 列只放计算用得到的字段,不加额外的"备注"列

### QB 录入清单(操作用)

**位置**:`payroll-assistant/staging/{client_id}_{period}/qb_input.md`
- 留本地,录入完成后可删除
- 不上共享盘

**格式**:每个员工分多行列出

| 员工 | 行类型 | 薪水 (Rate) | 工时 (Qty) |
|---|---|---|---|
| 张三 | Regular | 18.00 | 80.00 |
| 张三 | Overtime | 18.00 | 5.00 |
| 张三 | Stat Pay | 18.00 | 3.25 |

### 审查 flag 列表

附在 QB 录入清单顶部:
- 异常情况
- 待人工确认事项
- 疑似上期错漏

---

## 第 8 步:QB Desktop 录入

### QB 输入约定

**只填两个字段**:Rate(薪水)+ Qty(工时)。其他(YTD、扣税、净额)QB 自动算。

### 关键转换规则

**Stat Pay 时长 = Stat Pay 总额 ÷ 员工时薪**

QB 限制:stat pay 必须以"时长"形式录入,不能直接录金额。所以要把工具算出的 stat pay 总额换算回时长。

**Salary 员工的 stat pay 时薪**来自:
1. 上期 stat pay 记录(优先)
2. 再往前找
3. 仍找不到 → 人工指认

### 加班行处理

加班另起一行:
- Rate 仍填基础时薪($18 而非 $27)
- QB 内部会按 1.5 倍计算

> ⚠️ **此约定需在实际操作中再次验证 QB 实际行为**。如果 QB 实际要求录入已乘 1.5 的时薪,以实际为准。

### Vacation Pay 由 QB 自动算

工具不录入 vac pay。如果工具计算结果与 QB 自动算的不一致(异常),只 flag 提醒人工核对,**不修改**。

---

## 第 9 步:QB 生成 Paystubs

QB 完成录入后会自动生成 PDF paystubs,归档到客户原文件夹下(位置不变)。

**工具不介入此步**。

---

## 边界条件与异常处理

### 共享盘断线

脚本必须**立即停止报错**,不得:
- 静默跳过
- 使用过时缓存
- 继续推进生成可能错误的结果

### 文件被同事打开

未来涉及写入主表的功能(Phase B)需检测文件锁。读取通常不受影响(用 read_only 模式)。

### 客户提供的 timesheet 完全无法解析

例如:截图过于模糊、手写无法辨认、邮件内容残缺。

处理:工具 flag "无法提取",**不猜测**,要求人工补充或重新提交。

### 员工别名/昵称识别

客户可能用"小王 / Wang / 王师傅"指同一人。工具初次遇到时 flag 让人确认,确认结果存入 `client_notes/{client_id}.yaml` 备查。

### 上期 PDF 不存在或格式异常

如果某客户是新建立合作、之前没在我司处理过 payroll,无历史 PDF 可读:
- Salary 员工时薪需手动给定
- 28 天工资窗口需要从客户提供的资料推断
- 此情形整体提示"首次处理,需更多人工干预"

---

## 关键术语速查

| 术语 | 含义 |
|---|---|
| **Stat Pay** | Public Holiday Pay,法定假日工资 |
| **Premium Pay** | 假日当天上班的额外 1.5 倍工资 |
| **Stat Pay 时薪** | 计算 stat pay 时长用的时薪基准 |
| **28 天窗口** | 法定假日前 28 天的工资总和(stat pay 计算基础) |
| **VacPay-Paid Out** | 每期付清的假期工资 |
| **VacPay Accrued** | 累计但未付的假期工资 |
| **MWA** | Modified Work Arrangement,弹性排班协议 |
| **Holiday Week OT** | 假日所在周的 OT 门槛降至 32 小时的规则 |
| **Pay Period** | 工资周期(monthly / semi-monthly / bi-weekly 等) |
| **Pay Date** | 实际发工资的日期 |
| **Cheque Date** | QB PDF 上显示的支付日期,通常等同于 Pay Date |

---

## 参考资料

- SK Employment Standards Cheatsheet(项目文件 `sk_employment_standards_cheatsheet.docx`)
- SK 政府官网:https://www.saskatchewan.ca/business/employment-standards
- 法定假日工资计算器:https://apps.saskatchewan.ca/lrws/calculator/holidaypay/
- Vacation Pay 计算器:saskatchewan.ca/business/employment-standards/vacations-and-vacation-pay/vacation-pay-calculator

---

*本文档随实际工作中发现的新情况持续更新。修改时附上日期和原因。*
