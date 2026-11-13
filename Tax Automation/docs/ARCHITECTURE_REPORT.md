# Tax Automation 架构现状报告

> 生成日期：2026-05-04（更新自 2026-04-20 版本）
> 项目路径：`Z:\Scripts\Tax Automation`
> 共享库路径：`Z:\Scripts\_lib`

---

## 1. 项目概览

### 1.1 技术栈

| 项目 | 值 |
|------|-----|
| 语言 | Python 3.14 |
| AI 服务 | Google Gemini (gemini-3.1-pro-preview) + OpenAI (gpt-5.4) fallback |
| 邮件 | SMTP/IMAP (Hostinger) |
| 数据处理 | pandas + openpyxl |
| 模糊匹配 | rapidfuzz |
| 浏览器自动化 | Selenium (TaxFolder 下载) |
| 加密 | cryptography (Fernet — API key 加密存储) |
| 文档处理 | python-docx, mammoth, msoffcrypto-tool |

### 1.2 第三方依赖 (requirements.txt)

```
pandas>=2.0          # DataFrame 数据处理
openpyxl>=3.1        # Excel 读写
rapidfuzz>=3.0       # 模糊字符串匹配
msoffcrypto-tool>=0.9 # 加密 Excel 解密
google-genai>=1.0    # Gemini AI SDK
mammoth>=1.6         # .docx → HTML 转换
python-docx>=1.0     # .docx 模板读取
cryptography>=42.0   # Fernet 加密 (API keys)
openai>=1.0          # OpenAI SDK
selenium>=4.18       # 浏览器自动化
```

### 1.3 目录结构

```
Tax Automation/
├── src/
│   ├── tax_automation.py          (299行) 主菜单入口
│   ├── config.py                  (223行) 全局配置
│   ├── create_folders.py          (263行) Menu 1: 创建客户文件夹
│   ├── ai_extract_info.py         (1331行) Menu 2: AI 信息提取
│   ├── ai_rename.py               (487行) Menu 3: AI 文件重命名
│   ├── missing_client_reminder.py (682行) Menu 4: 往年客户提醒
│   ├── send_package_email.py      (1261行) Menu 5: 发送税务包裹邮件
│   ├── send_reminder_email.py     (438行) Menu 6: 发送催补材料提醒
│   ├── send_efile_email.py        (639行) Menu 7: 发送 efile 确认邮件
│   ├── scan_email_replies.py      (682行) Menu 4 子功能: 扫描邮件回复
│   ├── taxfolder_download.py      (1415行) Menu 9: TaxFolder 下载 & Tracker
│   ├── send_signpay_reminder.py   (657行) Menu 9-4: 签字付款催提醒
│   ├── fix_intake.py              (451行) 独立工具: 修复 Intake 文件夹
│   ├── name_mapping.py            (263行) 独立工具: 客户名映射
│   ├── client_lookup.py           (172行) 独立工具: 客户主表查询
│   └── rename_cra_files.py        (198行) 独立工具: CRA 文件重命名
│
├── data/
│   ├── keys/                      # API keys (.enc), credentials.json
│   ├── working/                   # 输入/输出数据文件 (Excel, CSV)
│   └── logs/                      # JSON 日志文件
│
├── docs/
│   ├── Info Template.xlsx         # Menu 2 使用的字段模板
│   └── PERSONALTAXCLIENTINTAKE.xlsx
│
└── requirements.txt

_lib/                              (共享库 — 尚未集成)
├── config/
│   ├── settings.py                # .env 配置加载
│   └── .env                       # 环境变量 (API keys, 邮件凭据)
├── connectors/
│   ├── ai_client.py               # 统一 AI 客户端 (Gemini + OpenAI fallback)
│   └── email_client.py            # 统一 IMAP + SMTP 邮件客户端
├── utils/
│   └── logger.py                  # JSON 日志 + 去重
└── tests/
    └── test_ai_client.py          # AI 客户端单元测试 + smoke test
```

---

## 2. 菜单 → 功能映射

| 菜单 | 功能 | 入口函数 | 源文件 | 行数 |
|------|------|----------|--------|------|
| 1 | 创建客户文件夹 | `create_folders()` | create_folders.py | 263 |
| 2 | AI 税务信息提取 | `ai_extract_info()` | ai_extract_info.py | 1331 |
| 3 | AI 文件重命名 | `ai_rename()` | ai_rename.py | 487 |
| 4 | 往年客户提醒 | `missing_client_reminder()` | missing_client_reminder.py | 682 |
| 5 | 发送税务包裹邮件 | `send_package_email()` | send_package_email.py | 1261 |
| 6 | 催补材料提醒 | `send_reminder_email()` | send_reminder_email.py | 438 |
| 7 | Efile 确认邮件 | `send_efile_email()` | send_efile_email.py | 639 |
| 8 | PayPal 付款链接 | — | *(未实现)* | — |
| 9-1 | TaxFolder 下载 + Tracker | `download_taxfolder()` | taxfolder_download.py | 1415 |
| 9-2 | 刷新 Tracker (用已有 CSV) | `generate_sign_pay_tracker(csv_path)` | taxfolder_download.py | |
| 9-3 | 移动已完成客户 → To efile | `move_ready_to_efile()` | taxfolder_download.py | |
| 9-4 | 签字/付款催提醒邮件 | `send_signpay_reminder()` | send_signpay_reminder.py | 657 |

**独立工具（不在菜单中）：**

| 工具 | 入口函数 | 源文件 | 用途 |
|------|----------|--------|------|
| 修复 Intake | `main()` → `scan_intake()` | fix_intake.py | 修复客户文件夹结构 |
| 客户名映射 | `build_mapping()` | name_mapping.py | TaxCycle ↔ 文件夹名映射 |
| 客户查询 | `lookup_client()` | client_lookup.py | 在 Master Client List 中查找 |
| CRA 重命名 | `rename_cra_files()` | rename_cra_files.py | CRA 文件批量重命名 |

---

## 3. 模块函数清单

### 3.1 config.py (223行) — 全局配置

**常量分组：**

| 分组 | 关键配置项 |
|------|-----------|
| 路径 | `BASE_WORKFLOW_PATH`, `SCRIPTS_DATA_PATH`, `SCRIPTS_KEYS_PATH`, `SCRIPTS_WORKING_PATH`, `SCRIPTS_LOGS_PATH` |
| 工作流文件夹 | `WORKFLOW_FOLDERS_TO_SCAN` (2-9阶段), `TEMPLATE_FOLDER_NAME`, `INTAKE_FOLDER_NAME` |
| 数据文件 | `NEW_CLIENT_FILE`, `TAXCYCLE_EXPORT`, `MAPPING_FILE`, `TAXFOLDER_EXPORT`, `SIGN_PAY_TRACKER`, `PAID_EXPORT` |
| AI | `GEMINI_MODEL`, `GEMINI_RPM`, `GEMINI_MAX_FILE_MB`, `OPENAI_MODEL` |
| 邮件 | `CREDENTIALS_FILE`, `EMAIL_LOG_FILE`, 邮件模板路径, 邮件主题模板 |
| Menu 2 | `CORE_FIELD_CODES` (35个字段), `INFO_TEMPLATE_PATH`, `INTAKE_FILE_EXTENSIONS` |
| Menu 9 | `TAXFOLDER_MATCH_THRESHOLD`, `TO_EFILE_PATH`, `CURRENT_TAX_YEAR` |
| 客户主表 | `CLIENT_MASTER_FILE`, `CLIENT_MASTER_PASSWORD`, `CLIENT_MASTER_NAME_COLS`, `CLIENT_MASTER_DISPLAY_COLS` |

**特殊逻辑：** 通过 `importlib.util` 动态加载 `_lib/config/settings.py` 获取 `CLIENT_MASTER_PASSWORD`（避免命名冲突）。

---

### 3.2 tax_automation.py (299行) — 主菜单入口

| 函数 | 签名 | 职责 |
|------|------|------|
| `clear()` | `()` | 清屏 |
| `check_tax_year()` | `()` | 启动时检查/更新 TAX_YEAR |
| `run_create_folders()` | `()` | Menu 1 入口 |
| `run_ai_extract_info()` | `()` | Menu 2 入口 |
| `run_rename_cra()` | `()` | Menu 3 入口 |
| `run_missing_client_reminder()` | `()` | Menu 4 入口 |
| `run_send_package_email()` | `()` | Menu 5 入口 |
| `run_send_efile_email()` | `()` | Menu 7 入口 |
| `run_taxfolder_download()` | `()` | Menu 9 入口 (含子菜单) |
| `run_send_reminder_email()` | `()` | Menu 6 入口 |
| `coming_soon()` | `(name: str)` | 未实现功能占位 |
| `main()` | `()` | 主循环 |

---

### 3.3 create_folders.py (263行) — Menu 1

| 函数 | 签名 |
|------|------|
| `_clean()` | `(val) -> str` |
| `build_folder_name()` | `(row: pd.Series) -> str` |
| `parse_kids()` | `(kids_cell) -> list[str]` |
| `copy_template()` | `(dest_path: str)` |
| `get_person_names()` | `(row: pd.Series) -> list[str]` |
| `create_intake_person_folders()` | `(client_path: str, persons: list[str])` |
| `create_child_folders()` | `(parent_path: str, children: list[str])` |
| `create_folders()` | `()` ← 主入口 |

**依赖：** config

---

### 3.4 ai_extract_info.py (1331行) — Menu 2

| 函数 | 签名 |
|------|------|
| `_load_field_specs()` | `() -> dict[str, dict]` |
| `_build_field_list_text()` | `(specs: dict) -> str` |
| `_check_returning_client()` | `(client_name: str) -> list[dict]` |
| `_list_client_folders()` | `() -> list[str]` |
| `_select_client()` | `() -> str \| None` |
| `_parse_person_results()` | `(parsed, file_name: str) -> dict` |
| `_extract_from_file()` | `(file_path, client_name, person_key, rate_limiter, ...) -> dict` |
| `_load_openai_key()` | `() -> str \| None` |
| `_save_openai_key()` | `(key: str) -> None` |
| `_setup_openai_key()` | `() -> str \| None` |
| `_extract_from_file_openai()` | `(file_path, client_name, ...) -> dict` |
| `_make_person_key()` | `(person_fields: dict, label: str) -> str` |
| `_merge_fields()` | `(entries: list[dict]) -> dict` |
| `_merge_results()` | `(per_file_results: list) -> dict` |
| `_get_output_dir()` | `(client_folder: str) -> str` |
| `_write_info_templates()` | `(output_dir, client_name, merged, ...) -> list` |
| `_write_source_report()` | `(output_dir, client_name, file_results, ...)` |
| `_write_missing_report()` | `(output_dir, client_name, merged, total_fields)` |
| `_log_run()` | `(client_name, merged, total_fields)` |
| `ai_extract_info()` | `() <- 主入口` |

**依赖：** config, ai_rename (RateLimiter, prepare_parts, load_api_key, setup_api_key 等)

---

### 3.5 ai_rename.py (487行) — Menu 3

| 函数 | 签名 |
|------|------|
| `_machine_fernet()` | `()` |
| `load_api_key()` | `()` |
| `save_api_key()` | `(key: str)` |
| `setup_api_key()` | `()` |
| `collect_files()` | `(folder: str) -> list` |
| `_prepare_image()` | `(path: str) -> list` |
| `_prepare_pdf()` | `(path: str) -> list` |
| `_prepare_docx()` | `(path: str) -> list` |
| `_prepare_xlsx()` | `(path: str) -> list` |
| `prepare_parts()` | `(path: str) -> list` |
| `RateLimiter` | `class` (RPM 限速器) |
| `_build_content()` | `(path: str)` |
| `_parse_response()` | `(text: str) -> dict` |
| `_is_quota_error()` | `(exc: Exception) -> bool` |
| `_is_fatal_error()` | `(exc: Exception) -> bool` |
| `analyze_file()` | `(client, path: str, rate_limiter) -> dict` |
| `sanitize()` | `(s: str, max_len: int = 30) -> str` |
| `build_new_name()` | `(result: dict, original_path, used_names: set) -> str` |
| `ai_rename()` | `() ← 主入口` |

**依赖：** config

---

### 3.6 missing_client_reminder.py (682行) — Menu 4

| 函数 | 签名 |
|------|------|
| `_norm_sin()` | `(val) -> str` |
| `_norm_name()` | `(val) -> str` |
| `_valid_email()` | `(val) -> bool` |
| `find_year_folder()` | `(year: int) -> str \| None` |
| `load_t1_list()` | `(folder: str) -> pd.DataFrame \| None` |
| `build_current_index()` | `(df: pd.DataFrame)` |
| `scan_folder_names()` | `(year_folder: str) -> list` |
| `find_missing_in_df()` | `(df, sin_index, name_index, existing_folders, ...)` |
| `deduplicate()` | `(all_missing: list) -> list` |
| `_load_old_states()` | `(path: str) -> dict` 🆕 |
| `write_missing_list()` | `(missing: list, old_path: str = None) -> str` |
| `open_in_excel()` | `(path: str)` |
| `generate_missing_list()` | `()` |
| `_load_template()` | `()` |
| `_fill_template()` | `(html, client_name) -> str` |
| `_build_message()` | `(client_name, recipient, body_html, inline_images, creds)` |
| `_load_log()` | `() -> dict` |
| `_save_log()` | `(log: dict)` |
| `_add_log_entry()` | `(log, name, email)` |
| `_update_excel_sent_at()` | `(path, sent_names: list)` |
| `send_missing_reminders()` | `()` |
| `missing_client_reminder()` | `() ← 主入口` |

**依赖：** config, send_package_email (extract_cid_images, load_credentials, send_one), scan_email_replies (scan_replies, create_folders_from_replies)

---

### 3.7 send_package_email.py (1261行) — Menu 5

| 函数 | 签名 |
|------|------|
| `parse_person_names()` | `(folder_name: str) -> list` |
| `scan_taxcycle_pdfs()` | `(client_folder: str) -> list` |
| `_norm()` | `(s: str) -> str` |
| `group_pdfs_by_person()` | `(pdfs, persons) -> tuple` |
| `_extract_name_from_filename()` | `(filename: str) -> str` |
| `group_unmatched_by_child()` | `(unmatched: list) -> dict` |
| `_reunite_renamed_persons()` | `(persons, groups, unmatched) -> list` 🆕 |
| `map_filename()` | `(filename: str) -> str` |
| `get_signature_docs()` | `(mapped_names: list) -> list` |
| `find_t1_pdf()` | `(pdfs, person_name, all_pdfs) -> str` |
| `_load_gemini_key()` | `() -> str` |
| `_machine_fernet()` | `()` |
| `_load_openai_key()` | `() -> str` |
| `_save_openai_key()` | `(key: str)` |
| `_setup_openai_key()` | `() -> str` |
| `extract_t1_info()` | `(gemini_client, t1_path: str) -> dict` |
| `_extract_t1_info_openai()` | `(t1_path: str, data: bytes) -> dict` |
| `read_template()` | `() -> str` |
| `extract_subject()` | `(html: str) -> tuple` |
| `extract_cid_images()` | `(html_str: str) -> tuple` |
| `build_review_text()` | `(person_name, fields) -> str` |
| `parse_review_text()` | `(text, keys) -> dict` |
| `build_amount_sentence()` | `(amount_type, amount) -> str` |
| `_sig_docs_str()` | `(sig_docs: list) -> str` |
| `fill_template()` | `(html, client_name, person_name, fields, ...)` |
| `edit_in_notepad()` | `(text: str) -> str` |
| `load_credentials()` | `()` |
| `build_message()` | `(sender, recipients, subject, html_body, inline_images, ...)` |
| `_imap_save_sent()` | `(raw_bytes, creds) -> None` |
| `send_one()` | `(msg, recipients, creds) -> tuple` |
| `move_to_sent()` | `(client_folder: str)` |
| `process_person()` | `(client_folder, folder_name, person_name, ...)` |
| `send_package_email()` | `() ← 主入口` |

**依赖：** config

**被导出的函数：** `load_credentials`, `send_one`, `build_message`, `extract_cid_images`, `scan_taxcycle_pdfs`, `map_filename`, `get_signature_docs`, `find_t1_pdf`, `extract_t1_info`, `_load_gemini_key`, `_setup_openai_key`, `parse_person_names`

---

### 3.8 send_reminder_email.py (438行) — Menu 6

| 函数 | 签名 |
|------|------|
| `_load_log()` | `()` |
| `_save_log()` | `(log)` |
| `_init_gemini()` | `()` |
| `_load_credentials()` | `()` |
| `_parse_person_names()` | `(folder_name)` |
| `_find_missing_items()` | `(folder_path)` |
| `_format_missing_info()` | `(items)` |
| `_find_info_pdf()` | `(taxcycle_dir, person_name)` |
| `_extract_email_from_pdf()` | `(gemini_client, pdf_path)` |
| `_read_template()` | `()` |
| `_fill_template()` | `(html, client_name, missing_html)` |
| `_build_review_text()` | `(folder_name, person_name, missing_info, email_addr)` |
| `_parse_review_text()` | `(text, keys)` |
| `_edit_in_notepad()` | `(text)` |
| `_build_message()` | `(sender, recipient, subject, html_body)` |
| `_send_email()` | `(msg, creds, recipient)` |
| `send_reminder_email()` | `() ← 主入口` |

**依赖：** config, send_package_email (_load_gemini_key, parse_person_names)

---

### 3.9 send_efile_email.py (639行) — Menu 7

| 函数 | 签名 |
|------|------|
| `_log_path()` | `() -> str` |
| `_followup_path()` | `() -> str` |
| `_csv_path()` | `() -> str` |
| `_load_sent_clients()` | `() -> set` |
| `_init_gemini()` | `()` |
| `_load_credentials()` | `()` |
| `_read_template()` | `() -> str` |
| `_fill_template()` | `(html, client_name) -> str` |
| `_find_all_receipts()` | `(folder_path: str) -> list` |
| `_person_from_receipt()` | `(filename: str) -> str` |
| `_find_t1_pdf()` | `(pdfs, person_name) -> str` |
| `_generate_review_csv()` | `(batch, csv_path)` |
| `_build_notes()` | `(item: dict) -> str` |
| `_open_in_excel()` | `(csv_path)` |
| `_read_review_csv()` | `(csv_path, original_batch) -> list` |
| `_resolve_file()` | `(folder_path, filename) -> str` |
| `_build_message()` | `(sender, recipients, subject, ...)` |
| `_send_email()` | `(msg, creds, recipients)` |
| `send_efile_email()` | `() ← 主入口` |
| `_skip_reason()` | `(item: dict) -> str` |
| `_email_str()` | `(item: dict) -> str` |
| `_write_followup()` | `(skipped: list)` |

**依赖：** config, send_package_email (extract_t1_info, extract_cid_images, _load_gemini_key)

---

### 3.10 taxfolder_download.py (1415行) — Menu 9

| 函数 | 签名 |
|------|------|
| `_load_or_prompt_credentials()` | `()` |
| `_setup_driver()` | `(download_dir: str)` |
| `_wait_for_table_ready()` | `(driver, slow_wait, By, EC)` |
| `_wait_for_download()` | `(download_dir, before, timeout) -> str` |
| `download_taxfolder()` | `() ← Menu 9-1` |
| `_parse_folder_persons()` | `(folder_name: str) -> list` |
| `_check_local_signature()` | `(sent_path, folder_name) -> dict` |
| `_run_with_timeout()` | `(fn, timeout_sec, *args, **kwargs)` |
| `_gemini_call()` | `(api_key, model, parts, prompt)` |
| `_openai_call()` | `(api_key, parts, prompt)` |
| `_call_ai_vision()` | `(file_path, prompt) -> dict` |
| `_ai_verify_signature()` | `(file_paths, limiter) -> str` |
| `_load_member_names()` | `() -> list` 🆕 |
| `_match_members_to_folders()` | `(member_names, folder_persons, threshold) -> set` 🆕 |
| `_load_paid_export()` | `() -> list` |
| `_match_paid_to_folders()` | `(paid_invoices, folder_persons, ...)` |
| `generate_sign_pay_tracker()` | `(csv_path: str) ← Menu 9-2` |
| `move_ready_to_efile()` | `() ← Menu 9-3` |

**依赖：** config, ai_rename (load_api_key, prepare_parts, RateLimiter)

---

### 3.11 send_signpay_reminder.py (657行) — Menu 9-4

| 函数 | 签名 |
|------|------|
| `_read_signpay_template()` | `() -> str` |
| `_identify_blocks()` | `(html: str) -> dict` |
| `_build_email_body()` | `(template, client_name, ...)` |
| `_read_tracker()` | `() -> tuple` |
| `_get_email_for_client()` | `(client_name, tracker_df, ...)` |
| `_get_signing_docs_for_client()` | `(folder_path, client_name) -> list` |
| `_update_tracker_after_send()` | `(...)` |
| `_write_log()` | `(results: list) -> None` |
| `send_signpay_reminder()` | `() <- 主入口` |

**依赖：** config, send_package_email (load_credentials, send_one, build_message, extract_cid_images, scan_taxcycle_pdfs, map_filename, get_signature_docs, find_t1_pdf, extract_t1_info, _load_gemini_key, _setup_openai_key, parse_person_names)

---

### 3.12 scan_email_replies.py (682行) — Menu 4 子功能

| 函数 | 签名 |
|------|------|
| `_connect_imap()` | `(creds: dict) -> imaplib.IMAP4_SSL` |
| `_extract_body_text()` | `(msg: email.message.Message) -> str` |
| `_fetch_replies_since()` | `(imap, since_date) -> list` |
| `_close_imap()` | `(imap)` |
| `_norm_subject()` | `(s: str) -> str` |
| `_match_reply_to_client()` | `(...)` |
| `_deduplicate_replies()` | `(replies: list) -> list` |
| `_classify_reply()` | `(client_name, reply_body) -> dict` |
| `_load_reply_log()` | `() -> dict` |
| `_save_reply_log()` | `(log: dict)` |
| `_ensure_reply_columns()` | `(ws) -> bool` |
| `_apply_row_color()` | `(ws, row_idx, status)` |
| `_update_excel_replies()` | `(path, results)` |
| `scan_replies()` | `()` |
| `_parse_client_name()` | `(full_name: str) -> dict` |
| `create_folders_from_replies()` | `()` |

**依赖：** config, ai_rename (setup_api_key), send_package_email (load_credentials), create_folders (create_folders)

---

### 3.13 独立工具

#### fix_intake.py (451行)

| 函数 | 签名 |
|------|------|
| `normalize_name()` | `(name: str) -> str` |
| `_leading_num()` | `(name: str) -> str` |
| `_find_existing_by_num()` | `(client_path, num) -> list[str]` |
| `audit_subfolders()` | `(client_path)` |
| `create_subfolder()` | `(client_path, canonical)` |
| `get_client_intake_path()` | `(client_path) -> str \| None` |
| `parse_persons_from_folder_name()` | `(folder_name) -> list[str]` |
| `audit_person_folders()` | `(intake_subfolder_path, persons)` |
| `create_person_folders()` | `(intake_subfolder_path, persons)` |
| `find_empty_duplicates()` | `(client_path, num, names) -> list[str]` |
| `scan_intake()` | `()` |
| `preview()` | `(plan: list)` |
| `apply()` | `(plan: list) -> list[str]` |
| `main()` | `() <- 入口` |

**依赖：** config

#### name_mapping.py (263行)

| 函数 | 签名 |
|------|------|
| `scan_client_folders()` | `() -> list[str]` |
| `parse_folder_name()` | `(folder: str) -> dict` |
| `folder_search_names()` | `(folder: str) -> list[str]` |
| `load_taxcycle()` | `(path: str) -> pd.DataFrame` |
| `best_folder_match()` | `(tc_name, folder_parsed) -> tuple[str, int]` |
| `build_mapping()` | `() <- 入口` |
| `_format_mapping_excel()` | `(path, rows: list[dict])` |

**依赖：** config

#### client_lookup.py (172行)

| 函数 | 签名 |
|------|------|
| `load_master()` | `()` |
| `build_name_index()` | `(df)` |
| `print_match()` | `(df, row_idx, matched_name, score, source_col)` |
| `lookup_client()` | `() <- 入口` |

**依赖：** config

#### rename_cra_files.py (198行)

| 函数 | 签名 |
|------|------|
| `identify_cra_file()` | `(filename)` |
| `extract_client_prefix()` | `(folder_name)` |
| `extract_prefix_from_cra_folder()` | `(cra_folder_name)` |
| `build_rename_plan()` | `(client_folder_path, folder_name)` |
| `rename_cra_files()` | `() <- 入口` |

**依赖：** config

---

## 4. 模块间调用关系（依赖图）

```
                    config.py
                   ╱    │    ╲
                  ╱     │     ╲
    ai_rename.py ╱      │      ╲ create_folders.py
         │       ╱      │       ╲ fix_intake.py
         │      ╱       │        ╲ name_mapping.py
         │     ╱        │         ╲ client_lookup.py
         │    ╱         │          ╲ rename_cra_files.py
         ▼   ▼          ▼           ▼
    ┌─────────────────────────────────────────┐
    │         send_package_email.py            │
    │  (核心枢纽 — 被最多模块导入)              │
    └────┬──────┬──────┬──────┬───────────────┘
         │      │      │      │
         ▼      ▼      ▼      ▼
     send_   send_   send_   missing_client
     reminder efile   signpay  _reminder.py
     _email  _email  _reminder
     .py     .py     .py
                       │
                       ▼
                 scan_email_replies.py
                       │
                       ▼
                 create_folders.py (create_folders)

    ai_extract_info.py ←── ai_rename.py
    taxfolder_download.py ←── ai_rename.py
```

### 详细依赖表

| 模块 | 导入自 |
|------|--------|
| **config.py** | *(无依赖 — 被所有模块导入)* |
| **ai_rename.py** | config |
| **create_folders.py** | config |
| **fix_intake.py** | config |
| **name_mapping.py** | config |
| **client_lookup.py** | config |
| **rename_cra_files.py** | config |
| **send_package_email.py** | config |
| **ai_extract_info.py** | config, **ai_rename** |
| **taxfolder_download.py** | config, **ai_rename** |
| **send_reminder_email.py** | config, **send_package_email** |
| **send_efile_email.py** | config, **send_package_email** |
| **send_signpay_reminder.py** | config, **send_package_email** |
| **missing_client_reminder.py** | config, **send_package_email**, **scan_email_replies** |
| **scan_email_replies.py** | config, **ai_rename**, **send_package_email**, **create_folders** |

### 关键观察

- **send_package_email.py 是事实上的"公共库"** — 6 个模块从它导入函数（13 个导出函数）
- **ai_rename.py 是 AI 工具库** — 提供 RateLimiter, prepare_parts, load_api_key 等
- **config.py 通过 importlib 加载 _lib/settings.py** — 唯一与 _lib 的连接点

---

## 5. 共享库 `_lib/` 状态

### 5.1 已实现模块

| 模块 | 行数 | 功能 | 状态 |
|------|------|------|------|
| `_lib/config/settings.py` | 90 | .env 配置加载 | ✅ 完整 |
| `_lib/utils/logger.py` | 98 | JSON 日志 + 去重 | ✅ 完整 |
| `_lib/connectors/email_client.py` | 215 | 统一 IMAP + SMTP | ✅ 完整 |
| `_lib/connectors/ai_client.py` | 565 | Gemini + OpenAI fallback | ✅ 完整 |
| `_lib/tests/test_ai_client.py` | 606 | AI 客户端测试 | ✅ 完整 |

### 5.2 集成状态：**未集成**

- _lib 已实现 4 个公共模块 + 1 个测试套件
- **Tax Automation 的业务模块全部未导入 _lib**
- config.py 仅通过 `importlib.util` 加载 settings.py 获取密码
- 各业务模块仍在使用内联的 SMTP/IMAP 代码、内联的 AI 调用逻辑

### 5.3 重复功能对照

| 功能 | _lib 提供 | 业务模块中的内联实现 |
|------|-----------|---------------------|
| AI 调用 (Gemini + OpenAI) | `ai_client.py` | ai_rename.py, ai_extract_info.py, send_package_email.py, send_efile_email.py, taxfolder_download.py |
| 邮件发送 (SMTP) | `email_client.py` | send_package_email.py, send_reminder_email.py, send_efile_email.py, missing_client_reminder.py |
| 邮件读取 (IMAP) | `email_client.py` | scan_email_replies.py, send_package_email.py |
| 配置管理 | `settings.py` (.env) | config.py (硬编码常量) |
| JSON 日志 | `logger.py` | 各模块自行实现 JSON log 读写 |
| API Key 管理 | `settings.py` (.env) | ai_rename.py, send_package_email.py (Fernet 加密文件) |

---

## 6. 配置与数据文件

### 6.1 配置文件

| 文件 | 位置 | 内容 |
|------|------|------|
| `config.py` | src/ | 所有路径常量、AI 设置、邮件配置、字段定义 |
| `.env` | _lib/config/ | API keys、邮件凭据、主表密码 |

### 6.2 数据文件

| 文件 | 路径 | 用途 |
|------|------|------|
| `NewClient.xlsx` | data/working/ | 新客户列表 (Menu 1 输入) |
| `client_mapping.xlsx` | data/working/ | 客户名映射表 |
| `TaxCycle_Export.xlsx` | data/working/ | TaxCycle 数据导出 |
| `taxfolder_export.csv` | data/working/ | TaxFolder 客户导出 |
| `sign_pay_tracker.xlsx` | data/working/ | 签字付款跟踪表 |
| `paid_export.xlsx` | data/working/ | QB 已付款发票导出 |
| `missing_clients_2025.xlsx` | data/working/ | 缺失客户列表 |
| `member_names_cache.json` | data/working/ | B/B+ 会员名缓存 🆕 |

### 6.3 日志文件

| 文件 | 路径 | 对应功能 |
|------|------|----------|
| `email_log.json` | data/logs/ | Menu 5 邮件发送记录 |
| `reminder_log.json` | data/logs/ | Menu 6 催补提醒记录 |
| `ai_extract_log.json` | data/logs/ | Menu 2 AI 提取记录 |
| `missing_clients_log.json` | data/logs/ | Menu 4 缺失客户记录 |
| `reply_scan_log.json` | data/logs/ | 邮件回复扫描记录 |
| `signpay_reminder_log.json` | data/logs/ | Menu 9-4 签字付款催提醒记录 |
| `bb_client_reminder_log.json` | data/logs/ | B/B+ 客户提醒记录 🆕 |

### 6.4 凭据文件

| 文件 | 路径 | 内容 |
|------|------|------|
| `gemini_key.enc` | data/keys/ | Fernet 加密的 Gemini API key |
| `openai_key.enc` | data/keys/ | Fernet 加密的 OpenAI API key |
| `credentials.json` | data/keys/ | 邮箱凭据 (JSON) |

---

## 7. 外部服务与 API 调用

| 服务 | 用途 | 使用模块 |
|------|------|----------|
| Google Gemini AI | 文档提取、分类、T1 解析 | ai_rename, ai_extract_info, send_package_email, send_efile_email, taxfolder_download, send_reminder_email |
| OpenAI API | Gemini 的 fallback | ai_extract_info, send_package_email, taxfolder_download |
| SMTP (Hostinger) | 发送邮件 | send_package_email, send_reminder_email, send_efile_email, missing_client_reminder |
| IMAP (Hostinger) | 读取邮件回复 | scan_email_replies, send_package_email (save to Sent) |
| TaxFolder (Web) | 下载客户数据 CSV | taxfolder_download (Selenium) |
| Windows 文件系统 | 文件夹操作 | 所有模块 |

---

## 8. 04-20 → 05-04 变更摘要

### 行数变化

| 文件 | 旧行数 | 新行数 | 变化 |
|------|--------|--------|------|
| config.py | 224 | 223 | -1 |
| send_package_email.py | 1221 | 1261 | +40 |
| taxfolder_download.py | 1150 | 1415 | +265 |
| missing_client_reminder.py | 651 | 682 | +31 |
| _lib/connectors/ai_client.py | 553 | 565 | +12 |

### 新增功能

| 功能 | 所属模块 | 说明 |
|------|----------|------|
| B/B+ 会员自动标记 Paid | taxfolder_download.py | `_load_member_names()` + `_match_members_to_folders()` — 从 Master Client List 读取 B/B+ 会员名，模糊匹配到 Tracker 中的客户，自动标记为 Paid |
| 婚前/婚后姓名匹配 | send_package_email.py | `_reunite_renamed_persons()` — 按 first name 匹配 PDF 和 person（处理已婚改姓情况） |
| 缺失客户状态保留 | missing_client_reminder.py | `_load_old_states()` — 重新生成缺失客户列表时保留用户手动编辑的字段（Include, Sent At, Reply Status 等） |
| 会员名缓存 | taxfolder_download.py | `_MEMBER_CACHE` 文件缓存，避免重复解密 Master Client List |

### 相关提交

| 日期 | 提交 | 说明 |
|------|------|------|
| 04-20 后 | `[Menu9] fix tracker accuracy` | 名字解析重写、EFILEReceipt 检测、B/B+ 会员自动 Paid |
| 04-20 后 | `[阶段二]-[ai_client]` | _lib 统一 AI 客户端（已提交但尚未集成到业务模块） |

---

## 9. 已知架构问题

### 9.1 send_package_email.py 过度膨胀 (God Module)

- **1261 行**，导出 **13 个函数**被 6 个模块依赖
- 承担了：AI 调用、邮件发送/保存、模板处理、文件扫描、PDF 解析、凭据管理
- 本质上充当了"临时公共库"

### 9.2 重复逻辑未收敛到 _lib

| 重复模式 | 出现次数 | _lib 已有替代 |
|----------|----------|---------------|
| SMTP 发送 + IMAP 保存 Sent | 4 处 | email_client.py |
| Gemini API 调用 + fallback | 5 处 | ai_client.py |
| API key 加密存储/读取 (Fernet) | 2 处 | settings.py (.env) |
| JSON log 读写 | 6 处 | logger.py |
| 模板读取 + 填充 | 4 处 | — (需新建) |
| Notepad/Excel 人工审核流程 | 3 处 | — (需抽象) |

### 9.3 配置分散

- `config.py` 是 223 行的单体配置文件
- API keys 和密码分散在两处：config.py 加密文件 vs _lib/.env
- 每年税季需手动更新 TAX_YEAR（config.py 写死，但有启动时检查机制）

### 9.4 无统一错误处理

- 各模块自行 try-catch，错误格式不统一
- 无集中式错误报告机制

### 9.5 无测试覆盖

- 除 _lib/test_ai_client.py 外，业务模块无单元测试
- 测试依赖人工运行和手动验证

### 9.6 硬编码和魔法值

- 邮件服务器地址在 config.py 硬编码
- 文件夹名称 ("6. Sign & Pay", "7. To efile" 等) 在多处硬编码
- AI prompt 文本嵌入在业务代码中

---

## 10. 与编码规范 P1-P4 + A-K 对照差距

| 规范 | 要求 | 当前状态 | 差距 |
|------|------|----------|------|
| P1 关注点分离 | 每个模块单一职责 | send_package_email.py 承担 6+ 职责 | 🔴 严重 |
| P2 数据流 | 配置 → 处理 → 输出清晰 | config.py 混合了路径、业务常量、AI 配置 | 🟡 中等 |
| P3 接口契约 | 函数签名明确 | 部分函数无类型注解 | 🟡 中等 |
| P4 目录结构 | _lib 公共库 + 业务模块 | _lib 已建但未集成 | 🔴 严重 |
| A utils 抽取 | 公共逻辑到 utils | _lib 已有但业务模块未迁移 | 🔴 严重 |
| B 禁跨模块导内部函数 | 只导出公共 API | 大量 `_` 前缀函数被跨模块导入 | 🟡 中等 |
| C 菜单数据驱动 | 菜单配置化 | 菜单硬编码在 tax_automation.py | 🟡 中等 |
| D 配置外置 | 配置在 .env 或 YAML | 部分在 .env，大部分在 config.py | 🟡 中等 |
| E 日志统一 | 使用 _lib/logger.py | 各模块自实现 JSON log | 🟡 中等 |
| F 安全 | 凭据不硬编码 | 已用 Fernet 加密 + .env | 🟢 良好 |
| G 版本控制 | .gitignore 完善 | 数据文件在 .gitignore 中 | 🟢 良好 |

---

## 11. 重构优先级建议

| 优先级 | 任务 | 影响范围 | 工作量 |
|--------|------|----------|--------|
| P0 | 拆分 send_package_email.py → 提取 AI/邮件/文件处理到 _lib | 6 个模块 | 大 |
| P0 | 集成 _lib/ai_client.py 替换内联 AI 调用 | 5 个模块 | 中 |
| P0 | 集成 _lib/email_client.py 替换内联邮件逻辑 | 4 个模块 | 中 |
| P1 | 集成 _lib/logger.py 替换内联 JSON log | 6 处 | 小 |
| P1 | 统一配置管理 (config.py → _lib/settings.py + .env) | 全局 | 中 |
| P1 | 提取模板处理为独立模块 | 4 个模块 | 中 |
| P2 | 添加类型注解 | 全局 | 大 |
| P2 | 编写业务模块单元测试 | 全局 | 大 |
| P3 | 菜单数据驱动 | tax_automation.py | 小 |
| P3 | AI prompt 外置为配置文件 | 5 个模块 | 中 |

---

*报告结束 — 建议结合 `rules_tax_automation.md` 编码规范制定详细重构计划。*
