from typing import Optional, List
from sqlalchemy import Column, Integer, String, Text, Boolean, BigInteger, Date, DateTime, JSON, ForeignKey, Index, Numeric
from sqlalchemy.sql import func
from db.session import Base
import enum

class TransactionType(str, enum.Enum):
    TOP_UP = "top_up"
    CALL_DEDUCTION = "call_deduction"
    REFUND = "refund"
    GRANT = "grant"
    MANUAL_CREDIT = "manual_credit"
    MANUAL_DEDUCT = "manual_deduct"
    MANUAL_SET_BALANCE = "manual_set_balance"
    CREDIT = "credit"
    DEDUCTION = "deduction"
    PHONE_NUMBER_LEASE = "phone_number_lease"

TRANSACTION_TYPE_CATEGORIES = {
    "top_up": [TransactionType.TOP_UP.value, TransactionType.CREDIT.value, TransactionType.MANUAL_CREDIT.value],
    "credit": [TransactionType.CREDIT.value, TransactionType.TOP_UP.value, TransactionType.MANUAL_CREDIT.value],
    "call_deduction": [TransactionType.CALL_DEDUCTION.value, TransactionType.DEDUCTION.value, TransactionType.MANUAL_DEDUCT.value],
    "deduction": [TransactionType.DEDUCTION.value, TransactionType.CALL_DEDUCTION.value, TransactionType.MANUAL_DEDUCT.value, TransactionType.PHONE_NUMBER_LEASE.value],
    "phone_number_lease": [TransactionType.PHONE_NUMBER_LEASE.value],
    "grant": [TransactionType.GRANT.value, TransactionType.MANUAL_CREDIT.value, TransactionType.CREDIT.value],
    "refund": [TransactionType.REFUND.value],
    "manual_credit": [TransactionType.MANUAL_CREDIT.value],
    "manual_deduct": [TransactionType.MANUAL_DEDUCT.value],
    "manual_set_balance": [TransactionType.MANUAL_SET_BALANCE.value],
}

def resolve_transaction_types(type_filter: Optional[str]) -> Optional[List[str]]:
    if not type_filter or not isinstance(type_filter, str):
        return None
    type_clean = type_filter.lower().strip()
    if type_clean == "all":
        return None
    if type_clean in TRANSACTION_TYPE_CATEGORIES:
        return TRANSACTION_TYPE_CATEGORIES[type_clean]
    return [type_filter]

class NotificationCategory(str, enum.Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    CRITICAL = "critical"
    UPDATE = "update"
    MAINTENANCE = "maintenance"

class Customer(Base):
    __tablename__ = "customers"
    
    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(Text, nullable=False)
    industry = Column(Text)
    contact_name = Column(Text, nullable=False)
    contact_email = Column(Text, nullable=False)
    contact_phone = Column(Text)
    status = Column(Text, nullable=False, default="pending_approval", index=True)
    onboarding_form = Column(JSON)
    documents = Column(JSON)
    billing_org_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    dograh_org_id = Column(Integer, index=True)
    dograh_user_id = Column(Integer)
    setup_fee_order_id = Column(Text)
    crm_links = Column(JSON, default=list)
    report_settings = Column(JSON, default=dict)
    is_test_account = Column(Boolean, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uq_customers_email_org", "contact_email", "dograh_org_id", unique=True),
    )

class GlobalPlatformSettings(Base):
    __tablename__ = "global_platform_settings"

    id = Column(Integer, primary_key=True, index=True)
    settings = Column(JSON, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, unique=True)
    plan = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="active")
    per_minute_rate_paise = Column(BigInteger, nullable=False)
    setup_fee_paid = Column(Boolean, default=False)
    start_date = Column(Date, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    custom_config = Column(JSON, nullable=True)
    custom_plan_label = Column(Text, nullable=True)

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
    zero_balance_since = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    sub_org_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    dograh_org_id = Column(Integer, nullable=True)
    type = Column(Text, nullable=False)
    amount_paise = Column(BigInteger, nullable=False)
    description = Column(Text)
    dograh_run_id = Column(Integer)
    razorpay_order_id = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_wallet_transactions_customer", "customer_id", "created_at"),
        Index("idx_wallet_transactions_sub_org", "sub_org_id", "created_at"),
        Index("idx_wallet_transactions_dograh_org", "dograh_org_id", "created_at"),
        Index("idx_wallet_transactions_razorpay", "razorpay_order_id", unique=True, postgresql_where=razorpay_order_id.isnot(None)),
        Index("idx_wallet_transactions_dograh_run_id", "dograh_run_id", unique=True, postgresql_where=dograh_run_id.isnot(None)),
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
    last_billed_at = Column(DateTime(timezone=True), nullable=True)

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
    crm_link = Column(Text, nullable=True)
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
    # Stamped at billing time so profitability uses the plan active during the call
    plan = Column(Text, nullable=True)           # e.g. "starter", "growth", "pro"
    tts_provider = Column(Text, nullable=True)   # e.g. "deepgram", "smallest_ai", "elevenlabs"

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
    resolved_by = Column(Integer, ForeignKey("talkar_admins.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True))

class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    type = Column(Text, nullable=False, default=NotificationCategory.INFO.value)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Announcement(Base):
    __tablename__ = "announcements"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    type = Column(Text, nullable=False, default=NotificationCategory.INFO.value)
    channels = Column(JSON, default=list)  # ["in_app", "email"]
    status = Column(Text, nullable=False, default="sent")  # 'sent' | 'scheduled' | 'cancelled'
    scheduled_for = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    sent_by = Column(Integer, ForeignKey("talkar_admins.id"), nullable=True)
    recipients_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class AutomationApiKey(Base):
    __tablename__ = "automation_api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key_hash = Column(Text, unique=True, nullable=False, index=True)
    name = Column(Text, nullable=False)
    created_by_admin_id = Column(Integer, ForeignKey("talkar_admins.id"))
    scopes = Column(JSON, default=list)
    rate_limit_per_minute = Column(Integer, default=60)
    last_used_at = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))

class AutomationAuditLog(Base):
    __tablename__ = "automation_audit_log"

    id = Column(BigInteger, primary_key=True, index=True)
    api_key_id = Column(Integer, ForeignKey("automation_api_keys.id"), index=True)
    endpoint = Column(Text, nullable=False)
    customer_id = Column(Integer, index=True)
    dograh_org_id = Column(Text, index=True)
    payload = Column(JSON)
    response_status = Column(Integer)
    ip_address = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    sub_org_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    dograh_org_id = Column(Integer, nullable=True, index=True)
    wallet_transaction_id = Column(Integer, ForeignKey("wallet_transactions.id"), unique=True)
    invoice_number = Column(Text, nullable=False, unique=True)
    amount_paise = Column(BigInteger, nullable=False)
    status = Column(Text, nullable=False, default="paid")
    pdf_url = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class VendorBalanceSnapshot(Base):
    __tablename__ = "vendor_balance_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    vendor = Column(String(32), nullable=False)
    balance_numeric = Column(Numeric(14, 4), nullable=True)
    units = Column(String(16), nullable=False)
    quota_limit = Column(Numeric(14, 4), nullable=True)
    status = Column(String(32), nullable=False)
    auto_recharge_enabled = Column(Boolean, default=False)
    overages_enabled = Column(Boolean, default=False)
    next_reset_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    raw_payload = Column(JSON, nullable=True)
    checked_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_vendor_balance_vendor_checked", "vendor", checked_at.desc()),
    )


class VendorAlertState(Base):
    __tablename__ = "vendor_alert_states"

    vendor = Column(String(32), primary_key=True)
    current_tier = Column(String(32), nullable=False, default="healthy")
    last_alert_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_alert_tier = Column(String(32), nullable=True)
    alerts_sent_in_window = Column(Integer, default=0)
    window_start_at = Column(DateTime(timezone=True), server_default=func.now())
    warning_threshold = Column(Numeric(14, 4), nullable=False)
    critical_threshold = Column(Numeric(14, 4), nullable=False)
    last_delivery_status = Column(String(16), default="success")
    last_delivery_error = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
