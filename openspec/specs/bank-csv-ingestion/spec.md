# bank-csv-ingestion Specification

## Purpose

Convert supported bank exports into precise, traceable import candidates with explicit account context and visible validation results.

## Requirements

### Requirement: Detect supported bank exports
The system SHALL detect Swisscard CSV exports by their required column headers and continue supporting PostFinance and generic bank CSV exports. Unsupported layouts SHALL produce an actionable format error rather than a successful empty import.

#### Scenario: Swisscard header detection
- **WHEN** a UTF-8 CSV has Transaction date, Description, Merchant, Card number, Currency, Amount, Debit/Credit and Status headers
- **THEN** the system uses Swisscard semantics regardless of filename

#### Scenario: Existing supported formats
- **WHEN** a valid PostFinance export or generic Date/Memo/Inflow/Outflow export is uploaded
- **THEN** its valid transactions retain their existing date, direction and category meaning

### Requirement: Validate exact money and dates
The system SHALL validate dates and parse money without binary floating-point truncation. It SHALL preserve exact signed integer milliunits at the YNAB boundary, report malformed values and contradictory direction/sign fields by source row, and prevent unsupported currency conversion.

#### Scenario: Swisscard debit and credits
- **WHEN** CHF rows contain a Debit amount 50.00, a Credit amount -43.70 and a Credit amount -6000.00
- **THEN** the signed amounts are respectively -50000, 43700 and 6000000 milliunits without reversing credits twice

#### Scenario: Invalid financial input
- **WHEN** a row has an impossible date, non-finite amount, unsupported precision, unsupported currency or a sign inconsistent with Debit/Credit
- **THEN** the row receives a specific validation error and cannot be written while other valid rows remain available

### Requirement: Account context precedes prediction
The system SHALL require an explicit target account in the selected plan before importing new candidates. It SHALL show the detected source and target account, persist that account selection, and reuse its identity and name during prediction, reload and reclassification. Account changes SHALL require explicit reassignment and reconciliation.

#### Scenario: Account-scoped classification
- **WHEN** an account-scoped rule applies to an uploaded row
- **THEN** initial prediction and later reclassification both evaluate it using the persisted target account

#### Scenario: Invalid target account
- **WHEN** a target account belongs to another plan or is unavailable
- **THEN** the import is rejected without replacing the current active batch

### Requirement: Preserve source evidence independently
The system SHALL preserve original description, raw date and money values, source format, currency, card identity, merchant, status, source category fields and foreign-currency fields when supplied. Source evidence SHALL survive persistence, reload and reclassification independently of edited payee, category and outgoing memo.

#### Scenario: Edited row retains evidence
- **WHEN** a user edits and reloads a Swisscard row containing Merchant Category and Registered Category
- **THEN** both source categories, original description and card identity remain available unchanged

#### Scenario: Blank merchant repayment
- **WHEN** a valid credit has an empty Merchant and a repayment Description
- **THEN** the row is retained with its description and exact positive amount

### Requirement: Report lifecycle and exclusion counts
The system SHALL default Swisscard imports to valid Posted candidates, retain Pending evidence outside the write queue, and separately report detected, eligible, pending and invalid row counts. Unsupported statuses SHALL be reported explicitly. Pending rows SHALL never be sent to YNAB by this default workflow.

#### Scenario: Posted and pending summary
- **WHEN** an export contains 174 valid entries, of which 167 are Posted and 7 are Pending
- **THEN** the import reports 174 detected, 167 eligible and 7 pending exclusions, retaining every Posted credit including repayments

#### Scenario: Pending evidence remains available
- **WHEN** a later export contains a Posted row resembling an earlier Pending row
- **THEN** the earlier evidence remains available for lifecycle comparison without having created a Pending transaction in YNAB
