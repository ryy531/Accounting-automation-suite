You are reading the first page of a Canadian bank or credit card statement.

Return ONLY valid JSON. Do not include markdown fences, explanations, or comments.

Return one JSON object with these fields:

{
  "statement_year": number or null,
  "statement_date": "YYYY-MM-DD or null",
  "statement_period_start": "YYYY-MM-DD or null",
  "statement_period_end": "YYYY-MM-DD or null",
  "account_last_four": "last four digits or empty string",
  "card_last_four": "last four digits or empty string"
}

Rules:

- Do not extract transactions.
- Look for statement date, statement period, billing period, closing date, payment
  due date, account summary, page header, and card/account number.
- statement_year should normally be the year of statement_period_end or
  statement_date.
- If a period crosses years, preserve both years in statement_period_start and
  statement_period_end.
- If only one reliable year is visible, set statement_year to that year.
- Never guess a year that is not visible on the page.
