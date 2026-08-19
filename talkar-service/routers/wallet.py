from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db

router = APIRouter()


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
