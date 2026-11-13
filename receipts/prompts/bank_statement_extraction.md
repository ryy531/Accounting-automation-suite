You are extracting transactions from a Canadian bank or credit card statement.

Return ONLY valid JSON. Do not include markdown fences, explanations, or comments.

Return a JSON array. Each item must represent one transaction row from the statement.
Do not return opening balance, closing balance, subtotal, total, page total, carried
forward, brought forward, or continued rows as normal transactions. If you are not
sure whether a row is a real transaction, include it but set needs_review to true
and explain in note.

Every item must include these fields:

{
  "date": "YYYY-MM-DD or null",
  "posting_date": "YYYY-MM-DD or null",
  "description": "transaction description or empty string",
  "withdrawal": number or null,
  "deposit": number or null,
  "charge": number or null,
  "payment_credit": number or null,
  "refund": number or null,
  "transfer_returned": number or null,
  "amount": number or null,
  "balance": number or null,
  "account_last_four": "last four digits or empty string",
  "card_last_four": "last four digits or empty string",
  "doc_type": "bank_statement",
  "needs_review": boolean,
  "note": "review reason, extra fields, or empty string"
}

Rules:

- Use date for the transaction date.
- Use posting_date only when the statement shows a separate posting date.
- Use YYYY-MM-DD. If the year is not visible on the row, infer it from the statement
  period when clearly available. Otherwise use null and set needs_review to true.
- Many statements show transaction dates as month/day only, such as "Jan 28",
  "JAN 28", "01/28", or "28 Jan". Convert these to YYYY-MM-DD by using the
  statement year visible on the same page. Look for the year in statement period,
  statement date, payment due date, billing period, closing date, page header, or
  account summary. If the page shows a statement period crossing two years, choose
  the year that matches the transaction month within that period.
- Do not leave date null just because the transaction row omits the year. Leave it
  null only when no reliable year can be found anywhere on the page.
- If the statement has separate Transaction Date and Posting Date columns, map
  Transaction Date to date and Posting Date to posting_date. If only one date
  column is present, use it as date and set posting_date to null.
- Use positive numbers for withdrawal, deposit, charge, payment_credit, refund, and
  transfer_returned.
- For chequing or savings accounts:
  - withdrawal is money leaving the account.
  - deposit is money entering the account.
- For credit card accounts:
  - charge is card spending.
  - payment_credit is card payment or autopay made by the cardholder.
- Use refund for merchant refunds, reversals, or returns.
- Use transfer_returned for failed or returned transfers.
- amount is the net amount using only withdrawal, deposit, charge, and payment_credit:
  - withdrawal and charge are negative.
  - deposit and payment_credit are positive.
  - refund and transfer_returned do not contribute to amount in v1.1.
- If a row has refund or transfer_returned, leave amount null unless the statement
  explicitly provides a net amount.
- If extra statement fields appear, put them in note using this format:
  [EXTRA: Field_Name=value]
- If confidence is low, set needs_review to true and explain in note.
- Never invent missing descriptions, dates, balances, or account digits.

If no transactions are found, return [].
