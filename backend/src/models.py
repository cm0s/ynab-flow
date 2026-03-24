import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Float, Integer, DateTime, ForeignKey
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

    batch = relationship("ImportBatch", back_populates="rows")


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
