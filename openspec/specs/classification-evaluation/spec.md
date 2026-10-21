# classification-evaluation Specification

## Purpose

Measure how well payee and category suggestions match real household history, reproducibly and without leaking evaluated rows into what the classifier learns from, so improvements are judged by evidence rather than confidence scores.

## Requirements

### Requirement: Run evaluation locally from private history
The system SHALL provide a local evaluation command that reads either a YNAB plan export archive or the synced local history. It SHALL never write to YNAB, never modify the production database or active models, and write reports only to a git-ignored location.

#### Scenario: Evaluating the YNAB export
- **WHEN** the user runs the evaluation on a YNAB plan export archive
- **THEN** a report is produced locally, no YNAB request is made, and the production database and active model files are unchanged

#### Scenario: Missing or unreadable input
- **WHEN** the input path does not exist or is not a recognised export
- **THEN** the command exits with a non-zero status and an actionable message, producing no report

### Requirement: Use a chronological split
The system SHALL split history by transaction date into an earlier training period and a later evaluation period. The cut-off SHALL be configurable and recorded in the report.

#### Scenario: Default split
- **WHEN** the evaluation runs without an explicit cut-off
- **THEN** every training transaction is dated before every evaluation transaction and the report states both date ranges

### Requirement: Isolate evaluation rows from learned state
Evaluation rows SHALL be excluded from the exact-history index, the fuzzy-history index, ML training and any derived rules used in that run. User-authored rules SHALL be applied only in a separately labelled variant, because they may have been written with knowledge of the evaluation period.

#### Scenario: Leakage check
- **WHEN** an evaluation row's description also appears verbatim only in the evaluation period
- **THEN** no history or ML stage can return that row's own label as evidence

#### Scenario: Rules variant
- **WHEN** the evaluation includes the user's current rules
- **THEN** those results are reported separately from the learned-only results and marked as written with hindsight

### Requirement: Be explicit about special rows
The system SHALL assign every input row to exactly one treatment, either evaluated or excluded with a reason, and report the count for each. Transfers SHALL be scored on payee only. Split lines, uncategorized rows, starting balances and rows with blank descriptions SHALL be excluded from accuracy. Hidden or deleted categories and refunds SHALL be evaluated and reported as their own subsets.

#### Scenario: Every row is accounted for
- **WHEN** the report is produced
- **THEN** the evaluated and excluded counts sum to the number of input rows and each exclusion has a named reason

#### Scenario: Split lines
- **WHEN** the export contains lines belonging to a split transaction
- **THEN** they are excluded from category accuracy and counted under a split reason

### Requirement: Report quality per target and context
The report SHALL give payee and category accuracy separately. For each target it SHALL give breakdowns per account, per prediction source and per confidence band (below 0.75, 0.75 to below 0.95, 0.95 and above), plus coverage (rows with any suggestion), the precision of rows that would be auto-accepted, and the number of rows a user would need to correct.

#### Scenario: Confidence bands
- **WHEN** some evaluated rows receive confidence of at least 0.95
- **THEN** the report shows how many of those rows were correct, alongside the same figure for the lower bands

#### Scenario: Unclassified rows
- **WHEN** the pipeline returns no suggestion for a target
- **THEN** the row counts against coverage and as requiring correction, never as correct

### Requirement: Show failures and limitations
The report SHALL list the highest-confidence wrong predictions and the most frequent confusions with their descriptions. It SHALL state that labels are the present-day assignments in the export. Accuracy SHALL be presented as measured on this history, not as an expected rate for future imports.

#### Scenario: Confident mistakes
- **WHEN** wrong predictions exist
- **THEN** the report lists them ordered by confidence, with description, predicted value, actual value and prediction source

#### Scenario: Label limitation stated
- **WHEN** any report is produced
- **THEN** it states that labels reflect present-day assignments, which may differ from historical choices or category names

### Requirement: Make runs reproducible and comparable
The system SHALL produce identical metrics for identical input, cut-off, options and code. Each report SHALL record its input fingerprint, cut-off, options and code version, and SHALL be machine-readable so two runs can be compared.

#### Scenario: Repeat run
- **WHEN** the same evaluation is run twice without changes
- **THEN** both reports contain identical metrics and the same input fingerprint
