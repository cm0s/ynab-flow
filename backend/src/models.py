import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, Float, DateTime, ForeignKey
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
