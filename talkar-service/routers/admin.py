from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from db.session import get_db
from db.models import Customer, Wallet, WalletTransaction, CallLog, Agent, TalkarAdmin, Subscription
from services import razorpay_client, notification_service
from services.admin_auth import get_current_admin, create_admin_access_token
from pydantic import BaseModel
from typing import Optional

router = APIRouter()

class AdminLoginRequest(BaseModel):
    email: str
    password: str

class ApproveApplicationRequest(BaseModel):
    plan: str # 'starter' | 'pro' | 'enterprise'

class RejectApplicationRequest(BaseModel):
    reason: str

class RequestInfoRequest(BaseModel):
    message: str

class CreditGrantRequest(BaseModel):
    amount_paise: int
    description: str

class AdminCreateRequest(BaseModel):
    email: str
    password_hash: str
    name: str
    role: Optional[str] = "admin"

class CustomerUpdateRequest(BaseModel):
    status: Optional[str] = None
    plan: Optional[str] = None

# --- AUTH ---

@router.post("/login")
async def admin_login(data: AdminLoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TalkarAdmin).where(TalkarAdmin.email == data.email))
    admin = result.scalar_one_or_none()

    if not admin:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # bcrypt password verification
    import bcrypt
    try:
        password_valid = bcrypt.checkpw(
            data.password.encode("utf-8"),
            admin.password_hash.encode("utf-8")
        )
    except Exception:
        password_valid = False

    if not password_valid:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_admin_access_token(
        data={"sub": str(admin.id), "is_admin": True, "role": admin.role}
    )
    return {"access_token": access_token, "token_type": "bearer", "admin": {"name": admin.name, "email": admin.email}}

# --- APPLICATIONS ---

@router.get("/applications")
async def get_applications(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.status == "under_review").order_by(Customer.created_at.asc()))
    return result.scalars().all()

@router.post("/applications/{customer_id}/approve")
async def approve_application(customer_id: int, data: ApproveApplicationRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status != "under_review": raise HTTPException(400, "Customer is not under review")
    
    # Update status
    customer.status = "approved"

    # Store the approved plan in onboarding_form JSON (no migration needed).
    # provisioning_service reads this if plan is not passed explicitly (e.g. on retry).
    existing_form = customer.onboarding_form or {}
    existing_form["approved_plan"] = data.plan
    customer.onboarding_form = existing_form

    # TESTING: ₹1 setup fee. Change to real amounts before production launch:
    # setup_fee_paise = 2500000 if data.plan == "pro" else 1000000  # Pro=₹25k, Starter=₹10k
    setup_fee_paise = 100  # ₹1 for testing
    order = await razorpay_client.create_setup_fee_order(setup_fee_paise, f"setup_{customer.id}", customer.id, data.plan)
    customer.setup_fee_order_id = order["id"]

    await db.commit()

    fee_display = "₹1 (test)"
    await notification_service.send_email(
        to_email=customer.contact_email,
        subject="Your Talkar Application is Approved!",
        body=f"Hi {customer.contact_name}, your application is approved! Please complete your setup fee payment ({fee_display}) to get started. Your payment link will appear on your dashboard."
    )
    return {"status": "approved", "setup_fee_order_id": order["id"]}

@router.post("/applications/{customer_id}/reject")
async def reject_application(customer_id: int, data: RejectApplicationRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    customer.status = "rejected"
    # Store rejection reason in onboarding_form alongside the other data
    existing_form = customer.onboarding_form or {}
    existing_form["rejection_reason"] = data.reason
    customer.onboarding_form = existing_form
    await db.commit()
    
    # Send rejection email to customer
    await notification_service.notify_customer_rejected(customer.id, data.reason)
    return {"status": "rejected"}

@router.post("/applications/{customer_id}/request-info")
async def request_info(customer_id: int, data: RequestInfoRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    # Send email to customer
    await notification_service.send_email(
        to_email=customer.contact_email,
        subject="Additional Information Required for Your Talkar Application",
        body=data.message
    )
    return {"status": "info_requested"}

# --- CUSTOMERS ---

@router.get("/customers")
async def get_all_customers(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).order_by(Customer.created_at.desc()))
    return result.scalars().all()

@router.get("/customers/{customer_id}")
async def get_customer(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    return customer

@router.patch("/customers/{customer_id}")
async def update_customer(customer_id: int, data: CustomerUpdateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if data.status: customer.status = data.status
    if data.plan:
        sub = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
        sub = sub.scalar_one_or_none()
        if sub: sub.plan = data.plan

    await db.commit()
    return {"status": "success"}

@router.post("/customers/{customer_id}/credit")
async def manual_credit_grant(customer_id: int, data: CreditGrantRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    wallet = await db.execute(select(Wallet).where(Wallet.customer_id == customer_id))
    wallet = wallet.scalar_one_or_none()
    if not wallet: raise HTTPException(404, "Wallet not found")

    wallet.balance_paise += data.amount_paise
    txn = WalletTransaction(
        customer_id=customer_id,
        type="grant",
        amount_paise=data.amount_paise,
        description=data.description
    )
    db.add(txn)
    await db.commit()
    return {"status": "success", "new_balance": wallet.balance_paise}

@router.post("/customers/{customer_id}/suspend")
async def suspend_customer(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    customer.status = "suspended"
    await db.commit()
    return {"status": "suspended"}

@router.post("/customers/{customer_id}/provision/retry")
async def retry_provisioning(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    if customer.status not in ("approved", "agent_building"):
        raise HTTPException(400, "Customer is not in a provisionable state")
    from services.provisioning_service import run_provisioning
    try:
        await run_provisioning(customer_id)
        return {"status": "provisioning_complete"}
    except Exception as e:
        raise HTTPException(500, f"Provisioning failed: {str(e)}")

# --- BUILD QUEUE ---

@router.get("/build-queue")
async def get_build_queue(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.status == "agent_building").order_by(Customer.created_at.asc()))
    return result.scalars().all()

@router.patch("/build-queue/{customer_id}/assign")
async def assign_build(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Assign builder logic stub
    return {"status": "assigned"}

@router.patch("/build-queue/{customer_id}/ready")
async def mark_ready(customer_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    customer.status = "active"
    await db.commit()
    # SOT p.194: notify customer agent is live
    await notification_service.send_email(
        to_email=customer.contact_email,
        subject="Your AI Agent is Live!",
        body=f"Hi {customer.contact_name}, your Talkar AI agent is live! Add credits to your wallet to start receiving calls. Log in at app.talkar.ai"
    )
    return {"status": "active"}

# --- WALLET / STATS ---

@router.get("/wallet/overview")
async def get_wallet_overview(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(func.sum(Wallet.balance_paise)))
    total_balance = result.scalar() or 0
    return {"total_platform_balance_paise": total_balance}

@router.get("/wallet/alerts")
async def get_wallet_alerts(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Yellow alert: balance < ₹500 (50000 paise) — low but not zero
    yellow_result = await db.execute(
        select(Wallet).where(
            Wallet.balance_paise > 0,
            Wallet.balance_paise < 50000
        ).order_by(Wallet.balance_paise.asc())
    )
    yellow = yellow_result.scalars().all()

    # Red/urgent alert: balance = 0, calls blocked
    red_result = await db.execute(
        select(Wallet).where(Wallet.balance_paise <= 0).order_by(Wallet.updated_at.asc())
    )
    red = red_result.scalars().all()

    return {"low_balance": yellow, "zero_balance": red}

@router.get("/calls/active")
async def get_active_calls(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Stub reading from Dograh Redis
    return {"active_calls": 0}

@router.get("/calls/stats")
async def get_calls_stats(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Stub call stats per customer
    return []

@router.get("/profitability")
async def get_profitability(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    # Stub profitability calculation
    return {"revenue": 0, "cost": 0, "margin": 0}

# --- TEAM ---

@router.get("/team")
async def get_team(db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(TalkarAdmin))
    return result.scalars().all()

@router.post("/team")
async def add_team_member(data: AdminCreateRequest, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    admin = TalkarAdmin(
        email=data.email,
        password_hash=data.password_hash,
        name=data.name,
        role=data.role
    )
    db.add(admin)
    await db.commit()
    return {"status": "success", "admin_id": admin.id}

@router.delete("/team/{admin_id}")
async def remove_team_member(admin_id: int, db: AsyncSession = Depends(get_db), current_admin: TalkarAdmin = Depends(get_current_admin)):
    result = await db.execute(select(TalkarAdmin).where(TalkarAdmin.id == admin_id))
    admin = result.scalar_one_or_none()
    if admin:
        await db.delete(admin)
        await db.commit()
    return {"status": "deleted"}
