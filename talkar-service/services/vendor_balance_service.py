"""Upstream Vendor Balance & Credit Monitoring Service for Talkar Infrastructure.

Monitors Plivo, Deepgram, ElevenLabs, and Smallest AI. Enforces 10% alert hysteresis,
cooldown suppression, and Redis distributed lock synchronization.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import desc, select, update

from config import settings
from db.models import VendorAlertState, VendorBalanceSnapshot
from db.session import AsyncSessionLocal
from services import redis_client
from services.notification_service import send_vendor_balance_alert

logger = logging.getLogger(__name__)

# Constants
SYNC_LOCK_KEY = "vendor_balance:sync_lock"
SYNC_LOCK_TTL_SECONDS = 30
INFRA_HEALTH_KEY = "vendor_infrastructure:healthy"
INFRA_STATUS_KEY = "vendor_infrastructure:status"

CONSOLE_URLS = {
    "plivo": "https://console.plivo.com/billing/",
    "deepgram": "https://console.deepgram.com/project/",
    "elevenlabs": "https://elevenlabs.io/app/subscription",
    "smallest": "https://smallest.ai/dashboard",
}

DEFAULT_THRESHOLDS = {
    "plivo": {"warning": 20.0, "critical": 2.0},
    "deepgram": {"warning": 20.0, "critical": 2.0},
    "elevenlabs": {"warning": 50000.0, "critical": 5000.0},
    "smallest": {"warning": 500.0, "critical": 50.0},
}


class VendorCheckResult(BaseModel):
    vendor: str
    display_name: str
    balance: float | None = None
    units: str
    quota_limit: float | None = None
    status: Literal["healthy", "warning", "critical", "payg_active", "not_configured", "error"]
    auto_recharge: bool = False
    overages_enabled: bool = False
    next_reset_at: datetime | None = None
    error_message: str | None = None
    raw_payload: dict[str, Any] | None = None
    console_url: str = ""
    warning_threshold: float = 0.0
    critical_threshold: float = 0.0
    checked_at: datetime


class VendorBalanceService:
    """Orchestrates multi-vendor credit balance checking, snapshot logging, and alerts."""

    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._client = http_client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=10.0)

    # -------------------------------------------------------------------------
    # Vendor-Specific Checkers
    # -------------------------------------------------------------------------

    async def check_plivo(self, warning_threshold: float = 20.0, critical_threshold: float = 2.0) -> VendorCheckResult:
        auth_id = getattr(settings, "TALKAR_PLIVO_AUTH_ID", "").strip()
        auth_token = getattr(settings, "TALKAR_PLIVO_AUTH_TOKEN", "").strip()
        now = datetime.now(timezone.utc)

        if not auth_id or not auth_token:
            return VendorCheckResult(
                vendor="plivo",
                display_name="Plivo Telephony",
                units="USD",
                status="not_configured",
                error_message="TALKAR_PLIVO_AUTH_ID or TALKAR_PLIVO_AUTH_TOKEN not set in environment",
                console_url=CONSOLE_URLS["plivo"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

        url = f"https://api.plivo.com/v1/Account/{auth_id}/"
        client = await self._get_client()
        try:
            resp = await client.get(url, auth=(auth_id, auth_token))
            if resp.status_code != 200:
                return VendorCheckResult(
                    vendor="plivo",
                    display_name="Plivo Telephony",
                    units="USD",
                    status="error",
                    error_message=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    console_url=CONSOLE_URLS["plivo"],
                    warning_threshold=warning_threshold,
                    critical_threshold=critical_threshold,
                    checked_at=now,
                )

            data = resp.json()
            raw_credits = data.get("cash_credits", 0.0)
            balance = float(raw_credits)
            currency = str(data.get("currency", "USD")).upper()
            auto_recharge = bool(data.get("auto_recharge", False))

            if balance <= critical_threshold:
                status = "critical"
            elif balance <= warning_threshold:
                status = "warning"
            else:
                status = "healthy"

            return VendorCheckResult(
                vendor="plivo",
                display_name="Plivo Telephony",
                balance=balance,
                units=currency,
                status=status,
                auto_recharge=auto_recharge,
                raw_payload=data,
                console_url=CONSOLE_URLS["plivo"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )
        except Exception as e:
            logger.error(f"[VENDOR BALANCE] Failed to fetch Plivo balance: {e}")
            return VendorCheckResult(
                vendor="plivo",
                display_name="Plivo Telephony",
                units="USD",
                status="error",
                error_message=str(e),
                console_url=CONSOLE_URLS["plivo"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

    async def check_deepgram(self, warning_threshold: float = 20.0, critical_threshold: float = 2.0) -> VendorCheckResult:
        api_key = getattr(settings, "TALKAR_DEEPGRAM_KEY", "").strip()
        project_id = getattr(settings, "TALKAR_DEEPGRAM_PROJECT_ID", "").strip()
        now = datetime.now(timezone.utc)

        if not api_key:
            return VendorCheckResult(
                vendor="deepgram",
                display_name="Deepgram STT",
                units="USD",
                status="not_configured",
                error_message="TALKAR_DEEPGRAM_KEY not configured",
                console_url=CONSOLE_URLS["deepgram"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

        client = await self._get_client()
        headers = {"Authorization": f"Token {api_key}"}

        try:
            # Auto-discover project if omitted
            if not project_id:
                proj_resp = await client.get("https://api.deepgram.com/v1/projects", headers=headers)
                if proj_resp.status_code == 200:
                    projects = proj_resp.json().get("projects", [])
                    if projects:
                        project_id = projects[0].get("project_id", "")
                elif proj_resp.status_code in (401, 403):
                    return VendorCheckResult(
                        vendor="deepgram",
                        display_name="Deepgram STT",
                        units="USD",
                        status="error",
                        error_message="Invalid Deepgram API Key or missing project permissions (STT-only key)",
                        console_url=CONSOLE_URLS["deepgram"],
                        warning_threshold=warning_threshold,
                        critical_threshold=critical_threshold,
                        checked_at=now,
                    )

            if not project_id:
                return VendorCheckResult(
                    vendor="deepgram",
                    display_name="Deepgram STT",
                    units="USD",
                    status="error",
                    error_message="No Deepgram project found or accessible with provided key",
                    console_url=CONSOLE_URLS["deepgram"],
                    warning_threshold=warning_threshold,
                    critical_threshold=critical_threshold,
                    checked_at=now,
                )

            # Query balances
            bal_resp = await client.get(f"https://api.deepgram.com/v1/projects/{project_id}/balances", headers=headers)
            if bal_resp.status_code == 403:
                return VendorCheckResult(
                    vendor="deepgram",
                    display_name="Deepgram STT",
                    units="USD",
                    status="error",
                    error_message="Deepgram key lacks Project Management / Billing scope",
                    console_url=f"https://console.deepgram.com/project/{project_id}",
                    warning_threshold=warning_threshold,
                    critical_threshold=critical_threshold,
                    checked_at=now,
                )

            if bal_resp.status_code != 200:
                return VendorCheckResult(
                    vendor="deepgram",
                    display_name="Deepgram STT",
                    units="USD",
                    status="error",
                    error_message=f"HTTP {bal_resp.status_code}: {bal_resp.text[:200]}",
                    console_url=CONSOLE_URLS["deepgram"],
                    warning_threshold=warning_threshold,
                    critical_threshold=critical_threshold,
                    checked_at=now,
                )

            bal_data = bal_resp.json()
            balances = bal_data.get("balances", [])

            # Edge Case: Pay-as-you-go credit card account has empty balances list
            if not balances:
                return VendorCheckResult(
                    vendor="deepgram",
                    display_name="Deepgram STT",
                    units="payg",
                    status="payg_active",
                    raw_payload=bal_data,
                    console_url=f"https://console.deepgram.com/project/{project_id}",
                    warning_threshold=warning_threshold,
                    critical_threshold=critical_threshold,
                    checked_at=now,
                )

            total_balance = sum(float(b.get("amount", 0.0)) for b in balances)
            units = balances[0].get("units", "USD") if balances else "USD"

            if total_balance <= critical_threshold:
                status = "critical"
            elif total_balance <= warning_threshold:
                status = "warning"
            else:
                status = "healthy"

            return VendorCheckResult(
                vendor="deepgram",
                display_name="Deepgram STT",
                balance=total_balance,
                units=units,
                status=status,
                raw_payload=bal_data,
                console_url=f"https://console.deepgram.com/project/{project_id}",
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )
        except Exception as e:
            logger.error(f"[VENDOR BALANCE] Failed to fetch Deepgram balance: {e}")
            return VendorCheckResult(
                vendor="deepgram",
                display_name="Deepgram STT",
                units="USD",
                status="error",
                error_message=str(e),
                console_url=CONSOLE_URLS["deepgram"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

    async def check_elevenlabs(self, warning_threshold: float = 50000.0, critical_threshold: float = 5000.0) -> VendorCheckResult:
        api_key = getattr(settings, "TALKAR_ELEVENLABS_KEY", "").strip()
        now = datetime.now(timezone.utc)

        if not api_key:
            return VendorCheckResult(
                vendor="elevenlabs",
                display_name="ElevenLabs Voice TTS",
                units="characters",
                status="not_configured",
                error_message="TALKAR_ELEVENLABS_KEY not configured",
                console_url=CONSOLE_URLS["elevenlabs"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

        client = await self._get_client()
        headers = {"xi-api-key": api_key}
        url = "https://api.elevenlabs.io/v1/user/subscription"

        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return VendorCheckResult(
                    vendor="elevenlabs",
                    display_name="ElevenLabs Voice TTS",
                    units="characters",
                    status="error",
                    error_message=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    console_url=CONSOLE_URLS["elevenlabs"],
                    warning_threshold=warning_threshold,
                    critical_threshold=critical_threshold,
                    checked_at=now,
                )

            data = resp.json()
            used_chars = int(data.get("character_count", 0))
            limit_chars = int(data.get("character_limit", 0))
            remaining = max(0, limit_chars - used_chars)
            overages_enabled = bool(data.get("can_extend_character_limit", False))
            sub_status = str(data.get("status", "active")).lower()

            reset_unix = data.get("next_character_count_reset_unix")
            next_reset_at = datetime.fromtimestamp(reset_unix, tz=timezone.utc) if reset_unix else None

            # Edge Case: Account is past due or blocked
            if sub_status in ("blocked", "past_due"):
                status = "critical"
            elif remaining <= critical_threshold:
                # If overages are allowed, account won't crash hard, but still warn
                status = "warning" if overages_enabled else "critical"
            elif remaining <= warning_threshold:
                status = "warning"
            else:
                status = "healthy"

            return VendorCheckResult(
                vendor="elevenlabs",
                display_name="ElevenLabs Voice TTS",
                balance=float(remaining),
                units="characters",
                quota_limit=float(limit_chars),
                status=status,
                overages_enabled=overages_enabled,
                next_reset_at=next_reset_at,
                raw_payload=data,
                console_url=CONSOLE_URLS["elevenlabs"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )
        except Exception as e:
            logger.error(f"[VENDOR BALANCE] Failed to fetch ElevenLabs balance: {e}")
            return VendorCheckResult(
                vendor="elevenlabs",
                display_name="ElevenLabs Voice TTS",
                units="characters",
                status="error",
                error_message=str(e),
                console_url=CONSOLE_URLS["elevenlabs"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

    async def check_smallest(self, warning_threshold: float = 500.0, critical_threshold: float = 50.0) -> VendorCheckResult:
        api_key = getattr(settings, "TALKAR_SMALLEST_AI_KEY", "").strip()
        now = datetime.now(timezone.utc)

        if not api_key:
            return VendorCheckResult(
                vendor="smallest",
                display_name="Smallest AI (Waves TTS)",
                units="credits",
                status="not_configured",
                error_message="TALKAR_SMALLEST_AI_KEY not configured",
                console_url=CONSOLE_URLS["smallest"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

        client = await self._get_client()
        headers = {"Authorization": f"Bearer {api_key}"}

        # Primary endpoint with fallback
        endpoints = [
            "https://api.smallest.ai/atoms/v1/billing/credit-balance",
            "https://api.smallest.ai/atoms/v1/analytics/usage/breakdown",
        ]

        data = None
        last_error = None
        for endpoint in endpoints:
            try:
                resp = await client.get(endpoint, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    break
                elif resp.status_code in (401, 403):
                    last_error = f"Authentication failed: HTTP {resp.status_code}"
                    break
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:150]}"
            except Exception as e:
                last_error = str(e)

        if data is None:
            return VendorCheckResult(
                vendor="smallest",
                display_name="Smallest AI (Waves TTS)",
                units="credits",
                status="error",
                error_message=last_error or "Smallest AI endpoints unreachable",
                console_url=CONSOLE_URLS["smallest"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

        try:
            balance = float(data.get("credit_balance", data.get("balance", data.get("remaining_credits", 0.0))))
            if balance <= critical_threshold:
                status = "critical"
            elif balance <= warning_threshold:
                status = "warning"
            else:
                status = "healthy"

            return VendorCheckResult(
                vendor="smallest",
                display_name="Smallest AI (Waves TTS)",
                balance=balance,
                units="credits",
                status=status,
                raw_payload=data,
                console_url=CONSOLE_URLS["smallest"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )
        except Exception as e:
            return VendorCheckResult(
                vendor="smallest",
                display_name="Smallest AI (Waves TTS)",
                units="credits",
                status="error",
                error_message=f"Failed to parse Smallest response: {e}",
                console_url=CONSOLE_URLS["smallest"],
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                checked_at=now,
            )

    # -------------------------------------------------------------------------
    # Aggregation & Alert Pipeline
    # -------------------------------------------------------------------------

    async def sync_all_vendors(self, force: bool = False) -> dict[str, Any]:
        """Runs concurrent checks on all 4 providers, saves snapshots, handles debouncing & alerts."""
        redis_conn = await redis_client.get_redis()

        # Distributed lock prevents concurrent polling storms
        lock_acquired = False
        if redis_conn:
            try:
                lock_acquired = bool(await redis_conn.set(SYNC_LOCK_KEY, "locked", nx=True, ex=SYNC_LOCK_TTL_SECONDS))
                if not lock_acquired and not force:
                    logger.info("[VENDOR BALANCE] Sync in progress by another worker. Returning cached snapshot.")
                    return await self.get_latest_summary()
            except Exception as e:
                logger.warning(f"[VENDOR BALANCE] Redis lock check failed: {e}")

        try:
            # 1. Load active thresholds from DB
            thresholds = dict(DEFAULT_THRESHOLDS)
            async with AsyncSessionLocal() as session:
                states_res = await session.execute(select(VendorAlertState))
                for row in states_res.scalars().all():
                    thresholds[row.vendor] = {
                        "warning": float(row.warning_threshold),
                        "critical": float(row.critical_threshold),
                    }

            # 2. Execute parallel checks
            p_task = self.check_plivo(thresholds["plivo"]["warning"], thresholds["plivo"]["critical"])
            d_task = self.check_deepgram(thresholds["deepgram"]["warning"], thresholds["deepgram"]["critical"])
            e_task = self.check_elevenlabs(thresholds["elevenlabs"]["warning"], thresholds["elevenlabs"]["critical"])
            s_task = self.check_smallest(thresholds["smallest"]["warning"], thresholds["smallest"]["critical"])

            results: list[VendorCheckResult] = await asyncio.gather(p_task, d_task, e_task, s_task)

            # 3. Store snapshots and process alert states
            now = datetime.now(timezone.utc)
            all_healthy = True

            async with AsyncSessionLocal() as session:
                for res in results:
                    # Save historical snapshot
                    snap = VendorBalanceSnapshot(
                        vendor=res.vendor,
                        balance_numeric=res.balance,
                        units=res.units,
                        quota_limit=res.quota_limit,
                        status=res.status,
                        auto_recharge_enabled=res.auto_recharge,
                        overages_enabled=res.overages_enabled,
                        next_reset_at=res.next_reset_at,
                        error_message=res.error_message,
                        raw_payload=res.raw_payload,
                        checked_at=now,
                    )
                    session.add(snap)

                    if res.status in ("warning", "critical"):
                        all_healthy = False

                    # Process alert transitions & cooldowns
                    await self._evaluate_and_dispatch_alert(session, res, now)

                await session.commit()

            # 4. Cache infrastructure status in Redis for campaign pre-flight checks
            summary = self._build_summary(results, now)
            if redis_conn:
                try:
                    await redis_conn.set(INFRA_HEALTH_KEY, "true" if all_healthy else "false", ex=3600)
                    await redis_conn.set(INFRA_STATUS_KEY, json.dumps(summary, default=str), ex=3600)
                except Exception as e:
                    logger.debug(f"[VENDOR BALANCE] Failed to cache Redis status: {e}")

            return summary

        finally:
            if lock_acquired and redis_conn:
                try:
                    await redis_conn.delete(SYNC_LOCK_KEY)
                except Exception:
                    pass

    async def _evaluate_and_dispatch_alert(
        self, session: Any, result: VendorCheckResult, now: datetime
    ) -> None:
        """Applies 10% hysteresis, cooldown rules, and triggers email notifications."""
        if result.status in ("not_configured", "error", "payg_active"):
            return

        state = await session.get(VendorAlertState, result.vendor)
        if not state:
            state = VendorAlertState(
                vendor=result.vendor,
                current_tier="healthy",
                warning_threshold=result.warning_threshold,
                critical_threshold=result.critical_threshold,
            )
            session.add(state)
            await session.flush()

        prev_tier = state.current_tier or "healthy"
        new_tier = result.status  # "healthy", "warning", or "critical"

        # 10% Hysteresis rule for recovery:
        # If currently in warning/critical, only recover to healthy if balance is >= 110% of warning threshold
        if prev_tier in ("warning", "critical") and new_tier == "healthy":
            recovery_target = float(state.warning_threshold) * 1.10
            if result.balance is not None and result.balance < recovery_target:
                # Keep in warning until safely past hysteresis buffer
                new_tier = "warning"

        # Check 24-hour window cap (Max 3 emails per 24 hours)
        window_start = state.window_start_at or now
        if now - window_start > timedelta(hours=24):
            state.alerts_sent_in_window = 0
            state.window_start_at = now

        should_alert = False
        alert_reason = ""

        if new_tier != prev_tier:
            # Immediate alert on state transition
            if new_tier in ("warning", "critical"):
                should_alert = True
                alert_reason = f"Transitioned from {prev_tier} to {new_tier}"
            elif prev_tier in ("warning", "critical") and new_tier == "healthy":
                # Recovery notification
                should_alert = True
                alert_reason = "Recovered to healthy"
        elif new_tier in ("warning", "critical"):
            # Cooldown reminder check
            cooldown_hrs = 4 if new_tier == "critical" else 12
            last_sent = state.last_alert_sent_at
            if last_sent is None or (now - last_sent) >= timedelta(hours=cooldown_hrs):
                if state.alerts_sent_in_window < 3:
                    should_alert = True
                    alert_reason = f"Periodic reminder (cooldown {cooldown_hrs}h expired)"

        # Dispatch alert if qualified
        if should_alert:
            dispatch_tier = "recovered" if new_tier == "healthy" else new_tier
            active_threshold = float(state.critical_threshold if dispatch_tier == "critical" else state.warning_threshold)
            try:
                sent = await send_vendor_balance_alert(
                    vendor=result.vendor,
                    display_name=result.display_name,
                    tier=dispatch_tier,
                    balance=result.balance,
                    units=result.units,
                    threshold=active_threshold,
                    auto_recharge=result.auto_recharge,
                    overages_enabled=result.overages_enabled,
                    next_reset_at=result.next_reset_at,
                    console_url=result.console_url,
                )
                if sent:
                    state.last_alert_sent_at = now
                    state.last_alert_tier = dispatch_tier
                    state.alerts_sent_in_window = (state.alerts_sent_in_window or 0) + 1
                    state.last_delivery_status = "success"
                    state.last_delivery_error = None
                    logger.info(f"[VENDOR BALANCE ALERT] Sent {dispatch_tier} email for {result.vendor}: {alert_reason}")
                else:
                    state.last_delivery_status = "failed"
                    state.last_delivery_error = "SMTP delivery returned false"
            except Exception as e:
                state.last_delivery_status = "failed"
                state.last_delivery_error = str(e)
                logger.error(f"[VENDOR BALANCE ALERT] Delivery error for {result.vendor}: {e}")

        state.current_tier = new_tier
        state.updated_at = now

    def _build_summary(self, results: list[VendorCheckResult], checked_at: datetime) -> dict[str, Any]:
        vendors_dict = {}
        for r in results:
            vendors_dict[r.vendor] = {
                "vendor": r.vendor,
                "display_name": r.display_name,
                "balance": r.balance,
                "units": r.units,
                "quota_limit": r.quota_limit,
                "status": r.status,
                "auto_recharge": r.auto_recharge,
                "overages_enabled": r.overages_enabled,
                "next_reset_at": r.next_reset_at.isoformat() if r.next_reset_at else None,
                "error_message": r.error_message,
                "console_url": r.console_url,
                "warning_threshold": r.warning_threshold,
                "critical_threshold": r.critical_threshold,
                "checked_at": r.checked_at.isoformat(),
            }

        return {
            "updated_at": checked_at.isoformat(),
            "overall_healthy": all(r.status in ("healthy", "payg_active", "not_configured") for r in results),
            "vendors": vendors_dict,
        }

    async def get_latest_summary(self) -> dict[str, Any]:
        """Returns the most recent snapshot for each vendor from database."""
        now = datetime.now(timezone.utc)
        display_names = {
            "plivo": "Plivo Telephony",
            "deepgram": "Deepgram STT",
            "elevenlabs": "ElevenLabs Voice TTS",
            "smallest": "Smallest AI (Waves TTS)",
        }

        try:
            async with AsyncSessionLocal() as session:
                # Get latest snapshot per vendor
                vendors_dict = {}
                for vendor_name in ("plivo", "deepgram", "elevenlabs", "smallest"):
                    snap_res = await session.execute(
                        select(VendorBalanceSnapshot)
                        .where(VendorBalanceSnapshot.vendor == vendor_name)
                        .order_by(desc(VendorBalanceSnapshot.checked_at))
                        .limit(1)
                    )
                    snap = snap_res.scalars().first()

                    state_res = await session.execute(select(VendorAlertState).where(VendorAlertState.vendor == vendor_name))
                    state = state_res.scalars().first()

                    warning_th = float(state.warning_threshold) if state else DEFAULT_THRESHOLDS[vendor_name]["warning"]
                    critical_th = float(state.critical_threshold) if state else DEFAULT_THRESHOLDS[vendor_name]["critical"]

                    if snap:
                        vendors_dict[vendor_name] = {
                            "vendor": vendor_name,
                            "display_name": display_names[vendor_name],
                            "balance": float(snap.balance_numeric) if snap.balance_numeric is not None else None,
                            "units": snap.units,
                            "quota_limit": float(snap.quota_limit) if snap.quota_limit is not None else None,
                            "status": snap.status,
                            "auto_recharge": snap.auto_recharge_enabled or False,
                            "overages_enabled": snap.overages_enabled or False,
                            "next_reset_at": snap.next_reset_at.isoformat() if snap.next_reset_at else None,
                            "error_message": snap.error_message,
                            "console_url": CONSOLE_URLS[vendor_name],
                            "warning_threshold": warning_th,
                            "critical_threshold": critical_th,
                            "checked_at": snap.checked_at.isoformat() if snap.checked_at else now.isoformat(),
                        }
                    else:
                        vendors_dict[vendor_name] = {
                            "vendor": vendor_name,
                            "display_name": display_names[vendor_name],
                            "balance": None,
                            "units": "units",
                            "status": "not_configured",
                            "console_url": CONSOLE_URLS[vendor_name],
                            "warning_threshold": warning_th,
                            "critical_threshold": critical_th,
                            "checked_at": now.isoformat(),
                        }

                return {
                    "updated_at": now.isoformat(),
                    "overall_healthy": all(
                        v["status"] in ("healthy", "payg_active", "not_configured") for v in vendors_dict.values()
                    ),
                    "vendors": vendors_dict,
                }
        except Exception as e:
            logger.error(f"[VENDOR BALANCE] Failed to get latest summary from DB: {e}")
            return {
                "updated_at": now.isoformat(),
                "overall_healthy": True,
                "vendors": {
                    v: {
                        "vendor": v,
                        "display_name": display_names[v],
                        "balance": None,
                        "units": "units",
                        "status": "not_configured",
                        "console_url": CONSOLE_URLS[v],
                        "warning_threshold": DEFAULT_THRESHOLDS[v]["warning"],
                        "critical_threshold": DEFAULT_THRESHOLDS[v]["critical"],
                        "checked_at": now.isoformat(),
                        "error_message": "Initializing vendor balance snapshot store...",
                    }
                    for v in display_names
                },
            }
