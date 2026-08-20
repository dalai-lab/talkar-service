from config import settings
import logging
from typing import Dict, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
import json

logger = logging.getLogger(__name__)

# Create an async engine connected to Dograh's database
dograh_engine = create_async_engine(settings.DOGRAH_DB_URL, echo=False)
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

async def restore_org_calls(org_id: int, tier: str = "starter"):
    """Restore call capacity for a Dograh org after reactivation.
    
    Sets CONCURRENT_CALL_LIMIT back to the tier's configured limit.
    """
    from config import TIER_CONFIG
    limit = TIER_CONFIG.get(tier, TIER_CONFIG["starter"])["concurrent_call_limit"]
    await upsert_org_config(org_id, "CONCURRENT_CALL_LIMIT", {"value": limit})
    logger.info(f"Restored calls for Dograh org {org_id} to limit={limit} (tier={tier})")

async def get_run(run_id: int):
    """Get a specific run from Dograh by ID via DB."""
    async with DograhSessionLocal() as session:
        result = await session.execute(
            text("SELECT id, organization_id, status, CAST(usage_info->>'call_duration_seconds' AS FLOAT) as duration_seconds FROM workflow_runs WHERE id = :run_id"),
            {"run_id": run_id}
        )
        row = result.fetchone()
        if row:
            return {"id": row.id, "organization_id": row.organization_id, "status": row.status, "duration_seconds": row.duration_seconds}
        return None

async def get_completed_runs(hours: int = 25):
    """Get completed runs from Dograh in the last N hours via DB."""
    async with DograhSessionLocal() as session:
        result = await session.execute(
            text("SELECT id, organization_id, status, CAST(usage_info->>'call_duration_seconds' AS FLOAT) as duration_seconds, created_at FROM workflow_runs WHERE status = 'completed' AND created_at >= NOW() - make_interval(hours => :hours)"),
            {"hours": hours}
        )
        rows = result.fetchall()
        return [
            {"id": r.id, "organization_id": r.organization_id, "status": r.status, "duration_seconds": r.duration_seconds, "created_at": r.created_at.isoformat()}
            for r in rows
        ]

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
