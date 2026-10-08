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
