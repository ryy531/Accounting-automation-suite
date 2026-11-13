# Template Brief — Menu 4: Prior Year Client Reminder

> **⚠️ This document covers FORMAT AND PLACEHOLDER INTEGRATION ONLY.**
> It does not contain any writing guidance, tone suggestions, or content structure.
> **The email body content must be written separately by the user.**

---

## File to Create

Save as a **.docx** file at:

```
\\file-server\Shared Folder\Quickbook Files\6. 2025 Personal Tax\0. Admin\1.3 Communications\5. 2025 Prior Year Reminder PackageForAI.docx
```

> For future tax years, update `6. 2025` and `5. 2025` to match the new year accordingly.

---

## How the System Uses This File

1. The program reads the `.docx` file using the **mammoth** library, converting it to HTML.
2. Paragraph structure, bold, italic, and images are preserved.
3. The HTML is then sent as the email body via SMTP.
4. Before sending, the program scans the HTML for the placeholders listed below and replaces them with per-client values.

---

## Placeholders

These strings must appear **verbatim** in the document body. Do not add spaces inside the braces, do not use single braces, do not reformat them.

| Placeholder | Replaced with | Required? |
|---|---|---|
| `{{CLIENT_NAME}}` | Client's full name, e.g. `John Smith` | **Mandatory** |
| `{{PREV_TAX_YEAR}}` | The tax year being filed, e.g. `2024` (= current season year − 1) | Optional |

### Rules

- Both placeholders use **double curly braces** `{{ }}` — consistent with all other templates in this project.
- `{{CLIENT_NAME}}` must appear at least once (typically in the greeting line).
- `{{PREV_TAX_YEAR}}` is optional. If omitted, the tax year can be written directly in the document (e.g., "2024") — but using the placeholder means the template does not need to be rewritten each year.
- **Do not add any other `{{...}}` patterns** — unrecognised placeholders are left as-is in the sent email.

---

## Technical Constraints

| Item | Requirement |
|---|---|
| File format | `.docx` (Word document) |
| Encoding | UTF-8 compatible — standard Word output is fine |
| Images / Logo | Embed directly in the `.docx` — mammoth extracts them and embeds as inline CID images |
| Tables | Supported by mammoth; will render in most email clients |
| Hyperlinks | Supported |
| File size | Keep total images under **~2 MB** to avoid email client issues |
| Font / colour | No restrictions — mammoth preserves inline styles |

---

## Subject Line

The subject line is **not** part of the `.docx` template. It is defined in `config.py`:

```python
MISSING_CLIENT_SUBJECT = "2024 Personal Tax Season – {client_name}"
```

To change the subject, update that line in `config.py`.

---

## Fallback Behaviour

If the `.docx` file does not exist at the path above, the program automatically falls back to a **built-in HTML template** with `{{CLIENT_NAME}}` and `{{PREV_TAX_YEAR}}` already in place. The fallback is functional but unstyled — it does not include the firm's logo or letterhead.

---

## Reference: Other Templates in This Project

For visual consistency (logo, letterhead, signature block), refer to the existing templates in the same folder:

| File | Used by |
|---|---|
| `2. 2025 Personal Tax Return PackageForAI.docx` | Menu 5 — Send package email |
| `3. 2025 Follow-up Reminder PackageForAI.docx` | Menu 6 — Follow-up reminder |
| `4 Efile_confirmation_2025_en_AI.docx` | Menu 7 — Efile confirmation |
