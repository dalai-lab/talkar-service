import hashlib
import json
import logging
from datetime import datetime, timezone
from fastapi import Request, HTTPException, Security, status, Depends
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from db.session import get_db, AsyncSessionLocal
from db.models import AutomationApiKey
from services import redis_client

logger = logging.getLogger(__name__)
API_KEY_HEADER = APIKeyHeader(name='X-Talkar-API-Key', auto_error=True)

class AutomationAuthException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code=status_code, detail=detail)

def verify_automation_key(
    required_scopes: list[str] = [],
):
    async def dependency(
        request: Request,
        api_key_header: str = Security(API_KEY_HEADER),
        db: AsyncSession = Depends(get_db)
    ) -> AutomationApiKey:
        # Enforce HTTPS
        if request.url.scheme != 'https' and not request.url.hostname.startswith('localhost'):
            raise AutomationAuthException(400, 'Automation API requires HTTPS')

        if not api_key_header:
            raise AutomationAuthException(401, 'Missing API Key')

        key_hash = hashlib.sha256(api_key_header.encode('utf-8')).hexdigest()

        res = await db.execute(select(AutomationApiKey).where(AutomationApiKey.key_hash == key_hash))
        api_key = res.scalar_one_or_none()

        if not api_key:
            raise AutomationAuthException(401, 'Invalid API Key')

        if not api_key.is_active:
            raise AutomationAuthException(403, 'API Key is disabled')

        now_utc = datetime.now(timezone.utc)
        if api_key.expires_at and api_key.expires_at < now_utc:
            raise AutomationAuthException(403, 'API Key is expired')

        # Check scopes
        if required_scopes:
            key_scopes = api_key.scopes or []
            for scope in required_scopes:
                if scope not in key_scopes:
                    raise AutomationAuthException(403, f'API Key missing required scope: {scope}')

        # Rate limit
        if api_key.rate_limit_per_minute > 0 and redis_client.redis_client:
            redis = redis_client.redis_client
            redis_key = f'automation_rpm:{api_key.id}'
            try:
                pipe = redis.pipeline()
                pipe.incr(redis_key)
                pipe.expire(redis_key, 60, nx=True)
                results = await pipe.execute()
                current_count = results[0]
                if current_count > api_key.rate_limit_per_minute:
                    raise AutomationAuthException(429, 'Rate limit exceeded')
            except AutomationAuthException:
                raise
            except Exception as e:
                logger.error(f'Redis rate limiter failed for automation key {api_key.id}: {e}')

        # Update last_used_at (fire and forget using a new session)
        import asyncio
        asyncio.create_task(_update_last_used(api_key.id))

        # Attach to request state for audit logging middleware
        request.state.api_key_id = api_key.id

        return api_key

    return dependency

async def _update_last_used(key_id: int):
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(AutomationApiKey)
                .where(AutomationApiKey.id == key_id)
                .values(last_used_at=datetime.now(timezone.utc))
            )
            await db.commit()
    except Exception as e:
        logger.error(f'Failed to update last_used_at for key {key_id}: {e}')

