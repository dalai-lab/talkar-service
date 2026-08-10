from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from db.session import get_db
from db.models import Customer, Subscription, Wallet, WalletTransaction
from services import razorpay_client, billing_service, provisioning_service, notification_service
from pydantic import BaseModel
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class SetupFeeRequest(BaseModel):
    customer_id: int
    plan: str

class TopupRequest(BaseModel):
    customer_id: int
    amount_rupees: int

class CreateSubscriptionRequest(BaseModel):
    customer_id: int

class DograhQuotaRequest(BaseModel):
    organization_id: int

class DograhDeductRequest(BaseModel):
    workflow_run_id: int
    duration_seconds: int
    organization_id: int

@router.post("/setup-fee/create-order")
async def create_setup_fee_order(data: SetupFeeRequest, db: AsyncSession = Depends(get_db)):
    # 1. Validate customer
    result = await db.execute(select(Customer).where(Customer.id == data.customer_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")
    
    if customer.status != "approved":
        raise HTTPException(400, f"Cannot create setup fee order. Customer status is '{customer.status}', expected 'approved'.")
    
    # Example logic for setup fee amount
    setup_fee_paise = 2500000 if data.plan == "pro" else 1000000 
    
    # 2. Create order
    order = await razorpay_client.create_setup_fee_order(setup_fee_paise, f"setup_{customer.id}", data.customer_id, data.plan)
    
    # 3. Store order ID
    customer.setup_fee_order_id = order["id"]
    await db.commit()
    
    # 2A Spec: Returns { razorpay_order_id, amount, currency }
    return {"razorpay_order_id": order["id"], "amount": setup_fee_paise, "currency": "INR"}

@router.post("/topup/create-order")
async def create_topup_order(data: TopupRequest, db: AsyncSession = Depends(get_db)):
    if data.amount_rupees < 500:
        raise HTTPException(400, "Minimum top-up is ₹500")
        
    amount_paise = data.amount_rupees * 100
    order = await razorpay_client.create_topup_order(amount_paise, f"topup_{data.customer_id}", data.customer_id)
    
    return {"razorpay_order_id": order["id"], "amount_paise": amount_paise, "currency": "INR"}

@router.post("/webhook/razorpay")
async def razorpay_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    
    if not razorpay_client.verify_webhook_signature(payload, signature):
        raise HTTPException(400, "Invalid signature")
        
    event = json.loads(payload)
    event_type = event.get("event")
    
    if event_type == "payment.captured":
        order_id = event["payload"]["payment"]["entity"]["order_id"]
        amount_paise = event["payload"]["payment"]["entity"]["amount"]
        notes = event["payload"]["payment"]["entity"].get("notes", {})
        
        # Idempotency for wallet top-ups
        result = await db.execute(
            select(WalletTransaction).where(WalletTransaction.razorpay_order_id == order_id)
        )
        if result.scalar_one_or_none():
            return {"status": "duplicate top-up, ignored"}
            
        # Check if setup fee or top-up
        cust_result = await db.execute(select(Customer).where(Customer.setup_fee_order_id == order_id))
        customer = cust_result.scalar_one_or_none()
        
        if customer:
            # It's a setup fee. Bulletproof Idempotency check:
            if customer.status != "approved":
                return {"status": "already provisioned or invalid state, ignored"}
                
            plan = notes.get("plan", "starter")
            customer.status = "agent_building"  # Immediate visual update
            await db.commit()
            # New signature: run_provisioning(customer_id, plan, db)
            await provisioning_service.run_provisioning(customer.id, plan, db)
        else:
            # It's a top-up
            customer_id = notes.get("customer_id")
            if customer_id:
                await billing_service.credit_wallet(db, int(customer_id), amount_paise, order_id)
                
    elif event_type == "subscription.charged":
        logger.info(f"Subscription charged: {event['payload']['subscription']['entity']['id']}")
        # SOT 209: Talkar Billing Service credits wallet for subscription
        # Logic would go here
        
    elif event_type == "subscription.halted":
        sub_id = event['payload']['subscription']['entity']['id']
        logger.error(f"Subscription halted: {sub_id}")
        await notification_service.notify_admin_subscription_halted(sub_id)

    return {"status": "ok"}

@router.post("/check-quota")
async def check_quota(data: DograhQuotaRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.organization_id))
    customer = result.scalar_one_or_none()
    if not customer:
        return {"has_quota": False}
        
    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
    wallet = wallet_res.scalar_one_or_none()
    
    if not wallet or wallet.balance_paise <= 0:
        return {"has_quota": False}
        
    return {"has_quota": True}

@router.post("/deduct")
async def deduct_for_run(data: DograhDeductRequest, db: AsyncSession = Depends(get_db)):
    import math
    from sqlalchemy.sql import func
    from services.billing_service import check_and_trigger_auto_recharge
    from db.models import CallLog

    result = await db.execute(select(Customer).where(Customer.dograh_org_id == data.organization_id))
    customer = result.scalar_one_or_none()
    if not customer:
        return {"status": "ignored", "reason": "no customer found for org"}

    # --- Idempotency: never double-charge the same run ---
    existing = await db.execute(
        select(CallLog).where(CallLog.dograh_run_id == data.workflow_run_id)
    )
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "reason": "run already processed"}

    # Get subscription for per-minute rate
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    rate = sub.per_minute_rate_paise if sub else 1800  # fallback to Starter rate (₹18/min)

    # --- SOT edge case: zero-duration call (pipeline crash, abnormal termination) ---
    # SOT line 278: log it with cost=0, do not retry, alert admin
    if data.duration_seconds <= 0:
        call_log = CallLog(
            customer_id=customer.id,
            dograh_run_id=data.workflow_run_id,
            duration_seconds=0,
            cost_to_customer_paise=0,
            called_at=func.now(),
            processed_at=func.now()
        )
        db.add(call_log)
        await db.commit()
        logger.warning(f"Zero-duration call for run {data.workflow_run_id} — logged with 0 cost")
        return {"status": "zero_duration_logged"}

    # SOT line 281: cost = ceil(duration_seconds / 60) * per_minute_rate, always ceil, never floor
    minutes = math.ceil(data.duration_seconds / 60)
    cost_paise = minutes * rate

    # Insert call log (processed_at set now = wallet deducted)
    call_log = CallLog(
        customer_id=customer.id,
        dograh_run_id=data.workflow_run_id,
        duration_seconds=data.duration_seconds,
        cost_to_customer_paise=cost_paise,
        called_at=func.now(),
        processed_at=func.now()
    )
    db.add(call_log)

    # SOT line 282: atomic deduction — UPDATE...RETURNING to get new balance
    result2 = await db.execute(
        update(Wallet)
        .where(Wallet.customer_id == customer.id)
        .values(balance_paise=Wallet.balance_paise - cost_paise)
        .returning(Wallet)
    )
    wallet = result2.scalar_one_or_none()

    # Log wallet transaction
    txn = WalletTransaction(
        customer_id=customer.id,
        type="call_deduction",
        amount_paise=-cost_paise,
        description=f"Call deduction for run {data.workflow_run_id}",
        dograh_run_id=data.workflow_run_id
    )
    db.add(txn)
    await db.commit()

    # SOT line 284-285: check auto-recharge, then check low balance
    await check_and_trigger_auto_recharge(db, customer.id)

    # SOT line 658: if balance went negative, email customer
    if wallet and wallet.balance_paise < 0:
        logger.warning(f"Customer {customer.id} wallet negative: {wallet.balance_paise} paise")
        await notification_service.notify_customer_negative_balance(customer.id)

    return {"status": "ok", "cost_paise": cost_paise, "new_balance_paise": wallet.balance_paise if wallet else None}

@router.get("/wallet/by-org/{org_id}")
async def get_wallet_by_org(org_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
    wallet = wallet_res.scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, "Wallet not found")
        
    return {
        "balance_paise": wallet.balance_paise,
        "auto_recharge_enabled": wallet.auto_recharge_enabled
    }


@router.post("/subscription/create")
async def create_subscription(data: CreateSubscriptionRequest, db: AsyncSession = Depends(get_db)):
    # 2D spec: Creates Razorpay Subscription linked to customer's Razorpay ID
    result = await db.execute(select(Subscription).where(Subscription.customer_id == data.customer_id))
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        raise HTTPException(404, "Subscription plan not found for customer")
        
    wallet_result = await db.execute(select(Wallet).where(Wallet.customer_id == data.customer_id))
    wallet = wallet_result.scalar_one_or_none()
    
    if not wallet or not wallet.razorpay_customer_id:
        raise HTTPException(400, "Customer Razorpay ID not setup")
        
    sub_data = await razorpay_client.create_subscription(
        plan_id=subscription.plan, 
        amount_paise=subscription.monthly_fee_paise,
        customer_razorpay_id=wallet.razorpay_customer_id
    )
    
    subscription.razorpay_subscription_id = sub_data["id"]
    await db.commit()
    
    return {"razorpay_subscription_id": sub_data["id"]}

