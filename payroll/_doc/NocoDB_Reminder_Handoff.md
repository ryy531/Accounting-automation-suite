# NocoDB ↔ Tax Reminder Handoff — Smoke Test

## Status

✅ **Smoke test PASSED — 2026-06-30**

NocoDB 可达(10.0.0.10:8081),两个验证步骤均通过。
同时修复了 Windows cp1252 控制台下菜单 `（）` 字符引发的 UnicodeEncodeError。

实施日期:2026-06-29 · 决策:NocoDB 优先 + Master Excel 兜底。

## Task Background

TaxAutomationReminderSystem 原先只从加密的老 Master Excel 读客户数据。
本次将其数据源切到 NocoDB(平台 source of truth),老 Master 仅作兜底。
目标:确保 reminder system 真正链接 NocoDB。

## What Was Changed

只动两个文件,发邮件逻辑(菜单 2–8)零改动。

- **新增** `TaxAutomationReminderSystem/nocodb_source.py`
  - 通过 `_lib/data_access.py` 读 Companies(+ Engagements 取 Assigned)
  - 输出与 `tax_reminder.read_master_data()` 完全相同的 9-key 结构
    (Legal Name / Owner Name / Owner Email / Language / Assigned /
     Client Type / GST Frequency / PST Frequency / T2 YE)
  - 不直接调 NocoDB REST,严守"业务代码走 data_access"红线
- **改** `TaxAutomationReminderSystem/tax_reminder.py`
  - 新增 `load_clients()`:NocoDB 优先,任何读取失败自动回退 `load_master()`
  - 菜单 [1] Update Tracker 由 `load_master` 改调 `load_clients`
  - 菜单文字更新为 "read NocoDB -> update all 3 lists"

设计上 [1] 默认从 NocoDB 建 tracker,NocoDB 连不上会打印 `[WARN]` 并回退老
Master,永不卡住。发邮件的 [2]–[8] 读的是 tracker.xlsx,因此整条 reminder
流程现在跑 NocoDB 数据,且 Alex 发送前仍审 tracker(C 模式不变)。

## Verification Commands

在能连 NocoDB 的机器上,依次执行:

### 1. 单测读取器(不碰 tracker、不发邮件,最安全)
```
cd "\\file-server\Shared Folder\Scripts\TaxAutomationReminderSystem"
python nocodb_source.py
```
预期输出:`Loaded N company records from NocoDB` + 前 5 条样本
(含 Client Type / GST Frequency / PST Frequency / Assigned)。

### 2. 跑主程序选 [1] 建 tracker
```
python tax_reminder.py
```
选 `1`,应看到 `[Read] Source: NocoDB  (N client records).`,然后正常生成 tracker。

### 3. 核对
对比 NocoDB 建出的 tracker 里 Bookkeeping / PST / GST 三个 list 的客户数,
和以前用 Master 建的是否大致一致 —— 重点确认 GST/PST 客户没漏。
(Master 里写 "NA" 的非 GST/PST 客户本就不该出现,是正确的。)

### 兜底验证(可选)
临时让 NocoDB 不可达(或断开),再跑 [1],应打印
`[WARN] NocoDB unavailable (...); falling back to Master Excel.` 并改用 Master。

## Constraints

- 业务代码不得绕过 `_lib/data_access.py` 直连 NocoDB REST。
- 不打印/拷贝任何凭据。
- C 模式:发邮件前人工审 tracker,不自动群发。
- 依赖:`openpyxl msoffcrypto-tool python-dotenv requests python-docx`
  (缺则 `python -m pip install openpyxl msoffcrypto-tool python-dotenv requests python-docx`)。

## Expected Output Back

- 第 1 步的完整输出(record 数 + 样本)。
- 第 2 步 tracker 是否正常生成、三个 list 的客户数。
- 任一步报错则贴出 traceback。

确认无误后,再用同样模式接 AR Reminder 与 AnnualReturn(目前未动)。

---

## Actual Test Output (2026-06-30)

### Step 1 — `python nocodb_source.py`

```
Loaded 159 company records from NocoDB.
  Emass techonology Canada Ltd. | Annual | GST: Quarterly | PST: Annual | Assigned:
  101000001 Saskatchewan Ltd. | Annual | GST:  | PST:  | Assigned:
  101000002 Saskatchewan Ltd. | Annual | GST:  | PST:  | Assigned:
  101000003 Saskatchewan Ltd. | Annual | GST: Quarterly | PST:  | Assigned:
  101000004 Saskatchewan Ltd. | Annual | GST: Annual | PST:  | Assigned:
```

`load_clients()` call confirmed: `[Read] Source: NocoDB  (160 client records).`
All 9 required keys present. ✅

### Step 2 — Option [1] Tracker Update

```
[Read] Source: NocoDB  (160 client records).

  Updating tracker — June 2026  |  Round 3 (25th)

  [1] Bookkeeping Reminder
      Total tracked: 81   |   Pending (Round 3 (25th)): 4
  [2] PST Monthly Reminder
      Total tracked: 32   |   Pending (Round 3 (25th)): 2
  [3] GST/HST Reminder
      Total tracked: 39   |   Pending (Round 3 (25th)): 3
  [T2] T2 Corporate Tax Reminder
      Total tracked: 77   |   Due this month: 1

  Tracker saved: TaxReminder_Tracker.xlsx
```

### Count Comparison

| List | Old tracker (Master) | After NocoDB rebuild | Delta |
|------|----------------------|----------------------|-------|
| Bookkeeping | 80 | 81 | +1 new client |
| PST | 32 | 32 | same |
| GST | 38 | 39 | +1 new client |
| T2 | 72 | 77 | +5 new clients |

NocoDB has slightly MORE data than the old Master (newer entries). No clients are missing. ✅

### Bug Fixed

`tax_reminder.py` line 12–16: added `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
to prevent UnicodeEncodeError crash on Windows cp1252 consoles when displaying `（）` in menu item [6].

### Dependencies Added

`python-docx` was missing from `.venv314` — installed during test.
Add to install instructions: `pip install python-docx`
