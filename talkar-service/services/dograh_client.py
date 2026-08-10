from config import settings
import httpx
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def _get_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.DOGRAH_ADMIN_TOKEN}",
        "Content-Type": "application/json"
    }

async def upsert_org_config(org_id: int, key: str, value: Any):
    """Upserts an organization config in Dograh"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.DOGRAH_API_URL}/api/orgs/{org_id}/configs",
            headers=_get_headers(),
            json={"key": key, "value": value}
        )
        response.raise_for_status()

async def get_run(run_id: int):
    """Get a specific run from Dograh by ID."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.DOGRAH_API_URL}/api/runs/{run_id}",
            headers=_get_headers()
        )
        if response.status_code == 200:
            return response.json()
        logger.error(f"Failed to fetch run {run_id}: {response.text}")
        return None

async def get_completed_runs(hours: int = 25):
    """Get completed runs from Dograh in the last N hours."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.DOGRAH_API_URL}/api/runs/completed?hours={hours}",
            headers=_get_headers()
        )
        if response.status_code == 200:
            return response.json()
        logger.error(f"Failed to fetch completed runs: {response.text}")
        return []

async def delete_org(org_id: int):
    """Delete an organization in Dograh."""
    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{settings.DOGRAH_API_URL}/api/orgs/{org_id}",
            headers=_get_headers()
        )
        if response.status_code not in (200, 204):
            logger.error(f"Failed to delete org {org_id}: {response.text}")

async def archive_org(org_id: int):
    """Archive an organization in Dograh."""
    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{settings.DOGRAH_API_URL}/api/orgs/{org_id}/archive",
            headers=_get_headers()
        )
        if response.status_code not in (200, 204):
            logger.error(f"Failed to archive org {org_id}: {response.text}")
