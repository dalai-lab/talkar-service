from config import settings
import logging
from typing import Dict, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import json

logger = logging.getLogger(__name__)

# Create an async engine connected to Dograh's database
dograh_engine = create_async_engine(
    settings.DOGRAH_DB_URL,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
)
DograhSessionLocal = sessionmaker(
    bind=dograh_engine, class_=AsyncSession, expire_on_commit=False
)

async def upsert_org_config(org_id: int, key: str, value: Any):
    """Upserts an organization config in Dograh via direct DB access"""
    async with DograhSessionLocal() as session:
        try:
            # First check if the organization exists
            result = await session.execute(
                text("SELECT id FROM organizations WHERE id = :org_id"),
                {"org_id": org_id}
            )
            if not result.scalar_one_or_none():
                logger.error(f"Cannot upsert config: Dograh Organization {org_id} not found.")
                return

            value_json = json.dumps(value)
            
            # Using PostgreSQL UPSERT (INSERT ... ON CONFLICT)
            # We assume a unique constraint exists on (organization_id, key) in Dograh's DB.
            # If not, we fall back to a manual check-and-update.
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
            logger.info(f"Successfully upserted config '{key}' for Dograh Org {org_id}")
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to upsert config '{key}' for org {org_id}: {e}")
            raise

async def get_org_config(org_id: int, key: str) -> Any:
    """Gets an organization config from Dograh via direct DB access"""
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
    """Block all calls for a Dograh org by setting CONCURRENT_CALL_LIMIT to 0.
    
    Called when a customer is suspended or has zero balance. This is the actual
    enforcement layer — without this, Dograh keeps serving calls regardless of
    the Talkar-side status.
    """
    await upsert_org_config(org_id, "CONCURRENT_CALL_LIMIT", {"value": 0})
    logger.info(f"Blocked calls for Dograh org {org_id} (CONCURRENT_CALL_LIMIT=0)")

async def restore_org_calls(org_id: int, tier: str = "starter", limit: int = None, group_id: int = None):
    """Restore call capacity for a Dograh org after reactivation.
    
    Sets CONCURRENT_CALL_LIMIT back to the tier's configured limit.
    Optionally sets CONCURRENCY_GROUP_ID to share concurrency limits across sub-orgs.
    """
    if limit is None:
        from config import TIER_CONFIG
        limit = TIER_CONFIG.get(tier, TIER_CONFIG["starter"])["concurrent_call_limit"]
    await upsert_org_config(org_id, "CONCURRENT_CALL_LIMIT", {"value": limit})
    if group_id is not None:
        await upsert_org_config(org_id, "CONCURRENCY_GROUP_ID", {"value": group_id})
    logger.info(f"Restored calls for Dograh org {org_id} to limit={limit} (group={group_id}, tier={tier})")

async def get_run(run_id: int):
    """Get a specific run from Dograh by ID via DB."""
    async with DograhSessionLocal() as session:
        result = await session.execute(
            text(r"""
                SELECT wfr.id, w.organization_id, wfr.state, 
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
            return {"id": row.id, "organization_id": row.organization_id, "status": row.state, "duration_seconds": dur}
        return None

async def get_completed_runs(hours: int = 25):
    """Get completed runs from Dograh in the last N hours via DB."""
    async with DograhSessionLocal() as session:
        result = await session.execute(
            text(r"""
                SELECT wfr.id, w.organization_id, wfr.state, 
                       CASE 
                           WHEN (wfr.usage_info->>'call_duration_seconds') ~ '^[0-9]+(\.[0-9]+)?$' 
                           THEN CAST(wfr.usage_info->>'call_duration_seconds' AS FLOAT)
                           ELSE 0.0 
                       END as duration_seconds, 
                       wfr.created_at 
                FROM workflow_runs wfr
                JOIN workflows w ON w.id = wfr.workflow_id
                WHERE wfr.state = 'completed' AND wfr.created_at >= NOW() - make_interval(hours => :hours)
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
                "status": r.state,
                "duration_seconds": dur,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return runs

async def delete_org(org_id: int):
    """Delete an organization in Dograh via DB."""
    async with DograhSessionLocal() as session:
        try:
            # Bypassed for testing: Do not actually delete Dograh orgs
            # await session.execute(text("DELETE FROM organizations WHERE id = :org_id"), {"org_id": org_id})
            # await session.commit()
            logger.info(f"Bypassed deleting Dograh org {org_id} (Testing Mode)")
        except Exception as e:
            logger.error(f"Failed to delete org {org_id}: {e}")

async def archive_org(org_id: int):
    """Archive an organization in Dograh via DB."""
    async with DograhSessionLocal() as session:
        try:
            # Assuming Dograh organizations table has an 'archived' or 'is_archived' column.
            # If not, this might need adjustment based on Dograh's actual archiving schema.
            # Since Dograh uses boolean flags or soft deletes:
            # Bypassed for testing: Do not actually archive or delete Dograh orgs
            # await session.execute(text("UPDATE organizations SET is_active = FALSE WHERE id = :org_id"), {"org_id": org_id})
            # await session.commit()
            logger.info(f"Bypassed archiving Dograh org {org_id} (Testing Mode)")
        except Exception as e:
            logger.error(f"Failed to archive org {org_id}: {e}")

async def verify_dograh_org_user(org_id: int, user_id: int | None = None, email: str | None = None) -> bool | None:
    """
    Verifies that a given user_id or email belongs to the specified Dograh organization.
    Returns:
        True: Explicitly verified in Dograh DB.
        False: Explicitly NOT found in Dograh DB for this org.
        None: Dograh DB was unreachable or could not be queried.
    """
    if not org_id or (user_id is None and not email):
        return False
    try:
        async with DograhSessionLocal() as session:
            org_check = await session.execute(
                text("SELECT id FROM organizations WHERE id = :org_id"),
                {"org_id": org_id}
            )
            if not org_check.scalar_one_or_none():
                return False

            if user_id is not None and email:
                res = await session.execute(
                    text("""
                        SELECT 1 FROM organization_users ou
                        JOIN users u ON u.id = ou.user_id
                        WHERE ou.organization_id = :org_id
                          AND ou.user_id = :user_id
                          AND LOWER(u.email) = LOWER(:email)
                        LIMIT 1
                    """),
                    {"org_id": org_id, "user_id": user_id, "email": email.strip()}
                )
                return res.scalar_one_or_none() is not None
            elif user_id is not None:
                res = await session.execute(
                    text("""
                        SELECT 1 FROM organization_users ou
                        WHERE ou.organization_id = :org_id
                          AND ou.user_id = :user_id
                        LIMIT 1
                    """),
                    {"org_id": org_id, "user_id": user_id}
                )
                return res.scalar_one_or_none() is not None
            elif email:
                res = await session.execute(
                    text("""
                        SELECT 1 FROM organization_users ou
                        JOIN users u ON u.id = ou.user_id
                        WHERE ou.organization_id = :org_id
                          AND LOWER(u.email) = LOWER(:email)
                        LIMIT 1
                    """),
                    {"org_id": org_id, "email": email.strip()}
                )
                return res.scalar_one_or_none() is not None
            return False
    except Exception as e:
        logger.warning(f"Could not query Dograh DB to verify org {org_id} user: {e}")
        return None

