# Future Phases — 推迟实施的功能

> 本文档记录**已识别需求、已讨论方案,但暂不立即实施**的功能。
> 等 `ARCHITECTURE.md` 中 Phase 1-4 的主线跑顺后,再从本文档取出执行。
>
> 启动任一 Phase 前,**必须重新核对主表当时的实际格式**,
> 不假设今天看到的结构稳定不变。如格式已变,先改本文档再改代码。

---

## 共同依赖:共享盘主表

两个 Phase 都读写同一个文件,Riley 称之为"那张狗屎 Excel":

**路径**:
```
\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\3. Task\{年份}\{年份.月份}\5. Payroll {年份.月份}.xlsx
```

**文件名规则**:
- 前缀的 `5.` 是公司内部任务编号(第 5 个任务),**不随月份变化**
- 下月文件名:`5. Payroll 2026.05.xlsx`

**主表结构**(截至 2026-04 样本):
- 多个 sheet,按 schedule 分:`Master` / `AB` / `SK` / `Sheet1` / `Biweekly` / `Monthly` / `Semi-Monthly` / `Quarterly` / `Yearly` / `UHT list`
- **Bi-weekly sheet**:每期 4 列(`Pay period Begin` / `Pay period End` / `Pay date` / `Initial/Date`),横向无限扩展
- **Monthly sheet**:每期 2 列(`{年.月}` 状态 / `Initial/Date`)
- **Semi-Monthly sheet**:每期 2 列(`{年.月.日}` 状态 / `Initial/Date`)
- **Quarterly / Yearly sheet**:按年/季度分列
- 日期单元格内容是 **Excel 日期序列号**(如 44923 = 2023-01-01 附近),不是格式化字符串

**主表已知坑**:
- 同一客户可能出现在多个 sheet(如 `Sample Nails` 在 Biweekly、Monthly、Semi-Monthly 均出现)
  → 更新规则:**只更新当期实际使用的那个 schedule 的行**(例如客户本月走 monthly,就只改 Monthly sheet 那行)
- 客户名在不同 sheet 写法不统一(`101234568 Saskatchewan Ltd. (Jane Doe) ~` vs `101234568 SK Ltd`)
  → 必须 fuzzy 匹配 + 首次匹配后人工确认并缓存
- 状态词自由发挥:`Done` / `Scheduled` / `New` / `ROE` / `Hold on` / `Pay` / `PD7A` / `No Semhar` / `Terminated` / `End services` / `Hold on`...
  → 本工具**只使用 `Done`**,不自造其它词
- Initial 列的首字母没有公司级对照表,各人用各人的(Riley = `R`)
  → 在 `config.yaml` 里配置当前使用者的首字母,默认 `R`
- 客户状态行可能标记 `Stopped` / `Terminated` / `End services` / `Hold on`
  → Phase A 必须识别并**排除**这些客户,不提醒
- 文件可能被同事打开占用
  → Phase A:用 openpyxl `read_only=True` 读取,通常能读;完全锁死时重试或跳过该次扫描
  → Phase B:写入前检测占用,占用则降级为生成手动更新片段

---

## Phase A — 客户到期提醒

### 核心目标

**在客户催我们之前,我们先联系客户**。这是老板原话要求的功能。

通过扫描主表,预测每个活跃客户下期 pay date,在合适的时间提前输出"该联系客户收 timesheet 了"的草稿消息。

### 输入

- 主表 `5. Payroll {当月}.xlsx`
- `config.yaml` 中:
  - 当前使用者首字母(如 `R`)
  - 提醒窗口(默认:提前 7 天普通提醒,提前 3 天升级为紧急)
  - 不提醒的客户列表(可选的强制排除,优先级高于主表状态识别)

### 输出

**每次扫描产出一个带日期的报告文件**:
```
payroll-assistant/reminders/{扫描日期}_reminder.md
```

报告内容示例:
```markdown
# 本周客户到期扫描 — 2026-04-17

## 🚨 已超期 (无应联系)
(空)

## ⚠️ 紧急 (≤3 天,若未收到 timesheet 立即联系)
- **Sample Nail Studio** (monthly)
  - 预计 pay date: 2026-04-30
  - 距今: 3 天
  - 上期已完成: 2026-03-31 (Initial: R)
  - → 消息草稿见下

## 📅 本周提醒 (≤7 天)
- **Sample Food Co.** (bi-weekly)
  - 预计 pay date: 2026-04-23
  - 距今: 6 天
  - 上期已完成: 2026-04-09 (Initial: D)
  - → 消息草稿见下

## 消息草稿

### Sample Nail Studio
**联系方式**:微信(主表 Notes 标注"微信")
**草稿**:
您好,本月工资即将结算(预计 4 月 30 日发放),方便的话请将本月员工
工时表发给我们,以便尽快处理。谢谢!

### Sample Food Co.
**联系方式**:微信或邮件
**草稿**:
...
```

### Windows Task Scheduler 触发

**建议触发节奏**:
- 每周一 08:00 — 本周扫描
- 每周三 08:00 — 补扫
- 每周五 08:00 — 收尾扫描

**实现方式**:
1. 写一个 `scripts/run_reminder_scan.py`(无需参数)
2. 配一个 `run_reminder_scan.bat`,内容大致:
   ```batch
   cd /d C:\path\to\payroll-assistant
   python scripts\run_reminder_scan.py
   ```
3. Windows Task Scheduler → Create Basic Task → Weekly → 周一/三/五 → Start a program → 选 .bat

**报告通知方式**:
- 首选:扫描脚本跑完后弹一个简单的 Windows 通知("本周有 X 家客户需要联系,报告已生成")
- 次选:脚本完成后自动打开报告 .md 文件
- **不发邮件/不自动联系客户**(见下)

### 消息草稿交付方式(Riley 已确认)

- **不自动发送给客户**(微信没 API,邮件可做但不做)
- 生成草稿文本 → 存在 `reminder.md` 里 → Riley 自己复制粘贴发
- **但邮件留程序化接口**:Phase A 代码中 `send_email()` 函数留桩,默认返回 `NotImplemented`,将来想开自动邮件时只需实现这个函数,不影响其它逻辑

### 排除客户的规则

以下情况**不**产生提醒:
- 主表中客户行注明 `Stopped` / `Terminated` / `End services`
- 客户行当前最近一期标注 `Hold on`(最近 3 期都 Hold on 认为是长期暂停)
- 客户行 Notes 列标注 `no employee`, `no payroll` 等
- `config.yaml` 显式配置为 `excluded_clients` 列表中的客户

### Schedule 解析规则

主表 Schedule 列的自由文本需映射到标准 schedule,用于推算下期 pay date:

| 主表原文(常见) | 标准 schedule | 下期 pay date 推算规则 |
|---|---|---|
| `bi-weekly` | biweekly | 上期 pay date + 14 天 |
| `Semi-monthly` | semimonthly | 当月 15 / 当月最后一天(或下月 15,取下一个) |
| `Monthly` | monthly | 当月最后一天 / 次月固定日(需从 Biweekly/Monthly sheet 实际列头推算) |
| `Quarterly` | quarterly | 下一季度末 |
| `Yearly` | yearly | 次年同期 |
| `15th of each month` | special_15th | 每月 15 日 |
| `bi-weekly + Monthly` / `Monthly + bi-weekly` | mixed | 两个都算,取最近一个 |

**无法解析的 schedule** → flag 为"需人工确认",不产生提醒,但记录在报告顶部警示。

### 上期 pay date 如何读取

- **Monthly sheet**:找到客户行,从右往左找最后一个状态 = `Done` 的列,其列头(`2026.04`)即上期 pay date(取当月末)
- **Bi-weekly sheet**:找客户行最后一组有效的 4 列,`Pay date` 列即上期
- **Semi-Monthly sheet**:同 Monthly,但列头是具体日期(`2026.04.15`)
- **兜底**:如果主表读不到,从客户 Payroll 文件夹的 QB PDF 读最近一期的 `Cheque Date`

### 留档

每次扫描写入一条 append-only 日志:
```
payroll-assistant/reminders/scan_log.jsonl
```

每行一个 JSON:
```json
{"scan_at":"2026-04-17T08:00:12","clients_scanned":164,"urgent":1,"normal":5,"over_due":0}
```

另外每次生成的 `{日期}_reminder.md` 文件本身就是历史留档,不删除。

### 不做什么

- 不对接邮件自动发送(虽留接口)
- 不对接微信
- 不生成老板要看的报告(老板不要)
- 不管理客户联系方式(Notes 列里有啥用啥,没有就在草稿里标注"未知联系方式,请补充")
- 不自动回复客户(仅单向提醒 Riley)

---

## Phase B — Payroll Tracker 主表自动写入

### 核心目标

每期 payroll 完成后,免除 Riley 打开主表手填 `Done + R` 的手动步骤。

### 启动前提

**必须先做 Phase A**。Phase A 已经解决了读主表的大部分基础问题(客户名匹配、schedule 解析、列定位),Phase B 只是在此基础上加一个写入动作。如果 Phase A 都没做,单独做 Phase B 意义不大且风险高。

### 工作流

在主线 Phase 2 步骤 6(Riley 照清单录入 QB)之后,新增可选动作:

```
Riley: "提交 {客户} {期号} 的 tracker 更新"

工具执行:
1. 定位主表文件并尝试只读打开
   - 若文件被占用 → 放弃写入,生成 tracker_update_manual.md 让 Riley 手填
2. 找客户本期使用的 schedule(Monthly / Bi-weekly / Semi-Monthly...)
   - 如果主表中该客户在多个 sheet 出现,只更新本期实际 schedule 对应的 sheet
3. fuzzy 匹配客户名定位到行
   - 匹配度 <95% → 放弃,让 Riley 手填(不赌)
   - 匹配到多行 → 放弃,让 Riley 手填
4. 定位到当期列
   - Monthly: 列头 = `{年份.月份}` 或相近
   - Bi-weekly: 按 pay date 匹配 4 列组的第 3 列
5. 确认目标单元格当前为空或为 "Scheduled"
   - 若已经有 "Done"、"New"、"Hold on" 等内容 → 放弃,让 Riley 手工核对
6. 先备份原文件到 payroll-assistant/backups/{时间戳}_{原文件名}
7. 写入:状态列 = "Done",Initial 列 = config.yaml 里的首字母(R)
8. 保存文件
9. 打印确认日志到 staging/{client}_{period}/tracker_commit.log
```

### 核心原则:宁可不写,不可写错

**任何一个不确定的环节都降级为"生成手填片段,不写主表"**。

降级情况产生的手填片段:
```
staging/{client}_{period}/tracker_update_manual.md

📋 请手动在 5. Payroll 2026.04.xlsx 里更新:

文件:5. Payroll 2026.04.xlsx
Sheet:Monthly
客户名(表中原始写法):Sample Nail Studio
找到该行后,定位到列 "2026.04" 及其右侧 "Initial/Date" 列,填入:
- 状态列:Done
- Initial 列:R

(本次未自动写入原因:fuzzy 匹配命中 2 行,不确定哪个是正确目标)
```

### 备份策略

- 每次写入前备份原文件到本地 `payroll-assistant/backups/`
- 保留最近 30 天备份,之后自动清理
- 备份文件名:`{YYYY-MM-DD_HHMMSS}_5. Payroll 2026.04.xlsx`

### 并发冲突处理

- 写入前检测文件占用(Windows 下 openpyxl 打开会抛异常或警告)
- 占用时不等待、不重试,直接降级为手填片段
- 不做锁定、不做队列

### 不做什么

- 不批量更新(一次只更一个客户一期)
- 不更新除 `状态` 和 `Initial` 外的其它列(备注、日期等一律不碰)
- 不重构主表(见下)
- 不删除或移动既有行

---

## 长期:主表重构(不主动做,等时机)

### 动机

主表当前结构有根本性问题:
- 横向无限扩展(每期 4 列或 2 列,已有 3+ 年积累)
- 同一客户跨 sheet 重复
- 日期用 Excel 序列号不直观
- 状态词无规范

### 理想结构

一张**长表**(long table),所有 schedule 共用:

| client_name | schedule | period_start | period_end | pay_date | status | assignee | notes |
|---|---|---|---|---|---|---|---|
| Sample Nail Studio | monthly | 2026-04-01 | 2026-04-30 | 2026-04-30 | Done | R | |
| Sample Food Co. | biweekly | 2026-04-06 | 2026-04-19 | 2026-04-26 | Pending | | |

优点:
- 加期只加行,不加列
- 筛选"本周谁负责哪些客户"、"R 这个月做了多少单"一个 filter 就出
- 日期用标准日期类型,可排序可计算
- 可加列(客户联系方式、优先级等)而不破坏结构

### 为什么不现在做

- 影响所有同事工作习惯,非 Riley 一人可决定
- 改完需重新培训、适应
- 现阶段 Riley 自身立足未稳,不宜推动公司级改动

### 什么时候可推动

当 Phase A 跑顺、老板看到"主动联系客户"的实际效果后,可以用如下话术提:

> "Phase A 提醒功能目前受限于主表格式,每次扫描要花 30 秒解析。
> 如果主表改成长表结构,扫描时间可降到 1 秒以内,且能支持更多自动化
> (比如自动生成月底工作总量报告)。我可以先做一个并行的长表视图(不
> 影响现有文件),验证一两个月后再讨论要不要切换。"

此时重构的理由**不是"表烂"**,而是**"为了支持已有价值的功能升级而重构"**。这是有力得多的叙事。

---

## 何时取出本文档的内容

**Phase A 取出时机**:
- `ARCHITECTURE.md` 的 Phase 1-2 已完成并跑顺
- 至少有 3 个客户已完整走通工作流,无明显 bug
- Riley 有精力投入 1-2 周做这块

**Phase B 取出时机**:
- Phase A 已跑顺至少 1 个月,主表读取逻辑稳定无误
- Riley 对写入共享盘主表的风险充分知情并同意

**取出方式**:
- 将对应 Phase 的章节从本文档移入 `ARCHITECTURE.md` 的 Phase 14 节
- 本文档保留该 Phase 的入口占位,标注"已启动,详见 ARCHITECTURE.md"
- 然后再开写代码

---

*End of Future Phases Document*
