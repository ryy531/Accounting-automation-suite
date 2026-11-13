# Accounting Firm Automation Suite

A collection of Python automation tools I built for the day-to-day work of a Canadian CPA firm: personal and corporate tax, bookkeeping, payroll, accounts receivable and corporate registry filings. The tools cut out repetitive work such as sorting client documents, chasing missing paperwork, sending reminders and keying receipts into QuickBooks, while leaving every decision that involves money or client communication to a person.

This is a sanitized snapshot. Firm details, client data and credentials have been replaced with placeholders.

**At a glance:** about 47,000 lines of Python across 160+ modules, a shared platform layer, a NocoDB database, a Flask webhook server, and AI document extraction with Gemini and OpenAI.

## What the tools do

### Personal tax (T1) workflow — `Tax Automation/`
A menu-driven tool that runs a tax season end to end:
- Creates a folder for each client from the intake list
- **AI extraction:** reads client slips and forms and fills a structured info template
- **AI renaming:** gives every uploaded document a consistent, searchable file name
- Finds last year's clients who have not come back yet and sends reminders
- Sends the tax package, missing-document reminders, sign-and-pay reminders and e-file confirmations by email, in English or Chinese
- Scans email replies and updates the tracker
- Downloads status from TaxFolder with Selenium and builds a sign-and-pay tracker
- Fuzzy-matches client names across spreadsheets with rapidfuzz

### Receipt and bank statement processing — `receipts/`
Turns a folder of receipt photos, PDFs and bank statements into bookkeeping entries:
- Gemini extracts vendor, date, amounts and tax from each document using versioned prompts
- Normalizes, groups multi-page PDFs and pairs related files
- Applies review rules and writes a review workbook that flags anything uncertain
- Exports **QuickBooks IIF** files with account and vendor prefill, plus a validator
- Checkpoints long batches, reports AI cost per run and audits that every source file was processed

### Reminder engine — `ReminderAutomation/`, `TaxAutomationReminderSystem/`, `AR Reminder/`, `AnnualReturn/`
- **Accounts receivable:** three escalating overdue-invoice letters with automatic interest warnings and PDF statements
- **Annual return, GST/PST and T2 deadlines:** bilingual reminders generated from Word templates, keeping formatting and inline images
- Business rules are pure functions with no I/O, so they can be tested and reused; delivery goes out by email, **WhatsApp** or **WeChat**

### Payroll — `payroll/`, `_lib/server/payroll_*.py`
- A daily job creates upcoming payroll periods in NocoDB (dry run by default)
- Fills the payroll schedule workbook from the source sheet
- Architecture for an AI payroll assistant that reads timesheets in any format and applies Saskatchewan rules

### Corporate registry tools — `AccessCodeSetter/`, `AccessibilityCheck/`, `WCBBatchFill/`
Selenium scripts that batch-process the provincial corporate registry and WCB forms for many companies at once, writing results back to the client spreadsheet.

### Weekly to-do report — `WeeklyTodoReport/`
Every Friday, pulls open items from NocoDB, uses an LLM to extract ad-hoc tasks from the week's team chat, builds an Excel report and sends it by WhatsApp and email.

### Real estate bulk email — `RealEstate/`
Design for a CASL-compliant listing campaign to about 2,000 agents: 100 per day on a 20-day cycle, no duplicates, one-click unsubscribe.

## Architecture

```
            Business tools (tax, receipts, payroll, reminders, reports)
                                     │
                                     ▼
 _lib  ──  data_access.py ── connectors: ai_client · email_client · nocodb_client
           server/: Flask webhooks · bot routes · cron jobs
                                     │
          ┌──────────────┬───────────┴──────────┬────────────────────┐
          ▼              ▼                      ▼                    ▼
       NocoDB      Gemini / OpenAI      Gmail API / SMTP     WhatsApp / WeChat
```

- **Shared platform layer (`_lib/`):** business code never calls an AI provider, mail server or database directly. It goes through one AI client (Gemini first, automatic OpenAI fallback, every call logged), one email client and one data-access module.
- **Data model:** four core tables in NocoDB: People, Companies, Engagements (each running its own status workflow) and an append-only Events log for a full audit trail.
- **Event-driven server:** a Flask app receives NocoDB webhooks, advances periods when a status changes, serves a WhatsApp bot that looks up and updates engagements, and runs scheduled jobs through Windows Task Scheduler.
- **Reliability:** timeouts and retries on all external calls, NocoDB health checks, and webhook handlers designed to avoid retry storms.

## Design principles

- **Prepare automatically, confirm by hand.** Anything touching money, client emails or QuickBooks imports is drafted and previewed by the system, then sent or applied only after a person approves it.
- **Dry run first.** Jobs that write data run in preview mode unless called with `--apply`.
- **Privacy by default.** Logs never record SINs, API keys, passwords or full client records; API keys are stored encrypted with Fernet.
- **Documented before built.** Each module has an architecture or design brief, and the repository has written rules (`AGENTS.md`) for how AI coding assistants may change it.

## How it was built

I built this suite with AI coding assistants (Claude Code and Codex), with myself as the product owner, architect and reviewer. I wrote the business requirements and architecture documents, set the guardrails the assistants work under, and tested every tool against real workflows before it went into use. The `_doc/` folder and the `AGENTS.md` files show that process.

## Tech stack

| Area | Tools |
|---|---|
| Language | Python |
| AI | Google Gemini, OpenAI (fallback), DeepSeek |
| Data | NocoDB, pandas, openpyxl, rapidfuzz |
| Server | Flask, Windows Task Scheduler |
| Email | Gmail API (OAuth), SMTP/IMAP |
| Messaging | WhatsApp (OpenClaw gateway), WeChat bridge |
| Browser automation | Selenium |
| Documents | python-docx, mammoth, msoffcrypto-tool, PDF generation |
| Accounting | QuickBooks IIF export |
| Security | cryptography (Fernet) |
| Testing | pytest (20+ test modules) |

## Repository layout

| Folder | Purpose |
|---|---|
| `_lib/` | Shared platform: connectors, data access, webhook server, cron jobs, tests |
| `_doc/` | Architecture and process documents |
| `Tax Automation/` | T1 tax season tool |
| `receipts/` | Receipt and bank statement extraction and QuickBooks export |
| `ReminderAutomation/` | Reminder rules and multi-channel delivery |
| `TaxAutomationReminderSystem/` | Tax and T2 reminder system |
| `AR Reminder/` | Overdue invoice reminders |
| `AnnualReturn/` | Annual return reminders |
| `payroll/` | Payroll schedule and preparation tools |
| `WeeklyTodoReport/` | Weekly to-do report |
| `AccessCodeSetter/`, `AccessibilityCheck/`, `WCBBatchFill/` | Corporate registry and WCB batch tools |
| `RealEstate/` | Bulk listing email design |
| `_migration/` | Setup scripts for moving the suite to a new machine |

Most internal documentation is written in Chinese, the working language of the team.
