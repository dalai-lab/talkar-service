import time
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from db.session import get_db, AsyncSessionLocal
from db.models import Customer, Wallet, Subscription, AutomationAuditLog
from services.automation_auth import verify_automation_key
from db.models import AutomationApiKey
from config import resolve_tier_config

router = APIRouter()

# --- AUDIT LOGGING MIDDLEWARE FOR THIS ROUTER ---
@router.middleware('http')
async def audit_log_middleware(request: Request, call_next):
    if not request.url.path.startswith('/automation/v1/'):
        return await call_next(request)

    body_bytes = await request.body()
    
    async def receive():
        return {'type': 'http.request', 'body': body_bytes}
    request._receive = receive

    payload = None
    if body_bytes:
        import json
        try:
            payload = json.loads(body_bytes)
        except:
            pass

    dograh_org_id = None
    parts = request.url.path.split('/')
    if 'customers' in parts:
        idx = parts.index('customers')
        if len(parts) > idx + 1:
            dograh_org_id = parts[idx + 1]
            if dograh_org_id == 'me' or not dograh_org_id.isdigit():
                dograh_org_id = None

    response = await call_next(request)

    api_key_id = getattr(request.state, 'api_key_id', None)
    
    if api_key_id:
        import asyncio
        asyncio.create_task(_log_audit(
            api_key_id=api_key_id,
            endpoint=f"{request.method} {request.url.path}",
            dograh_org_id=dograh_org_id,
            payload=payload,
            status=response.status_code,
            ip=request.client.host if request.client else None
        ))

    return response

async def _log_audit(api_key_id: int, endpoint: str, dograh_org_id: str, payload: dict, status: int, ip: str):
    try:
        async with AsyncSessionLocal() as db:
            customer_id = None
            if dograh_org_id and dograh_org_id.isdigit():
                res = await db.execute(select(Customer.id).where(Customer.dograh_org_id == int(dograh_org_id)))
                customer_id = res.scalar_one_or_none()

            log = AutomationAuditLog(
                api_key_id=api_key_id,
                endpoint=endpoint,
                customer_id=customer_id,
                dograh_org_id=dograh_org_id,
                payload=payload,
                response_status=status,
                ip_address=ip
            )
            db.add(log)
            await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to write automation audit log: {e}")

class CreditRequest(BaseModel):
    amount_rupees: float
    description: str

class DeductRequest(BaseModel):
    amount_rupees: float
    reason: str

class SuspendRequest(BaseModel):
    reason: str
    custom_message: Optional[str] = None
@router.post('/customers/{dograh_org_id}/credit')
async def credit_customer(
    dograh_org_id: int,
    data: CreditRequest,
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['credit']))
):
    if data.amount_rupees <= 0 or data.amount_rupees > 100000:
        raise HTTPException(400, "Amount must be between 1 and 1,00,000 INR")
    if not data.description:
        raise HTTPException(400, "Description is required")

    customer_res = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = customer_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    if customer.status in ("rejected", "churned"):
        raise HTTPException(400, f"Cannot credit a {customer.status} account")

    amount_paise = int(data.amount_rupees * 100)
    
    from db.models import WalletTransaction
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id)
    if not wallet:
        raise HTTPException(400, "Customer has no active billing wallet")
        
    wallet.balance_paise += amount_paise
    txn = WalletTransaction(
        customer_id=master_id,
        type='credit',
        amount_paise=amount_paise,
        description=f"[Auto] {data.description}"
    )
    db.add(txn)
    await db.commit()

    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
    wallet = wallet_res.scalar_one_or_none()
    
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
    tier_cfg = resolve_tier_config(sub)
    activation_threshold = tier_cfg.get("activation_deposit_paise", 600000)

    from services import dograh_client
    from services.provisioning_service import run_provisioning

    if customer.status == "pending_deposit" and wallet.balance_paise >= activation_threshold:
        customer.status = "active"
        await db.commit()
        try:
            await run_provisioning(customer.id, None, db)
        except Exception:
            pass
        if customer.dograh_org_id:
            tier = sub.plan if sub else "starter"
            concurrent_limit = tier_cfg.get("concurrent_call_limit")
            await dograh_client.restore_org_calls(customer.dograh_org_id, tier, concurrent_limit)
            
    elif customer.status == "suspended" and wallet.balance_paise >= activation_threshold:
        customer.status = "active"
        await db.commit()
        try:
            await run_provisioning(customer.id, None, db)
        except Exception:
            pass
        if customer.dograh_org_id:
            tier = sub.plan if sub else "starter"
            concurrent_limit = tier_cfg.get("concurrent_call_limit")
            await dograh_client.restore_org_calls(customer.dograh_org_id, tier, concurrent_limit)

    elif customer.status == "pending_deposit" and wallet.balance_paise < activation_threshold:
        if customer.dograh_org_id:
            await dograh_client.block_org_calls(customer.dograh_org_id)
            
    from services import notification_service
    import asyncio
    asyncio.create_task(notification_service.notify_customer_credit_granted(
        customer_id=customer.id,
        amount_paise=amount_paise,
        description=f"[Automated] {data.description}"
    ))

    return {"status": "success", "new_balance_rupees": wallet.balance_paise / 100}

@router.post('/customers/{dograh_org_id}/deduct')
async def deduct_customer(
    dograh_org_id: int,
    data: DeductRequest,
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['deduct']))
):
    if data.amount_rupees <= 0:
        raise HTTPException(400, "Amount must be > 0")
    if not data.reason:
        raise HTTPException(400, "Reason is required")

    customer_res = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = customer_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    if customer.status not in ("active", "suspended", "pending_deposit", "agent_building"):
        raise HTTPException(400, "Customer is not in a deductible status")

    amount_paise = int(data.amount_rupees * 100)
    
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id)
    if not wallet:
        raise HTTPException(400, "Customer has no active billing wallet")
        
    if wallet.balance_paise < amount_paise:
        raise HTTPException(400, f"Insufficient balance. Wallet has Rs. {wallet.balance_paise/100:.2f}")

    wallet.balance_paise -= amount_paise
    from db.models import WalletTransaction
    txn = WalletTransaction(
        customer_id=master_id,
        type='deduction',
        amount_paise=amount_paise,
        description=f"[Auto Deduction] {data.reason}"
    )
    db.add(txn)
    await db.commit()

    return {"status": "success", "new_balance_rupees": wallet.balance_paise / 100}
@router.get('/customers/{dograh_org_id}/wallet')
async def get_wallet(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['wallet_read']))
):
    customer_res = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = customer_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    from services.billing_service import get_billing_wallet
    wallet, _ = await get_billing_wallet(db, customer.id)
    if not wallet:
        raise HTTPException(404, "Wallet not found")
        
    return {
        "balance_rupees": wallet.balance_paise / 100,
        "balance_paise": wallet.balance_paise,
        "status": customer.status
    }

@router.get('/wallet/overview')
async def wallet_overview(
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['wallet_read']))
):
    res_bal = await db.execute(select(func.sum(Wallet.balance_paise)))
    total_paise = res_bal.scalar() or 0
    
    res_count = await db.execute(select(func.count(Customer.id)).where(Customer.status == 'active'))
    active_customers = res_count.scalar() or 0
    
    return {
        "total_float_rupees": total_paise / 100,
        "active_customers": active_customers
    }

@router.post('/customers/{dograh_org_id}/suspend')
async def suspend_customer(
    dograh_org_id: int,
    data: SuspendRequest,
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['suspend']))
):
    valid_reasons = {"zero_balance", "policy_violation", "fraud", "other"}
    if data.reason not in valid_reasons:
        raise HTTPException(400, f"Reason must be one of: {', '.join(valid_reasons)}")
        
    customer_res = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = customer_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    if customer.status != "active":
        raise HTTPException(400, f"Customer is {customer.status}, not active")

    customer.status = "suspended"
    existing_form = dict(customer.onboarding_form or {})
    existing_form["suspension_reason"] = data.reason
    existing_form["suspension_message"] = data.custom_message or ""
    customer.onboarding_form = existing_form
    await db.commit()
    
    from services import dograh_client
    try:
        await dograh_client.block_org_calls(customer.dograh_org_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to block calls: {e}")

    from services import notification_service
    import asyncio
    asyncio.create_task(notification_service.notify_customer_suspended(customer.id, data.reason, data.custom_message))

    return {"status": "suspended"}

@router.post('/customers/{dograh_org_id}/unsuspend')
async def unsuspend_customer(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['suspend']))
):
    customer_res = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = customer_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
    if customer.status != "suspended":
        raise HTTPException(400, f"Customer is not suspended")
        
    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id)
    
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    sub = sub_res.scalar_one_or_none()
    tier_cfg = resolve_tier_config(sub)
    activation_threshold = tier_cfg.get("activation_deposit_paise", 600000)

    if not wallet or wallet.balance_paise < activation_threshold:
        raise HTTPException(400, f"Wallet balance must be >= {activation_threshold/100:.2f} INR to unsuspend")

    customer.status = "active"
    existing_form = dict(customer.onboarding_form or {})
    existing_form.pop("suspension_reason", None)
    existing_form.pop("suspension_message", None)
    customer.onboarding_form = existing_form
    await db.commit()
    
    from services import dograh_client
    try:
        tier = sub.plan if sub else "starter"
        concurrent_limit = tier_cfg.get("concurrent_call_limit", 2)
        await dograh_client.restore_org_calls(customer.dograh_org_id, tier, concurrent_limit)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to restore calls: {e}")

    return {"status": "active"}

@router.get('/customers/{dograh_org_id}')
async def get_customer(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['customer_read']))
):
    customer_res = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = customer_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")
        
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    
    from services.billing_service import get_billing_wallet
    wallet, _ = await get_billing_wallet(db, customer.id)
    
    return {
        "dograh_org_id": customer.dograh_org_id,
        "company_name": customer.company_name,
        "status": customer.status,
        "plan": sub.plan if sub else "starter",
        "balance_rupees": wallet.balance_paise / 100 if wallet else 0,
        "created_at": customer.created_at
    }

@router.get('/calls/stats')
async def get_call_stats(
    from_date: str,
    to_date: str,
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['stats_read']))
):
    from datetime import datetime
    try:
        start_dt = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use ISO 8601")
        
    if (end_dt - start_dt).days > 90:
        raise HTTPException(400, "Date range cannot exceed 90 days")

    from db.models import CallLog
    query = select(
        func.count(CallLog.id).label('total_calls'),
        func.sum(CallLog.duration_seconds).label('total_duration'),
        func.sum(CallLog.cost_to_customer_paise).label('total_cost_paise')
    ).where(CallLog.called_at >= start_dt, CallLog.called_at <= end_dt)
    
    res = await db.execute(query)
    row = res.one()
    
    return {
        "total_calls": row.total_calls or 0,
        "total_duration_seconds": row.total_duration or 0,
        "total_cost_rupees": (row.total_cost_paise or 0) / 100
    }

@router.get('/wallet/alerts')
async def wallet_alerts(
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['stats_read']))
):
    from sqlalchemy import text
    query = text('''
        SELECT c.dograh_org_id, w.balance_paise, COALESCE((c.report_settings->>'low_balance_alert_paise')::bigint, 150000) as threshold
        FROM wallets w JOIN customers c ON c.id = w.customer_id
        WHERE c.status = 'active' AND w.balance_paise < COALESCE((c.report_settings->>'low_balance_alert_paise')::bigint, 150000)
    ''')
    res = await db.execute(query)
    alerts = []
    for row in res:
        alerts.append({
            "dograh_org_id": row[0],
            "balance_rupees": row[1] / 100,
            "threshold_rupees": float(row[2] or 150000) / 100
        })
    return {"low_balance_customers": alerts}

@router.get('/customers')
async def list_customers(
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    api_key: AutomationApiKey = Depends(verify_automation_key(['customer_read']))
):
    if limit > 100: limit = 100
    
    query = select(Customer)
    if status:
        query = query.where(Customer.status == status)
        
    query = query.order_by(Customer.created_at.desc()).offset(offset).limit(limit)
    res = await db.execute(query)
    customers = res.scalars().all()
    
    # We need to fetch wallets for all these customers
    customer_ids = [c.id for c in customers]
    wallets = {}
    if customer_ids:
        wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id.in_(customer_ids)))
        for w in wallet_res.scalars().all():
            wallets[w.customer_id] = w
            
    # And subscriptions
    subs = {}
    if customer_ids:
        sub_res = await db.execute(select(Subscription).where(Subscription.customer_id.in_(customer_ids)))
        for s in sub_res.scalars().all():
            subs[s.customer_id] = s
            
    result = []
    for c in customers:
        w = wallets.get(c.id)
        s = subs.get(c.id)
        result.append({
            "dograh_org_id": c.dograh_org_id,
            "company_name": c.company_name,
            "status": c.status,
            "plan": s.plan if s else "starter",
            "balance_rupees": w.balance_paise / 100 if w else 0,
            "created_at": c.created_at
        })
        
    return {"customers": result, "limit": limit, "offset": offset}
