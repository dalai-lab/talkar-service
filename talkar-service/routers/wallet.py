from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db
from db.models import TalkarAdmin
from services.admin_auth import get_current_admin
from services.billing_service import get_billing_wallet

router = APIRouter()


@router.get("/{customer_id}")
async def get_wallet(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    from sqlalchemy import select
    from db.models import Wallet
    
    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer_id))
    wallet = wallet_res.scalar_one_or_none()
    
    master_id = customer_id
    if not wallet:
        wallet, master_id = await get_billing_wallet(db, customer_id, allow_suspended=True)

    if not wallet:
        raise HTTPException(404, "Wallet not found")

    return {
        "balance_paise": wallet.balance_paise,
        "auto_recharge_enabled": wallet.auto_recharge_enabled,
        "customer_id": customer_id,
        "master_customer_id": master_id,
        "is_sub_org": master_id != customer_id,
    }

