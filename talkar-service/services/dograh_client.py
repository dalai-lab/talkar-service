from config import settings
import logging
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import json
import httpx
import unittest.mock

logger = logging.getLogger(__name__)

# Lazy async engine connected to Dograh's database (only instantiated if direct DB is needed)
dograh_engine = None
_sessionmaker_instance = None


def _has_dograh_db() -> bool:
    """Check if direct Dograh DB URL is configured."""
    db_url = getattr(settings, "DOGRAH_DB_URL", None)
    return bool(db_url and str(db_url).strip().lower() not in ("", "none"))


def _get_dograh_engine():
    """Lazily create the asyncpg engine for Dograh's database."""
    global dograh_engine
    if dograh_engine is None:
        db_url = getattr(settings, "DOGRAH_DB_URL", None)
        if not db_url or str(db_url).strip().lower() in ("", "none"):
            return None
        try:
            dograh_engine = create_async_engine(
                db_url,
                echo=settings.DB_ECHO,
                pool_size=settings.DB_POOL_SIZE,
                max_overflow=settings.DB_MAX_OVERFLOW,
                pool_timeout=settings.DB_POOL_TIMEOUT,
                pool_recycle=settings.DB_POOL_RECYCLE,
                pool_pre_ping=True,
            )
        except Exception as e:
            logger.warning(f"Could not initialize dograh_engine: {e}")
            return None
    return dograh_engine


class _DograhSessionMakerProxy:
    """Callable proxy for DograhSessionLocal that delays engine creation until first call."""
    def __call__(self, *args, **kwargs):
        global _sessionmaker_instance
        if _sessionmaker_instance is None:
            engine = _get_dograh_engine()
            if engine is None:
                raise RuntimeError("Dograh database is not configured (DOGRAH_DB_URL is unset or invalid).")
            _sessionmaker_instance = sessionmaker(
                bind=engine, class_=AsyncSession, expire_on_commit=False
            )
        return _sessionmaker_instance(*args, **kwargs)


DograhSessionLocal = _DograhSessionMakerProxy()


def _is_mocked() -> bool:
    """Check if DograhSessionLocal has been replaced with a mock in unit tests."""
    return isinstance(DograhSessionLocal, (unittest.mock.Mock, unittest.mock.MagicMock, unittest.mock.AsyncMock)) or hasattr(DograhSessionLocal, "assert_called")


def _get_api_headers() -> dict[str, str]:
    token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "") or "change-me-in-production-billing-token"
    headers = {
        "Content-Type": "application/json",
        "X-Service-Token": token,
        "Authorization": f"Bearer {token}",
    }
    admin_token = getattr(settings, "DOGRAH_ADMIN_TOKEN", "")
    if admin_token:
        headers["X-API-Key"] = admin_token
    return headers


async def _request_dograh_api(method: str, path: str, **kwargs) -> Optional[httpx.Response]:
    """Execute an authenticated HTTP request to Dograh internal REST API."""
    if _is_mocked():
        return None

    api_url = getattr(settings, "DOGRAH_API_URL", None)
    if not api_url or str(api_url).strip().lower() in ("", "none", "false"):
        return None

    url = f"{api_url.rstrip('/')}{path}"
    headers = _get_api_headers()
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))
    timeout = kwargs.pop("timeout", 5.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(method, url, headers=headers, **kwargs)


async def upsert_org_config(org_id: int, key: str, value: Any):
    """Upserts an organization config in Dograh via REST API with DB fallback."""
    try:
        resp = await _request_dograh_api(
            "POST",
            f"/api/v1/internal/org-config/{org_id}",
            json={"key": key, "value": value},
        )
        if resp:
            if resp.status_code == 200:
                logger.info(f"Successfully upserted config '{key}' for Dograh Org {org_id} via REST API")
                return
            elif resp.status_code == 404:
                logger.error(f"Cannot upsert config: Dograh Organization {org_id} not found.")
                return
            else:
                logger.warning(f"Dograh REST API upsert_org_config returned status {resp.status_code}")
    except Exception as e:
        logger.debug(f"REST call to Dograh failed for upsert_org_config ({org_id}): {e}. Attempting DB fallback.")

    if not _has_dograh_db() and not _is_mocked():
        logger.error(f"Failed to upsert config '{key}' for org {org_id}: REST API failed and Dograh DB not configured.")
        return

    async with DograhSessionLocal() as session:
        try:
            result = await session.execute(
                text("SELECT id FROM organizations WHERE id = :org_id"),
                {"org_id": org_id}
            )
            if not result.scalar_one_or_none():
                logger.error(f"Cannot upsert config: Dograh Organization {org_id} not found.")
                return

            value_json = json.dumps(value)
            check_res = await session.execute(
                text("SELECT id FROM organization_configurations WHERE organization_id = :org_id AND key = :key"),
                {"org_id": org_id, "key": key}
            )
            existing_id = check_res.scalar_one_or_none()

            if existing_id:
                await session.execute(
                    text("UPDATE organization_configurations SET value = CAST(:value AS jsonb), updated_at = now() WHERE id = :id"),
                    {"value": value_json, "id": existing_id}
                )
            else:
                await session.execute(
                    text("INSERT INTO organization_configurations (organization_id, key, value, created_at, updated_at) VALUES (:org_id, :key, CAST(:value AS jsonb), now(), now())"),
                    {"org_id": org_id, "key": key, "value": value_json}
                )

            await session.commit()
            logger.info(f"Successfully upserted config '{key}' for Dograh Org {org_id} via direct DB")
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to upsert config '{key}' for org {org_id}: {e}")
            raise


async def get_org_config(org_id: int, key: str) -> Any:
    """Gets an organization config from Dograh via REST API with DB fallback."""
    try:
        resp = await _request_dograh_api("GET", f"/api/v1/internal/org-config/{org_id}/{key}")
        if resp and resp.status_code == 200:
            data = resp.json()
            return data.get("value")
    except Exception as e:
        logger.debug(f"REST call to Dograh failed for get_org_config ({org_id}, {key}): {e}. Attempting DB fallback.")

    if not _has_dograh_db() and not _is_mocked():
        return None

    async with DograhSessionLocal() as session:
        try:
            result = await session.execute(
                text("SELECT value FROM organization_configurations WHERE organization_id = :org_id AND key = :key"),
                {"org_id": org_id, "key": key}
            )
            val = result.scalar_one_or_none()
            return val
        except Exception as e:
            logger.error(f"Failed to get config '{key}' for org {org_id}: {e}")
            raise


async def block_org_calls(org_id: int):
    """Block all calls for a Dograh org by setting CONCURRENT_CALL_LIMIT to 0."""
    await upsert_org_config(org_id, "CONCURRENT_CALL_LIMIT", {"value": 0})
    logger.info(f"Blocked calls for Dograh org {org_id} (CONCURRENT_CALL_LIMIT=0)")


async def restore_org_calls(org_id: int, tier: str = "starter", limit: int = None, group_id: int = None, is_custom_cap: bool = None):
    """Restore call capacity for a Dograh org after reactivation."""
    if limit is None:
        from config import TIER_CONFIG
        limit = TIER_CONFIG.get(tier, TIER_CONFIG["starter"])["concurrent_call_limit"]
    payload = {"value": limit}
    if is_custom_cap is not None:
        payload["is_custom_cap"] = is_custom_cap
    await upsert_org_config(org_id, "CONCURRENT_CALL_LIMIT", payload)
    if group_id is not None:
        await upsert_org_config(org_id, "CONCURRENCY_GROUP_ID", {"value": group_id})
    logger.info(f"Restored calls for Dograh org {org_id} to limit={limit} (group={group_id}, tier={tier}, is_custom_cap={is_custom_cap})")


async def get_run(run_id: int):
    """Get a specific run from Dograh by ID via REST API with DB fallback."""
    try:
        resp = await _request_dograh_api("GET", f"/api/v1/internal/runs/{run_id}")
        if resp:
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                return None
    except Exception as e:
        logger.debug(f"REST call to Dograh failed for get_run ({run_id}): {e}. Attempting DB fallback.")

    if not _has_dograh_db() and not _is_mocked():
        return None

    async with DograhSessionLocal() as session:
        result = await session.execute(
            text(r"""
                SELECT wfr.id, w.organization_id, wfr.workflow_id, wfr.state, wfr.mode, 
                       CASE 
                           WHEN (wfr.usage_info->>'call_duration_seconds') ~ '^[0-9]+(\.[0-9]+)?$' 
                           THEN CAST(wfr.usage_info->>'call_duration_seconds' AS FLOAT)
                           ELSE 0.0 
                       END as duration_seconds 
                FROM workflow_runs wfr
                JOIN workflows w ON w.id = wfr.workflow_id
                WHERE wfr.id = :run_id
            """),
            {"run_id": run_id}
        )
        row = result.fetchone()
        if row:
            try:
                dur = float(row.duration_seconds) if row.duration_seconds is not None else 0.0
            except (ValueError, TypeError):
                dur = 0.0
            return {
                "id": row.id,
                "organization_id": row.organization_id,
                "workflow_id": getattr(row, "workflow_id", None),
                "status": row.state,
                "mode": getattr(row, "mode", None),
                "duration_seconds": dur,
            }
        return None


async def get_completed_runs(hours: int = 25):
    """Get completed runs from Dograh in the last N hours via REST API with DB fallback."""
    try:
        resp = await _request_dograh_api("GET", "/api/v1/internal/runs/completed", params={"hours": hours})
        if resp and resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug(f"REST call to Dograh failed for get_completed_runs: {e}. Attempting DB fallback.")

    if not _has_dograh_db() and not _is_mocked():
        return []

    async with DograhSessionLocal() as session:
        result = await session.execute(
            text(r"""
                SELECT wfr.id, w.organization_id, wfr.workflow_id, wfr.state, wfr.mode, 
                       CASE 
                           WHEN (wfr.usage_info->>'call_duration_seconds') ~ '^[0-9]+(\.[0-9]+)?$' 
                           THEN CAST(wfr.usage_info->>'call_duration_seconds' AS FLOAT)
                           ELSE 0.0 
                       END as duration_seconds, 
                       wfr.created_at 
                FROM workflow_runs wfr
                JOIN workflows w ON w.id = wfr.workflow_id
                WHERE wfr.state = 'completed'
                  AND wfr.created_at >= NOW() - make_interval(hours => :hours)
                  AND (wfr.mode IS NULL OR wfr.mode != 'textchat')
            """),
            {"hours": hours}
        )
        rows = result.fetchall()
        runs = []
        for r in rows:
            try:
                dur = float(r.duration_seconds) if r.duration_seconds is not None else 0.0
            except (ValueError, TypeError):
                dur = 0.0
            runs.append({
                "id": r.id,
                "organization_id": r.organization_id,
                "workflow_id": getattr(r, "workflow_id", None),
                "status": r.state,
                "mode": getattr(r, "mode", None),
                "duration_seconds": dur,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return runs


async def delete_org(org_id: int):
    """Delete an organization in Dograh via REST API with DB fallback."""
    try:
        resp = await _request_dograh_api("DELETE", f"/api/v1/internal/organizations/{org_id}")
        if resp and resp.status_code in (200, 204, 404):
            logger.info(f"Successfully requested delete for Dograh org {org_id} via REST API")
            return
    except Exception as e:
        logger.debug(f"REST call failed for delete_org: {e}")

    if not _has_dograh_db() and not _is_mocked():
        return

    async with DograhSessionLocal() as session:
        try:
            # 1. Nullify user selected_organization_id
            await session.execute(
                text("UPDATE users SET selected_organization_id = NULL WHERE selected_organization_id = :org_id"),
                {"org_id": org_id}
            )
            # 2. Delete organization_users association
            await session.execute(
                text("DELETE FROM organization_users WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            # 3. Disconnect workflow runs from campaigns and delete campaigns
            await session.execute(
                text("""
                    UPDATE workflow_runs SET campaign_id = NULL
                    WHERE campaign_id IN (SELECT id FROM campaigns WHERE organization_id = :org_id)
                """),
                {"org_id": org_id}
            )
            await session.execute(
                text("DELETE FROM campaigns WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            # 4. Delete runs and definitions for workflows belonging to this org
            await session.execute(
                text("""
                    DELETE FROM workflow_runs
                    WHERE workflow_id IN (SELECT id FROM workflows WHERE organization_id = :org_id)
                """),
                {"org_id": org_id}
            )
            await session.execute(
                text("""
                    DELETE FROM workflow_definitions
                    WHERE workflow_id IN (SELECT id FROM workflows WHERE organization_id = :org_id)
                """),
                {"org_id": org_id}
            )
            await session.execute(
                text("DELETE FROM workflows WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            # 5. Delete folders
            await session.execute(
                text("DELETE FROM folders WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            # 6. Delete integrations
            await session.execute(
                text("DELETE FROM integrations WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            # 7. Delete usage cycles
            await session.execute(
                text("DELETE FROM organization_usage_cycles WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            # 8. Delete organization
            await session.execute(
                text("DELETE FROM organizations WHERE id = :org_id"),
                {"org_id": org_id}
            )
            await session.commit()
            logger.info(f"Successfully deleted Dograh org {org_id} via direct DB fallback")
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to delete org {org_id}: {e}")
            raise


async def archive_org(org_id: int):
    """Archive an organization in Dograh via REST API with DB fallback."""
    try:
        resp = await _request_dograh_api("POST", f"/api/v1/internal/organizations/{org_id}/archive")
        if resp and resp.status_code in (200, 204, 404):
            logger.info(f"Successfully requested archive for Dograh org {org_id} via REST API")
            return
    except Exception as e:
        logger.debug(f"REST call failed for archive_org: {e}")

    if not _has_dograh_db() and not _is_mocked():
        return

    # 1. Block calls & mark org status archived in configuration
    try:
        await upsert_org_config(org_id, "CONCURRENT_CALL_LIMIT", {"value": 0})
        await upsert_org_config(org_id, "TALKAR_ORG_STATUS", {"value": "archived"})
    except Exception as e:
        logger.error(f"Failed to set archive configuration for Dograh org {org_id}: {e}")

    # 2. Deactivate API keys, telephony numbers, and workflows in direct DB
    async with DograhSessionLocal() as session:
        try:
            await session.execute(
                text("UPDATE api_keys SET is_active = false, archived_at = now() WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            await session.execute(
                text("UPDATE telephony_phone_numbers SET is_active = false WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            await session.execute(
                text("UPDATE workflows SET status = 'archived' WHERE organization_id = :org_id AND status != 'archived'"),
                {"org_id": org_id}
            )
            await session.commit()
            logger.info(f"Successfully archived Dograh org {org_id} via direct DB fallback")
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to archive org {org_id}: {e}")
            raise


async def verify_dograh_org_user(
    org_id: int,
    user_id: int | None = None,
    email: str | None = None,
    require_owner: bool = False
) -> bool | None:
    """Verifies that a given user_id or email belongs to the specified Dograh organization,
    and optionally verifies that they are the organization's owner/admin.
    
    Returns:
        True: Explicitly verified in Dograh.
        False: Explicitly NOT found (or not owner when require_owner=True).
        None: Dograh service was unreachable and DB could not be queried.
    """
    if not org_id or (user_id is None and not email):
        return False

    params = {"org_id": org_id, "require_owner": require_owner}
    if user_id is not None:
        params["user_id"] = user_id
    if email:
        params["email"] = email.strip()

    try:
        resp = await _request_dograh_api("GET", "/api/v1/internal/org-users/verify", params=params)
        if resp and resp.status_code == 200:
            return bool(resp.json().get("verified", False))
    except Exception as e:
        logger.debug(f"REST call to Dograh failed for verify_dograh_org_user: {e}. Attempting DB fallback.")

    if not _has_dograh_db() and not _is_mocked():
        logger.warning(f"Could not reach Dograh API and DB not configured to verify org {org_id} user")
        return None

    try:
        async with DograhSessionLocal() as session:
            org_check = await session.execute(
                text("SELECT id FROM organizations WHERE id = :org_id"),
                {"org_id": org_id}
            )
            if not org_check.scalar_one_or_none():
                return False

            # Check membership first
            clean_email = email.strip() if email else None
            user_row = None
            if user_id is not None and clean_email:
                res = await session.execute(
                    text("""
                        SELECT u.id, u.email, u.is_superuser FROM organization_users ou
                        JOIN users u ON u.id = ou.user_id
                        WHERE ou.organization_id = :org_id
                          AND ou.user_id = :user_id
                          AND LOWER(u.email) = LOWER(:email)
                        LIMIT 1
                    """),
                    {"org_id": org_id, "user_id": user_id, "email": clean_email}
                )
                user_row = res.fetchone()
            elif user_id is not None:
                res = await session.execute(
                    text("""
                        SELECT u.id, u.email, u.is_superuser FROM organization_users ou
                        JOIN users u ON u.id = ou.user_id
                        WHERE ou.organization_id = :org_id
                          AND ou.user_id = :user_id
                        LIMIT 1
                    """),
                    {"org_id": org_id, "user_id": user_id}
                )
                user_row = res.fetchone()
            elif clean_email:
                res = await session.execute(
                    text("""
                        SELECT u.id, u.email, u.is_superuser FROM organization_users ou
                        JOIN users u ON u.id = ou.user_id
                        WHERE ou.organization_id = :org_id
                          AND LOWER(u.email) = LOWER(:email)
                        LIMIT 1
                    """),
                    {"org_id": org_id, "email": clean_email}
                )
                user_row = res.fetchone()

            if not user_row:
                return False

            if not require_owner:
                return True

            # --- Owner / Admin Verification (Issue 8 Prevention) ---
            # 1. Platform superuser check
            if getattr(user_row, "is_superuser", False):
                return True

            matched_uid = user_row.id
            matched_uemail = user_row.email

            # 2. Check ORGANIZATION_OWNER_ID configuration
            cfg_check = await session.execute(
                text("SELECT value FROM organization_configurations WHERE organization_id = :org_id AND key = 'ORGANIZATION_OWNER_ID'"),
                {"org_id": org_id}
            )
            cfg_val = cfg_check.scalar_one_or_none()
            if cfg_val:
                if isinstance(cfg_val, dict):
                    if cfg_val.get("user_id") == matched_uid:
                        return True
                    if matched_uemail and cfg_val.get("email") and cfg_val.get("email").strip().lower() == matched_uemail.strip().lower():
                        return True
                elif str(cfg_val) == str(matched_uid):
                    return True

            # 3. Check APIKeyModel creator (Default API Key created_by)
            api_key_check = await session.execute(
                text("SELECT created_by FROM api_keys WHERE organization_id = :org_id ORDER BY id ASC LIMIT 1"),
                {"org_id": org_id}
            )
            creator_id = api_key_check.scalar_one_or_none()
            if creator_id is not None and creator_id == matched_uid:
                return True

            # 4. Check if matched user is the sole member of the organization
            count_res = await session.execute(
                text("SELECT count(*) FROM organization_users WHERE organization_id = :org_id"),
                {"org_id": org_id}
            )
            total_members = count_res.scalar()
            if total_members == 1:
                return True

            # Otherwise, the user is merely an invited team member / collaborator
            return False
    except Exception as e:
        logger.warning(f"Could not query Dograh DB to verify org {org_id} user: {e}")
        return None


async def sync_phone_number_to_dograh(org_id: int, phone_number: str, workflow_id: int | None = None):
    """Sync an assigned phone number into Dograh via REST API with DB fallback."""
    clean_number = phone_number.strip()
    if not clean_number:
        return

    try:
        resp = await _request_dograh_api(
            "POST",
            "/api/v1/internal/telephony/sync-number",
            json={"org_id": org_id, "phone_number": clean_number, "workflow_id": workflow_id},
        )
        if resp and resp.status_code == 200:
            logger.info(f"Successfully synced phone number {clean_number} to Dograh org {org_id} via REST API")
            return
    except Exception as e:
        logger.debug(f"REST call to Dograh failed for sync_phone_number_to_dograh: {e}. Attempting DB fallback.")

    if not _has_dograh_db() and not _is_mocked():
        logger.error(f"Failed to sync phone number {clean_number} to Dograh org {org_id}: REST API failed and DB not configured.")
        raise RuntimeError("Dograh service unreachable and DB not configured.")

    normalized = clean_number if clean_number.startswith("+") else f"+{clean_number}"
    country_code = "IN" if normalized.startswith("+91") else None

    async with DograhSessionLocal() as session:
        try:
            cfg_res = await session.execute(
                text("SELECT id FROM telephony_configurations WHERE organization_id = :org_id ORDER BY id ASC LIMIT 1"),
                {"org_id": org_id}
            )
            cfg_id = cfg_res.scalar_one_or_none()

            if not cfg_id:
                insert_cfg = await session.execute(
                    text("""
                        INSERT INTO telephony_configurations (organization_id, name, provider, credentials, is_default_outbound, created_at, updated_at)
                        VALUES (:org_id, 'talkar_telephony', 'plivo', '{}'::json, true, now(), now())
                        RETURNING id
                    """),
                    {"org_id": org_id}
                )
                cfg_id = insert_cfg.scalar_one()

            target_wf_id = workflow_id
            if target_wf_id is None:
                try:
                    wf_res = await session.execute(
                        text("SELECT id FROM workflows WHERE organization_id = :org_id AND status = 'active' ORDER BY id ASC LIMIT 1"),
                        {"org_id": org_id}
                    )
                    target_wf_id = wf_res.scalar_one_or_none()
                except Exception as wf_err:
                    logger.debug(f"Could not query default workflow for org {org_id}: {wf_err}")

            num_res = await session.execute(
                text("SELECT id FROM telephony_phone_numbers WHERE organization_id = :org_id AND address_normalized = :normalized"),
                {"org_id": org_id, "normalized": normalized}
            )
            existing_num_id = num_res.scalar_one_or_none()

            if existing_num_id:
                await session.execute(
                    text("""
                        UPDATE telephony_phone_numbers 
                        SET is_active = true, 
                            inbound_workflow_id = COALESCE(:target_wf_id, inbound_workflow_id),
                            updated_at = now() 
                        WHERE id = :id
                    """),
                    {"id": existing_num_id, "target_wf_id": target_wf_id}
                )
            else:
                await session.execute(
                    text("""
                        INSERT INTO telephony_phone_numbers (
                            organization_id, telephony_configuration_id, address, address_normalized,
                            address_type, country_code, inbound_workflow_id, is_active, is_default_caller_id, extra_metadata,
                            created_at, updated_at
                        ) VALUES (
                            :org_id, :cfg_id, :address, :normalized,
                            'pstn_number', :country_code, :target_wf_id, true, true, '{}'::json,
                            now(), now()
                        )
                    """),
                    {
                        "org_id": org_id,
                        "cfg_id": cfg_id,
                        "address": clean_number,
                        "normalized": normalized,
                        "country_code": country_code,
                        "target_wf_id": target_wf_id,
                    }
                )

            await session.commit()
            logger.info(f"Successfully synced phone number {clean_number} (inbound_workflow_id={target_wf_id}) to Dograh org {org_id} via direct DB")
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to sync phone number {clean_number} to Dograh org {org_id}: {e}")
            raise


async def release_phone_number_from_dograh(org_id: int, phone_number: str):
    """Deactivate a released phone number in Dograh via REST API with DB fallback."""
    clean_number = phone_number.strip()
    if not clean_number:
        return

    try:
        resp = await _request_dograh_api(
            "POST",
            "/api/v1/internal/telephony/release-number",
            json={"org_id": org_id, "phone_number": clean_number},
        )
        if resp and resp.status_code == 200:
            logger.info(f"Successfully released phone number {clean_number} from Dograh org {org_id} via REST API")
            return
    except Exception as e:
        logger.debug(f"REST call to Dograh failed for release_phone_number_from_dograh: {e}. Attempting DB fallback.")

    if not _has_dograh_db() and not _is_mocked():
        logger.warning(f"Could not release phone number {clean_number} from Dograh org {org_id}: REST API failed and DB not configured.")
        return

    normalized = clean_number if clean_number.startswith("+") else f"+{clean_number}"

    try:
        async with DograhSessionLocal() as session:
            await session.execute(
                text("""
                    UPDATE telephony_phone_numbers 
                    SET is_active = false, updated_at = now() 
                    WHERE organization_id = :org_id AND address_normalized = :normalized
                """),
                {"org_id": org_id, "normalized": normalized}
            )
            await session.commit()
            logger.info(f"Successfully released phone number {clean_number} from Dograh org {org_id} via direct DB")
    except Exception as e:
        logger.warning(f"Failed to deactivate phone number {clean_number} in Dograh org {org_id}: {e}")


async def get_org_workflows(org_id: int) -> list[dict]:
    """Retrieve workflows belonging to an organization for Talkar agent syncing."""
    try:
        resp = await _request_dograh_api("GET", f"/api/v1/internal/org-workflows/{org_id}")
        if resp and resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.debug(f"REST call to Dograh failed for get_org_workflows ({org_id}): {e}. Attempting DB fallback.")

    if not _has_dograh_db() and not _is_mocked():
        return []

    async with DograhSessionLocal() as session:
        result = await session.execute(
            text("SELECT id, name, status, created_at FROM workflows WHERE organization_id = :org_id"),
            {"org_id": org_id}
        )
        rows = result.fetchall()
        return [
            {
                "id": r.id,
                "name": r.name,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in rows
        ]
