# Test fixtures

All fixtures are synthetic. Private reference inputs live in the ignored
`ynab-automation/data/` directory and are never copied here.

Availability note (checked 2026-10-19, metadata only): `cashback-original.csv`,
`postfinance-original.csv` and `ynab-history-export.zip` are present locally.
The Swisscard layout is comma separated with the headers
`Transaction date, Description, Merchant, Card number, Currency, Amount,
Foreign Currency, Amount in foreign currency, Debit/Credit, Status,
Merchant Category, Registered Category`, dates as `DD.MM.YYYY`, amounts as
positive decimals for Debit and negative for Credit.
