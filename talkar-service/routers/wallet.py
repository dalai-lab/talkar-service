from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db

router = APIRouter()

@router.get("/check")
async def check_wallet(dograh_org_id: int, db: AsyncSession = Depends(get_db)):
    """
    Called by Dograh quota_service before every call.
    Returns has_balance=True/False. Non-Talkar orgs (superusers) always pass through.
    """
    from db.models import Customer, Wallet
    from sqlalchemy import select

    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        # Not a Talkar org (e.g. superuser/internal org) — let through
        return {"has_balance": True, "reason": "not_a_talkar_customer"}

    if customer.status != "active":
        return {"has_balance": False, "reason": "customer_not_active"}

    from services.billing_service import get_billing_wallet
    wallet, master_id = await get_billing_wallet(db, customer.id)
    
    if not wallet or wallet.balance_paise <= 0:
        return {"has_balance": False, "balance_paise": wallet.balance_paise if wallet else 0, "reason": "empty_wallet"}

    # Minimum Reserve Check (Risk 1)
    from db.models import Subscription
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
    sub = sub_res.scalar_one_or_none()
    from config import TIER_CONFIG
    
    rate = sub.per_minute_rate_paise if sub else TIER_CONFIG["starter"]["per_minute_rate_paise"]
    minimum_reserve_paise = 5 * rate
    
    if wallet.balance_paise < minimum_reserve_paise:
        import logging
        logging.getLogger(__name__).warning(f"Org {dograh_org_id} has balance {wallet.balance_paise} below minimum reserve {minimum_reserve_paise}")
        return {"has_balance": False, "balance_paise": wallet.balance_paise, "reason": "below_minimum_reserve"}

    return {"has_balance": True, "balance_paise": wallet.balance_paise}


@router.get("/{customer_id}")
async def get_wallet(customer_id: int, db: AsyncSession = Depends(get_db)):
    from db.models import Wallet
    from sqlalchemy import select
    from fastapi import HTTPException
    
    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer_id))
    wallet = wallet_res.scalar_one_or_none()
    if not wallet: raise HTTPException(404, "Wallet not found")
    
    return {
        "balance_paise": wallet.balance_paise,
        "auto_recharge_enabled": wallet.auto_recharge_enabled
    }
