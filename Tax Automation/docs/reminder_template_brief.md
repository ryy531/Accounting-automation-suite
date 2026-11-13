# Email Template Brief — Follow-up Reminder (Menu 6)

## Purpose
A follow-up reminder email sent to clients whose tax files are pending because
they have not yet provided required documents or information.

---

## File to Create
Save as a **.docx** file at:
`Z:\Quickbook Files\6. 2025 Personal Tax\0. Admin\1.3 Communications\3. 2025 Follow-up Reminder PackageForAI.docx`

---

## Placeholders (must appear verbatim in the document)

| Placeholder | What it becomes at send time |
|---|---|
| `{{CLIENT_NAME}}` | Client's display name, e.g. "John Smith" |
| `{{MISSING_INFO}}` | One or more lines, each in the format "<item> related information", e.g. "rental related information" |

Both placeholders are **mandatory**. Do not rename or reformat them.

---

## Tone & Style
- Professional but warm — same tone as a CPA firm's standard client communication
- Concise — clients should be able to read and act in under 30 seconds
- English only

---

## Required Structure

1. **Greeting** — use `{{CLIENT_NAME}}`
2. **Opening sentence** — brief reminder that their 2024 personal tax return is in
   progress and we are still waiting on information from them
3. **Missing items section** — introduce with one sentence, then insert
   `{{MISSING_INFO}}` as the list
4. **Call to action** — ask the client to send the outstanding items at their
   earliest convenience, or contact the office if they have questions
5. **Closing** — professional sign-off from ExampleCo CPA (include office phone /
   email if the firm wants; leave a placeholder comment if unknown)

---

## Example of rendered output (for reference only)

> Dear John Smith,
>
> We hope this message finds you well. We are currently preparing your 2024
> personal tax return and wanted to follow up, as we are still waiting on the
> following information:
>
> rental related information
> RRSP related information
>
> Please forward these documents at your earliest convenience so we can complete
> your return on time. If you have any questions, don't hesitate to reach out.
>
> Warm regards,
> ExampleCo CPA
> [phone] | [email]

---

## Notes for the AI writing this template
- The `{{MISSING_INFO}}` block will be injected as HTML `<br>`-separated lines,
  so it will render as a line-by-line list inside the email client.
- The template is a Word (.docx) file. Write the body text naturally — the
  system converts it to HTML automatically using the mammoth library, which
  preserves bold, italic, and paragraph structure.
- If the firm has a logo or letterhead in the existing package email template
  (`2. 2025 Personal Tax Return PackageForAI.docx` in the same folder), match
  that visual style.
- Do not add any content that references specific dollar amounts, filing
  deadlines, or refund/owing status — those details are not relevant to this
  reminder.
