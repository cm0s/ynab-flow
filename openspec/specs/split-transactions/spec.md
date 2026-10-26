# split-transactions Specification

## Purpose

Record mixed purchases correctly by representing an import row as several category lines and writing it to YNAB as a split transaction.

## Requirements

### Requirement: Represent split lines on import rows
An import row SHALL optionally carry two or more split lines, each with an amount in integer milliunits, a category ID, and an optional payee and memo. The row's bank amount, date and account SHALL stay unchanged.

#### Scenario: Supermarket receipt with household items
- **WHEN** a CHF -84.90 row is split into Groceries CHF -62.40 and Household CHF -22.50
- **THEN** the row keeps CHF -84.90 as its amount and stores both lines with their category IDs

### Requirement: Validate splits before they can be pushed
A split SHALL be valid only when it has at least two lines, its line amounts sum exactly to the bank amount, and every category exists and is not deleted. Every line SHALL have the same sign as the bank amount. An invalid split SHALL block the push with a specific message. Transfers and repayments SHALL NOT be split.

#### Scenario: Rounding mismatch
- **WHEN** split lines total CHF -84.85 for a CHF -84.90 row
- **THEN** the row cannot be pushed and shows that 0.05 is unassigned

#### Scenario: Repayment
- **WHEN** a user tries to split a resolved card repayment
- **THEN** the split is rejected

### Requirement: Suggest, edit and clear splits in review
The system SHALL let the user accept a split suggested from a receipt or the AI, edit lines, add or remove lines, and clear the split to return to a single category. Edits SHALL persist across reloads, like other row edits.

#### Scenario: Accept receipt-based split
- **WHEN** the user accepts a split suggested from a linked receipt
- **THEN** the lines are stored on the row and survive a reload

### Requirement: Push splits as YNAB subtransactions
A valid split row SHALL be written as one YNAB transaction with subtransactions whose amounts and categories are the split lines, keeping the row's import ID and `approved=false`. Outcome correlation, duplicate handling and retries SHALL be unchanged.

#### Scenario: Split push
- **WHEN** an accepted valid split row is pushed
- **THEN** YNAB receives one transaction with the bank amount, its import ID, `approved=false` and subtransactions matching the split lines

#### Scenario: Dry run
- **WHEN** a split row is included in a dry run
- **THEN** it is validated, and nothing is sent or persisted
