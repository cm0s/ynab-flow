# Spec Delta

## ADDED Requirements

### Requirement: Re-match after a receipt correction
When the user corrects a receipt's total, date, merchant, currency or kind, or reclassifies a message as a receipt, the system SHALL recompute that receipt's proposals and automatic links against the active import rows. User link, unlink and reject decisions SHALL be kept.

#### Scenario: Corrected total now matches
- **WHEN** a receipt read with a total of 0 is corrected to CHF 51.00 and an active row of CHF -51.00 from that merchant posted two days later exists
- **THEN** the receipt is proposed or linked for that row under the usual matching rules

#### Scenario: Rejected pairing stays rejected
- **WHEN** the user had rejected a receipt for a row and then corrects the receipt's date
- **THEN** the pairing stays rejected

### Requirement: Link a receipt to rows of any import as evidence
From a receipt, the user SHALL be able to search candidate rows in any import, including rows already pushed to YNAB, scored with the same signals, and link or unlink them. Linking a pushed row SHALL only record evidence for review and spending analysis; it SHALL NOT change the row, re-send it to YNAB or change its reconciliation state. Automatic linking SHALL remain limited to unpushed rows.

#### Scenario: Receipt for an already pushed purchase
- **WHEN** the user links a Decathlon receipt to a CHF -100.00 row pushed last month
- **THEN** the link is stored and used for spending analysis, and the row's write state and YNAB transaction are unchanged

#### Scenario: Candidate search
- **WHEN** the user looks for rows for a receipt dated 16 October of CHF 100.00
- **THEN** rows within ±30 days from any import are listed by score with their signals
