from sqlalchemy import Column, Integer, String, Text, Boolean, BigInteger, Date, DateTime, JSON, ForeignKey, Index
from sqlalchemy.sql import func
from db.session import Base

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(Text, nullable=False)
    industry = Column(Text)
    contact_name = Column(Text, nullable=False)
    contact_email = Column(Text, nullable=False, unique=True)
    contact_phone = Column(Text)
    status = Column(Text, nullable=False, default="pending_approval", index=True)
    onboarding_form = Column(JSON)
    documents = Column(JSON)
    dograh_org_id = Column(Integer, index=True)
    dograh_user_id = Column(Integer)
    setup_fee_order_id = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    plan = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="active")
    per_minute_rate_paise = Column(BigInteger, nullable=False)
    setup_fee_paid = Column(Boolean, default=False)
    start_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PhoneNumberRequest(Base):
    __tablename__ = "phone_number_requests"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    region = Column(Text)
    use_case = Column(Text)
    status = Column(Text, default="pending")  # pending | approved | denied
    admin_note = Column(Text)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True))

class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, unique=True)
    balance_paise = Column(BigInteger, nullable=False, default=0)
    auto_recharge_enabled = Column(Boolean, default=False)
    auto_recharge_threshold_paise = Column(BigInteger, default=100000)
    auto_recharge_amount_paise = Column(BigInteger, default=500000)
    razorpay_customer_id = Column(Text)
    razorpay_payment_method_id = Column(Text)
    low_balance_alerted_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    type = Column(Text, nullable=False)
    amount_paise = Column(BigInteger, nullable=False)
    description = Column(Text)
    dograh_run_id = Column(Integer)
    razorpay_order_id = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_wallet_transactions_customer", "customer_id", "created_at"),
        Index("idx_wallet_transactions_razorpay", "razorpay_order_id", postgresql_where=razorpay_order_id.isnot(None)),
    )

class PhoneNumber(Base):
    __tablename__ = "phone_numbers"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    number = Column(Text, nullable=False)
    country = Column(Text, default="IN")
    plivo_number_id = Column(Text)
    dograh_phone_number_id = Column(Text)
    agent_id = Column(Integer, ForeignKey("agents.id"))
    monthly_cost_paise = Column(BigInteger)
    status = Column(Text, default="active")
    purchased_at = Column(DateTime(timezone=True), server_default=func.now())

class Agent(Base):
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    name = Column(Text, nullable=False)
    use_case = Column(Text)
    language = Column(Text)
    dograh_workflow_id = Column(Integer)
    dograh_org_id = Column(Integer)
    per_minute_rate_paise = Column(BigInteger, nullable=True)
    status = Column(Text, default="building")
    built_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class CallLog(Base):
    __tablename__ = "call_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"))
    phone_number_id = Column(Integer, ForeignKey("phone_numbers.id"))
    call_type = Column(Text)
    caller_number = Column(Text)
    duration_seconds = Column(Integer, default=0)
    cost_to_customer_paise = Column(BigInteger, default=0)
    dograh_run_id = Column(Integer, nullable=False, unique=True)
    called_at = Column(DateTime(timezone=True), nullable=False)
    processed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_call_logs_processed_at", "processed_at", postgresql_where=processed_at.is_(None)),
    )

class TalkarAdmin(Base):
    __tablename__ = "talkar_admins"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(Text, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    role = Column(Text, nullable=False, default="admin")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class SupportRequest(Base):
    __tablename__ = "support_requests"
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    type = Column(Text, nullable=False)   # 'new_agent'|'add_phone'|'modify_agent'|'billing'|'other'
    subject = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=True)
    status = Column(Text, nullable=False, default="open")  # open|in_progress|resolved|closed
    admin_note = Column(Text)
    resolved_by = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True))
