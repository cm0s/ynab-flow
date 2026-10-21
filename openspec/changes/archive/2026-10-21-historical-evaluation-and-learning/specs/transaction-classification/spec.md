# Spec Delta

## ADDED Requirements

### Requirement: Identify categories by stable ID
The system SHALL identify categories by their YNAB category ID in training labels, historical evidence, suggestions and write-back, and SHALL resolve display names from that ID when showing them. Renaming a category in YNAB SHALL not split its history into two labels or change which category a stored suggestion refers to.

#### Scenario: Category renamed after training
- **WHEN** a category with history is renamed in YNAB and data is synced again
- **THEN** existing evidence and the trained model still predict that category, and it is shown under its new name

#### Scenario: Write-back uses the stored ID
- **WHEN** an accepted row with a suggested category is pushed
- **THEN** the outgoing transaction carries that category's ID without looking it up by name

#### Scenario: Category no longer exists
- **WHEN** a suggested category ID has been deleted in YNAB
- **THEN** the row requires review and is not pushed with that category
