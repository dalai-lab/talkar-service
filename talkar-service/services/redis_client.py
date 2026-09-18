import redis.asyncio as redis
from config import settings
import logging

logger = logging.getLogger(__name__)

redis_client = None

async def init_redis():
    global redis_client
    if redis_client is None:
        try:
            redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
            # Test connection
            await redis_client.ping()
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            redis_client = None

async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None

async def get_active_calls(master_id: int) -> int:
    global redis_client
    if not redis_client:
        return 0
    try:
        val = await redis_client.get(f"billing_group_active:{master_id}")
        return max(0, int(val)) if val else 0
    except Exception as e:
        logger.error(f"Redis GET failed for billing_group_active:{master_id}: {e}")
        return 0

async def increment_active_calls(master_id: int, ttl_seconds: int):
    global redis_client
    if not redis_client:
        return
    try:
        key = f"billing_group_active:{master_id}"
        # INCR first, then only set TTL if this is a fresh key (value == 1).
        # This avoids resetting the TTL for pre-existing concurrent calls, which
        # would cause the counter to expire before all calls decrement it.
        pipe = redis_client.pipeline()
        pipe.incr(key)
        results = await pipe.execute()
        new_val = results[0]
        if new_val == 1:
            # First call in this group - set a generous TTL as a safety net.
            # Add 60s buffer so the last decrement is not racing the expiry.
            await redis_client.expire(key, ttl_seconds + 60)
    except Exception as e:
        logger.error(f"Redis INCR failed for billing_group_active:{master_id}: {e}")

async def decrement_active_calls(master_id: int):
    global redis_client
    if not redis_client:
        return
    try:
        key = f"billing_group_active:{master_id}"
        pipe = redis_client.pipeline()
        pipe.decr(key)
        await pipe.execute()
    except Exception as e:
        logger.error(f"Redis DECR failed for billing_group_active:{master_id}: {e}")

async def acquire_auto_recharge_lock(master_id: int, ttl_seconds: int = 120) -> bool:
    global redis_client
    if not redis_client:
        return True
    try:
        res = await redis_client.set(f"auto_recharge_lock:{master_id}", "1", nx=True, ex=ttl_seconds)
        return bool(res)
    except Exception as e:
        logger.error(f"Redis auto-recharge lock failed: {e}")
        return True

async def release_auto_recharge_lock(master_id: int):
    global redis_client
    if not redis_client:
        return
    try:
        await redis_client.delete(f"auto_recharge_lock:{master_id}")
    except Exception as e:
        logger.error(f"Redis auto-recharge unlock failed: {e}")
