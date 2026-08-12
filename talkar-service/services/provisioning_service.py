import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Customer, Subscription, Wallet
from services import dograh_client, razorpay_client, notification_service
from config import settings

logger = logging.getLogger(__name__)

import datetime

async def run_provisioning(customer_id: int, plan: str = None, db: AsyncSession = None) -> None:
    """Orchestrates the entire Talkar -> Dograh engine setup phase synchronously.
    
    Can be called with an existing db session (e.g. from webhook handler)
    or without one (e.g. from admin retry endpoint) — in which case it creates its own.
    """
    from db.session import AsyncSessionLocal
    
    own_session = db is None
    if own_session:
        session_cm = AsyncSessionLocal()
        db = await session_cm.__aenter__()
    
    try:
        result = await db.execute(select(Customer).where(Customer.id == customer_id))
        customer = result.scalar_one_or_none()
        if not customer:
            logger.error(f"Provisioning failed: Customer {customer_id} not found")
            return

        # Resolve tier from DB
        tier = customer.onboarding_form.get("approved_tier") if customer.onboarding_form else None
        if not tier:
            # Fallback for old rows
            tier = customer.onboarding_form.get("approved_plan", "starter") if customer.onboarding_form else "starter"

        logger.info(f"Starting provisioning for customer {customer_id} with tier {tier}")
        
        from config import TIER_CONFIG
        tier_cfg = TIER_CONFIG.get(tier, TIER_CONFIG["starter"])

        # Step 1: Overwrite Dograh's auto-injected MPS config with Talkar keys
        tts_provider = tier_cfg["tts_provider"]
        tts_key = settings.TALKAR_ELEVENLABS_KEY if tts_provider == "elevenlabs" else settings.TALKAR_DEEPGRAM_KEY
        model_config = {
            "llm": {
                "provider": "openai",
                "api_key": settings.TALKAR_OPENAI_KEY or "",
                "model": tier_cfg["llm_model"]
            },
            "tts": {"provider": tts_provider, "api_key": tts_key or ""},
            "stt": {"provider": "deepgram", "api_key": settings.TALKAR_DEEPGRAM_KEY or ""}
        }
        await dograh_client.upsert_org_config(customer.dograh_org_id, "MODEL_CONFIGURATION_V2", model_config)

        # Step 2: Write TALKAR_ORG_TYPE = "customer" (controls sidebar filtering)
        await dograh_client.upsert_org_config(customer.dograh_org_id, "TALKAR_ORG_TYPE", "customer")

        # Step 3: Write CONCURRENT_CALL_LIMIT and WORKFLOW_TIMEOUT_SECONDS based on tier
        await dograh_client.upsert_org_config(customer.dograh_org_id, "CONCURRENT_CALL_LIMIT", str(tier_cfg["concurrent_call_limit"]))
        await dograh_client.upsert_org_config(customer.dograh_org_id, "WORKFLOW_TIMEOUT_SECONDS", str(tier_cfg["max_call_duration_seconds"]))

        # Step 4: Create wallet record in Talkar DB (balance = 0)
        existing_wallet = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
        if not existing_wallet.scalar_one_or_none():
            wallet = Wallet(customer_id=customer.id, balance_paise=0)
            db.add(wallet)

        # Step 5: Create Subscription record in Talkar DB (or update if exists)
        existing_sub_check = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        existing_sub = existing_sub_check.scalar_one_or_none()
        if not existing_sub:
            subscription = Subscription(
                customer_id=customer.id,
                plan=tier,
                status="active",
                per_minute_rate_paise=tier_cfg["per_minute_rate_paise"],
                setup_fee_paid=True,
                start_date=datetime.date.today()
            )
            db.add(subscription)
        else:
            # Re-provisioning an existing customer (e.g. tier upgrade)
            existing_sub.plan = tier
            existing_sub.per_minute_rate_paise = tier_cfg["per_minute_rate_paise"]

        await db.commit()

        logger.info(f"Provisioning successful for customer {customer_id}")
        await notification_service.notify_admin_customer_ready_for_build(customer_id)

    except Exception as e:
        logger.error(f"Provisioning failed for customer {customer_id}: {str(e)}")
        await db.rollback()
        await notification_service.notify_admin_provisioning_failed(customer_id, [], str(e))
        raise  # Re-raise so retry endpoint gets the error
    finally:
        if own_session:
            await session_cm.__aexit__(None, None, None)

