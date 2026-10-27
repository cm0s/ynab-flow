# receipt-matching Specification

## Purpose

Link extracted receipts to bank import rows conservatively, so receipts enrich the bank record without ever changing it.

## Requirements

### Requirement: Match receipts using several signals
The system SHALL propose receipt matches for import rows from amount agreement (exact, or within currency conversion tolerance for foreign-currency rows), a date window allowing posting after purchase, merchant similarity and, where available, card or account hints. Each proposal SHALL carry a score and the signals that produced it.

#### Scenario: Card purchase posted later
- **WHEN** a receipt dated 3 October for CHF 84.90 at an online shop exists and a bank row of CHF -84.90 posted on 6 October mentions that merchant
- **THEN** the receipt is proposed for that row with its date, amount and merchant signals

#### Scenario: Amount alone is not enough
- **WHEN** a receipt matches a row's amount but neither the merchant nor a nearby date
- **THEN** it is not linked automatically

### Requirement: Resolve ambiguity explicitly
The system SHALL link a receipt automatically only when exactly one row and one receipt are strong mutual best matches. Otherwise it SHALL show the candidates for the user to choose or reject. A receipt SHALL be linked to at most one row unless the user splits it across shipments.

#### Scenario: Two identical orders
- **WHEN** two receipts and two bank rows share the same merchant and amount within the window
- **THEN** no automatic link is made and the user chooses the pairing

#### Scenario: Partial shipment
- **WHEN** an order receipt totals CHF 120 and two bank rows of CHF 80 and CHF 40 follow
- **THEN** both rows are offered the receipt as a candidate, and the user can link it to both

### Requirement: Receipts never change bank values
Receipt data SHALL only add evidence and suggestions. It SHALL NOT change a row's amount, date, account, transfer routing, import identity or reconciliation state.

#### Scenario: Receipt total differs from bank amount
- **WHEN** a linked receipt's total differs from the bank amount because of a discount or fee
- **THEN** the bank amount is kept, and the difference is shown and taken into account by any split suggestion

### Requirement: Refunds and payment notices
The system SHALL match refund receipts to inflow rows and SHALL treat payment or shipping notices as supporting evidence for an existing purchase match, not as separate purchases.

#### Scenario: Refund
- **WHEN** a refund receipt for CHF 25 matches a CHF +25 card row from the same merchant
- **THEN** it is proposed for that inflow row and labelled as a refund

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
