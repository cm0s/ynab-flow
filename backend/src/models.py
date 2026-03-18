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
    last_server_knowledge = Column(String, nullable=True)
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
