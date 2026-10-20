# transaction-import-reconciliation Specification

## Purpose

Reconcile bank records with prior imports and YNAB outcomes so retries preserve legitimate transactions without silently duplicating or losing them.

## Requirements

### Requirement: Persist stable source identities
The system SHALL assign each eligible occurrence a persistent identity scoped to its plan and target account using immutable source evidence. Upload batch IDs, prediction outputs, editable payee/category and outgoing annotations SHALL not affect identity. The write workflow SHALL use persisted identity rather than trusting client-supplied source attributes.

#### Scenario: Edits and new upload preserve identity
- **WHEN** a user edits payee, category or memo and later uploads the same export in a new batch
- **THEN** corresponding source occurrences retain their identity and reuse the same import identifiers

#### Scenario: Account scopes identity
- **WHEN** similar bank rows belong to two different target accounts
- **THEN** their import identities remain separate

### Requirement: Reconcile occurrences without collapsing legitimate purchases
The system SHALL reconcile repeated and overlapping uploads using occurrence counts and persisted evidence. Distinct identical purchases in the same export SHALL retain separate occurrences. When available evidence cannot distinguish a new occurrence from an existing one, the system SHALL require resolution before writing and disclose the ambiguity.

#### Scenario: Two identical purchases and a repeat upload
- **WHEN** a file contains two genuinely identical purchases and is uploaded twice
- **THEN** two distinct identities are retained and the second upload creates no additional records

#### Scenario: Partial overlap with ambiguous multiplicity
- **WHEN** a partial export contains one indistinguishable row previously observed twice
- **THEN** the system does not silently create a third purchase or assert a unique match without supporting evidence

### Requirement: Reconcile earlier YNAB imports conservatively
The system SHALL compare candidates with existing YNAB records before creating transactions, including records imported outside this identity scheme. Only explicit identity links or unambiguous evidence SHALL establish a confirmed existing match. Potential or stale matches SHALL require review, and arbitrary new IDs SHALL not be treated as proof of deduplication.

#### Scenario: Earlier file-based import
- **WHEN** a source candidate resembles a YNAB record whose import identifier came from another importer
- **THEN** the system evaluates account, date, signed amount and available description evidence and either links a confirmed match or reports a conflict before writing

#### Scenario: Previously linked transaction was deleted
- **WHEN** a prior successful source link points to a deleted YNAB transaction
- **THEN** the candidate is flagged for explicit resolution rather than silently recreated or reported as an existing live transaction

### Requirement: Repayments require transfer routing
The system SHALL treat recognized card repayments as transfer candidates with no ordinary spending or income category. Unresolved destination or counterpart matching SHALL require review and block writing that repayment. Confirmed routing SHALL use a valid same-plan transfer account and avoid creating a second counterpart for an existing transfer.

#### Scenario: Unresolved blank-merchant repayment
- **WHEN** a Posted credit has a blank merchant and recognized repayment description but no confirmed counterpart
- **THEN** it remains visible with its positive amount, requires transfer resolution and cannot be sent as ordinary income

#### Scenario: Existing Post-side transfer
- **WHEN** repayment evidence identifies an existing transfer from the Post account to the selected card account
- **THEN** reconciliation links the existing card-side counterpart rather than creating another repayment

### Requirement: Correlate write outcomes by identity
The system SHALL associate created, skipped and failed outcomes with their source identity and import identifier independently of response order. An absent or ambiguous response association SHALL remain unresolved and SHALL never count as confirmed creation. Every row result SHALL expose its identity, status and known YNAB transaction ID.

#### Scenario: Mixed reordered response
- **WHEN** a request contains an existing duplicate, a newly created transaction and a locally invalid row, with returned transactions in a different order
- **THEN** each result is associated with its own source row and only the verified creation receives that YNAB ID

#### Scenario: Missing outcome
- **WHEN** a response omits an expected import identifier or a network timeout leaves creation uncertain
- **THEN** the affected row remains unresolved and reconciliation precedes a retry

### Requirement: Persist completion and retain retryable rows
The system SHALL persist confirmed created and confirmed existing outcomes, exclude only successfully handled rows from the active review queue, and retain errors, unresolved matches and unsent rows with their edits across reloads. Dry runs SHALL preserve review and completion state. A batch SHALL not complete while retryable rows remain.

#### Scenario: Partial success survives reload
- **WHEN** a write creates one row, confirms one duplicate and fails another row
- **THEN** reload shows the failed row available for retry with its edits and excludes the two successfully handled rows

#### Scenario: Dry run changes no completion state
- **WHEN** a dry run validates all accepted rows
- **THEN** no YNAB write occurs and all rows retain their review state

### Requirement: Keep categorization separate from YNAB approval
The system SHALL explicitly send `approved=false` when creating transactions in this workflow, regardless of local acceptance or prediction confidence. Development verification SHALL use isolated databases, mocked external services and dry-run writes for private reference inputs.

#### Scenario: Accepted prediction remains auditable
- **WHEN** a locally accepted, high-confidence transaction is created in YNAB
- **THEN** the outgoing payload has `approved=false` and retains its categorized result for subsequent audit
