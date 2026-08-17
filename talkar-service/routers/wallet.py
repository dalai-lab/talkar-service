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

    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
    wallet = wallet_res.scalar_one_or_none()
    if not wallet or wallet.balance_paise <= 0:
        return {"has_balance": False, "balance_paise": wallet.balance_paise if wallet else 0}

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
