import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Float, Integer, BigInteger, DateTime, ForeignKey, Text, UniqueConstraint, false as sa_false
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def generate_uuid():
    return str(uuid.uuid4())

class Plan(Base):
    __tablename__ = 'plans'

    id = Column(String, primary_key=True, default=generate_uuid)
    ynab_plan_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    is_default = Column(Boolean, default=False)
    last_server_knowledge = Column(String, nullable=True)  # legacy / categories
    last_knowledge_payees = Column(String, nullable=True)
    last_knowledge_transactions = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    accounts = relationship("Account", back_populates="plan")

class Account(Base):
    __tablename__ = 'accounts'

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), nullable=False)
    ynab_account_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)
    closed = Column(Boolean, default=False)
    currency = Column(String, nullable=True)

    plan = relationship("Plan", back_populates="accounts")
    transactions = relationship("Transaction", back_populates="account")

class CategoryGroup(Base):
    __tablename__ = 'category_groups'

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), nullable=False)
    ynab_category_group_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    deleted = Column(Boolean, default=False)

    plan = relationship("Plan")
    categories = relationship("Category", back_populates="category_group")

class Category(Base):
    __tablename__ = 'categories'

    id = Column(String, primary_key=True, default=generate_uuid)
    category_group_id = Column(String, ForeignKey('category_groups.id'), nullable=False)
    ynab_category_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    deleted = Column(Boolean, default=False)

    category_group = relationship("CategoryGroup", back_populates="categories")
    transactions = relationship("Transaction", back_populates="category")

class Payee(Base):
    __tablename__ = 'payees'

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), nullable=False)
    ynab_payee_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    transfer_account_id = Column(String, nullable=True)  # YNAB account ID if this is a transfer payee
    deleted = Column(Boolean, default=False)

    plan = relationship("Plan")
    transactions = relationship("Transaction", back_populates="payee")

# Provenance values whose labels may be learned (unreviewed machine guesses and split parents may not)
TRUSTED_PROVENANCE = ("external", "app_confirmed", "app_corrected")


class Transaction(Base):
    __tablename__ = 'transactions'

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), nullable=False)
    account_id = Column(String, ForeignKey('accounts.id'), nullable=False)
    payee_id = Column(String, ForeignKey('payees.id'), nullable=True)
    category_id = Column(String, ForeignKey('categories.id'), nullable=True)
    ynab_transaction_id = Column(String, unique=True, index=True, nullable=False)
    
    date = Column(String, nullable=False)  # Stored as YYYY-MM-DD
    amount = Column(Float, nullable=False) # Milliunits in YNAB, or standard float depending on your adapter
    memo = Column(String, nullable=True)
    cleared = Column(String, nullable=False, default="uncleared")
    approved = Column(Boolean, default=False)
    deleted = Column(Boolean, default=False)

    # Authoritative integer milliunits (``amount`` is the legacy view)
    amount_milliunits = Column(BigInteger, nullable=True)
    import_id = Column(String, nullable=True, index=True)
    transfer_account_ynab_id = Column(String, nullable=True)
    transfer_transaction_ynab_id = Column(String, nullable=True)
    # Learning provenance: external | app_unreviewed | app_confirmed | app_corrected | split_parent
    provenance = Column(String, nullable=False, default="external", server_default="external")
    is_split = Column(Boolean, nullable=False, default=False, server_default=sa_false())

    plan = relationship("Plan")
    account = relationship("Account", back_populates="transactions")
    payee = relationship("Payee", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")


class ImportBatch(Base):
    __tablename__ = 'import_batches'

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), nullable=False)
    filename = Column(String, nullable=True)
    status = Column(String, nullable=False, default="active")  # active | completed | abandoned
    account_id = Column(String, ForeignKey('accounts.id'), nullable=True)  # NULL for legacy batches
    account_confirmed = Column(Boolean, nullable=False, default=True)
    source_format = Column(String, nullable=False, default="")
    report_json = Column(Text, nullable=False, default="{}")  # counts + pending/invalid evidence
    export_signature = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    plan = relationship("Plan")
    rows = relationship("ImportRow", back_populates="batch", cascade="all, delete-orphan")


class ImportRow(Base):
    __tablename__ = 'import_rows'

    id = Column(String, primary_key=True, default=generate_uuid)
    batch_id = Column(String, ForeignKey('import_batches.id'), nullable=False)
    row_index = Column(Integer, nullable=False)

    # CSV / prediction fields
    date = Column(String, nullable=False)
    original_memo = Column(String, nullable=False)
    cleaned_memo = Column(String, nullable=False, default="")
    merchant_stem = Column(String, nullable=False, default="")
    amount = Column(Float, nullable=False)
    label = Column(String, nullable=False, default="")
    source_category = Column(String, nullable=False, default="")
    payee = Column(String, nullable=True)
    category = Column(String, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    source = Column(String, nullable=False, default="unclassified")
    explanation = Column(String, nullable=False, default="")
    review_required = Column(Boolean, nullable=False, default=True)
    flag_ignore = Column(Boolean, nullable=False, default=False)

    # Review state
    status = Column(String, nullable=False, default="pending")  # pending | accepted | ignored
    edited_payee = Column(String, nullable=False, default="")
    edited_category = Column(String, nullable=False, default="")
    edited_memo = Column(String, nullable=False, default="")
    flag_color = Column(String, nullable=False, default="")

    # Canonical money, account context and immutable source evidence
    account_id = Column(String, ForeignKey('accounts.id'), nullable=True)
    amount_milliunits = Column(BigInteger, nullable=True)  # authoritative; ``amount`` is the legacy currency-unit view
    currency = Column(String, nullable=False, default="")
    source_format = Column(String, nullable=False, default="")
    source_status = Column(String, nullable=False, default="")
    evidence_json = Column(Text, nullable=False, default="{}")
    source_record_id = Column(String, ForeignKey('source_records.id'), nullable=True)

    # Per-target confidence and provenance
    payee_confidence = Column(Float, nullable=False, default=0.0)
    category_confidence = Column(Float, nullable=False, default=0.0)
    payee_source = Column(String, nullable=False, default="unclassified")
    category_source = Column(String, nullable=False, default="unclassified")
    payee_explanation = Column(String, nullable=False, default="")
    category_explanation = Column(String, nullable=False, default="")
    review_flag = Column(Boolean, nullable=False, default=False)  # explicit rule/transfer review request

    # Transfer routing: none | candidate | resolved | unresolved
    transfer_state = Column(String, nullable=False, default="none")
    transfer_account_id = Column(String, nullable=True)  # local counterpart account id

    # Reconciliation and write outcome, separate from review ``status``
    reconciliation_state = Column(String, nullable=False, default="pending")
    reconciliation_note = Column(String, nullable=False, default="")
    write_state = Column(String, nullable=False, default="unsent")  # unsent | created | existing | error | unresolved
    write_error = Column(String, nullable=False, default="")
    ynab_transaction_id = Column(String, nullable=True)
    # Payee/category actually sent to YNAB, kept for audit even if they differ from the stored suggestion or edits
    sent_payee = Column(String, nullable=True)
    sent_category = Column(String, nullable=True)
    sent_category_id = Column(String, nullable=True)
    # Stable category identity (local categories.id); names above are display values
    category_id = Column(String, nullable=True)
    edited_category_id = Column(String, nullable=True)

    batch = relationship("ImportBatch", back_populates="rows")
    source_record = relationship("SourceRecord")


class SourceRecord(Base):
    """Persistent occurrence-aware ledger entry for one eligible bank transaction."""
    __tablename__ = 'source_records'
    __table_args__ = (
        UniqueConstraint('plan_id', 'account_id', 'fingerprint', 'occurrence', name='uq_source_occurrence'),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), nullable=False)
    account_id = Column(String, ForeignKey('accounts.id'), nullable=False)
    fingerprint_version = Column(Integer, nullable=False, default=1)
    fingerprint = Column(String, nullable=False)
    occurrence = Column(Integer, nullable=False, default=1)
    source_format = Column(String, nullable=False, default="")
    date = Column(String, nullable=False)
    amount_milliunits = Column(BigInteger, nullable=False)
    currency = Column(String, nullable=False, default="")
    evidence_json = Column(Text, nullable=False, default="{}")
    import_id = Column(String, nullable=False, unique=True)

    reconciliation_state = Column(String, nullable=False, default="pending")
    ynab_transaction_id = Column(String, nullable=True)
    write_state = Column(String, nullable=False, default="unsent")  # unsent | claimed | created | existing | error | reconcile_required
    claim_token = Column(String, nullable=True)
    claimed_at = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SourceExport(Base):
    """Order-independent multiset signature of an uploaded export, per account scope."""
    __tablename__ = 'source_exports'

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), nullable=False)
    account_id = Column(String, ForeignKey('accounts.id'), nullable=False)
    signature = Column(String, nullable=False, index=True)
    counts_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)


class Rule(Base):
    __tablename__ = 'rules'

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), nullable=False)
    name = Column(String, nullable=False)
    priority = Column(Integer, nullable=False, default=0)  # Higher wins

    # Conditions (FR-4.1)
    match_type = Column(String, nullable=False, default="contains")  # exact | contains | regex
    pattern = Column(String, nullable=False)
    amount_sign = Column(String, nullable=True)   # "positive" | "negative" | None (any)
    amount_min = Column(Float, nullable=True)
    amount_max = Column(Float, nullable=True)
    account_filter = Column(String, nullable=True)    # Account name substring
    category_filter = Column(String, nullable=True)   # Source category name substring

    # Outputs (FR-4.2)
    assign_payee = Column(String, nullable=True)
    assign_category = Column(String, nullable=True)
    flag_review = Column(Boolean, default=False)
    flag_ignore = Column(Boolean, default=False)

    plan = relationship("Plan")


class SyncJob(Base):
    __tablename__ = 'sync_jobs'

    id = Column(String, primary_key=True, default=generate_uuid)
    plan_id = Column(String, ForeignKey('plans.id'), unique=True, nullable=False)
    status = Column(String, nullable=False)
    message = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ModelVersion(Base):
    """A trained classifier version: what it learned from, how it compared, and whether it serves."""
    __tablename__ = 'model_versions'

    id = Column(String, primary_key=True, default=generate_uuid)
    status = Column(String, nullable=False, default="candidate")  # candidate | active | retired | rejected
    created_at = Column(DateTime, default=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)
    plan_id = Column(String, nullable=True)
    training_json = Column(Text, nullable=False, default="{}")    # example counts per target
    exclusions_json = Column(Text, nullable=False, default="{}")  # excluded examples per reason
    comparison_json = Column(Text, nullable=False, default="{}")  # held-out metrics: candidate vs active
    note = Column(String, nullable=False, default="")
