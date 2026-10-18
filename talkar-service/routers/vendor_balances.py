"""Router for Upstream Vendor Balance & Infrastructure Monitoring."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import TalkarAdmin, VendorAlertState
from db.session import get_db
from services.admin_auth import get_current_admin
from services.vendor_balance_service import VendorBalanceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/vendor-balances", tags=["Vendor Balances"])


class ThresholdUpdateRequest(BaseModel):
    vendor: str = Field(..., description="Provider name: plivo, deepgram, elevenlabs, smallest, or openai")
    warning_threshold: float = Field(..., ge=0, description="Warning threshold level")
    critical_threshold: float = Field(..., ge=0, description="Critical threshold level")


@router.get("")
async def get_vendor_balances(
    admin: TalkarAdmin = Depends(get_current_admin),
) -> dict[str, Any]:
    """Fetch the latest upstream vendor balances summary."""
    service = VendorBalanceService()
    return await service.get_latest_summary()


@router.post("/refresh")
async def refresh_vendor_balances(
    admin: TalkarAdmin = Depends(get_current_admin),
) -> dict[str, Any]:
    """Force an immediate live poll against all upstream vendors."""
    service = VendorBalanceService()
    return await service.sync_all_vendors(force=True)


@router.put("/thresholds")
async def update_vendor_thresholds(
    request: ThresholdUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: TalkarAdmin = Depends(get_current_admin),
) -> dict[str, Any]:
    """Update custom warning and critical thresholds for a vendor."""
    vendor = request.vendor.lower().strip()
    if vendor not in ("plivo", "deepgram", "elevenlabs", "smallest", "openai"):
        raise HTTPException(status_code=400, detail="Invalid vendor name. Must be plivo, deepgram, elevenlabs, smallest, or openai")

    state = await db.get(VendorAlertState, vendor)
    if not state:
        state = VendorAlertState(
            vendor=vendor,
            warning_threshold=request.warning_threshold,
            critical_threshold=request.critical_threshold,
        )
        db.add(state)
    else:
        state.warning_threshold = request.warning_threshold
        state.critical_threshold = request.critical_threshold

    await db.commit()
    await db.refresh(state)

    logger.info(
        f"[VENDOR THRESHOLDS] Admin {admin.email} updated {vendor} thresholds: "
        f"warning={state.warning_threshold}, critical={state.critical_threshold}"
    )

    return {
        "status": "success",
        "vendor": vendor,
        "warning_threshold": float(state.warning_threshold),
        "critical_threshold": float(state.critical_threshold),
    }
