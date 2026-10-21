# model-training-lifecycle Specification

## Purpose

Control what the classifier learns from and when a newly trained model replaces the current one, so models improve from confirmed outcomes without reinforcing their own unreviewed guesses or silently getting worse.

## Requirements

### Requirement: Record the provenance of synced transactions
The system SHALL classify each synced transaction as created by this app or not, and SHALL record whether a YNAB user has approved it. A transaction created by this app and still unapproved SHALL be treated as an unreviewed machine guess. One approved in YNAB SHALL be treated as user-confirmed, and one whose YNAB category or payee differs from the value this app sent SHALL be treated as a user correction.

#### Scenario: Unapproved app transaction
- **WHEN** a synced transaction carries this app's import identifier and is unapproved
- **THEN** it is recorded as an unreviewed machine guess

#### Scenario: User changed the category
- **WHEN** a synced app-created transaction has a category different from the one this app sent
- **THEN** it is recorded as a user correction with the corrected category

### Requirement: Train only on trusted examples
Training SHALL exclude unreviewed machine guesses, deleted transactions, split parents and transactions without a usable description. Training statistics SHALL report how many examples were used and how many were excluded for each reason.

#### Scenario: Machine guess is not learned
- **WHEN** an app-created transaction is still unapproved at training time
- **THEN** it contributes to neither the payee nor the category model, and the training statistics count it as excluded

#### Scenario: Split parent
- **WHEN** a synced transaction has subtransactions
- **THEN** it is excluded from category training

### Requirement: Promote a model only when it is not worse
Training SHALL produce a candidate model without replacing the active one. The candidate SHALL be compared with the active model on a recent held-out slice of trusted examples it was not trained on, and promoted only if its accuracy on each trained target is not lower than the active model's. With no active model, a candidate that trains successfully SHALL be promoted. The comparison result SHALL be returned and recorded.

#### Scenario: Worse candidate is kept out
- **WHEN** a candidate's category accuracy on the held-out slice is lower than the active model's
- **THEN** the active model keeps serving predictions and the result explains why the candidate was not promoted

#### Scenario: First model
- **WHEN** no model is active and training succeeds
- **THEN** the candidate is promoted and recorded as the active model

### Requirement: Version models and allow rollback
Each trained model SHALL be stored as a distinct version with its training time, example counts, exclusion counts, held-out comparison and active status. The user SHALL be able to see the active version and restore the previously active version.

#### Scenario: Rollback
- **WHEN** the user restores the previous version
- **THEN** predictions use that version from the next prediction onward and the change is recorded

#### Scenario: Concurrent training
- **WHEN** a training request arrives while another is running
- **THEN** it is rejected without changing the active model
