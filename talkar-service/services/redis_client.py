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
        # Use a transaction to incr and set TTL
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds)
        await pipe.execute()
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
