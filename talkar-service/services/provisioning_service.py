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
        tier = plan
        master_sub = None
        if customer.billing_org_id:
            master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.billing_org_id))
            master_sub = master_sub_res.scalar_one_or_none()
            if not tier:
                # If tier was not explicitly passed, check approved_tier in onboarding_form
                tier = customer.onboarding_form.get("approved_tier") if customer.onboarding_form else None
            if not tier and master_sub:
                # Fallback to master plan
                tier = master_sub.plan
        
        if not tier:
            tier = customer.onboarding_form.get("approved_tier") if customer.onboarding_form else None
            if not tier:
                # Fallback for old rows
                tier = customer.onboarding_form.get("approved_plan", "starter") if customer.onboarding_form else "starter"

        logger.info(f"Starting provisioning for customer {customer_id} with tier {tier}")
        
        from config import resolve_tier_config, TIER_CONFIG
        sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        sub = sub_res.scalar_one_or_none()
        
        if customer.billing_org_id:
            if not master_sub:
                master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.billing_org_id))
                master_sub = master_sub_res.scalar_one_or_none()
            # If the sub-org's tier is custom and master has custom plan, inherit custom config
            if tier == "custom" and master_sub and master_sub.plan == "custom":
                tier_cfg = resolve_tier_config(master_sub)
            else:
                tier_cfg = resolve_tier_config(tier)
        elif sub:
            tier_cfg = resolve_tier_config(sub)
        else:
            tier_cfg = resolve_tier_config(tier)

        # Step 1: Overwrite Dograh's auto-injected MPS config with Talkar keys
        # Uses the OrganizationAIModelConfigurationV2 schema that Dograh expects.
        tts_provider = tier_cfg["tts_provider"]
        
        # Build TTS/STT config based on provider
        if tts_provider == "elevenlabs":
            tts_config = {
                "provider": "elevenlabs",
                "api_key": settings.TALKAR_ELEVENLABS_KEY or "",
                "model": "eleven_flash_v2_5",
                "voice": "21m00Tcm4TlvDq8ikWAM",  # Rachel voice (default)
            }
            stt_config = {
                "provider": "deepgram",
                "api_key": settings.TALKAR_DEEPGRAM_KEY or "",
                "model": "nova-3-general",
                "language": "multi",
            }
        elif tts_provider == "smallest_ai":
            tts_config = {
                "provider": "smallest",
                "api_key": settings.TALKAR_SMALLEST_AI_KEY or "",
                "model": "lightning_v3.1_pro",
                "voice": tier_cfg.get("default_voice_id", "mishka"),
            }
            stt_config = {
                "provider": "deepgram",
                "api_key": settings.TALKAR_DEEPGRAM_KEY or "",
                "model": "flux-general-multi",
                "language": "multi",
            }
        else:  # deepgram
            tts_config = {
                "provider": "deepgram",
                "api_key": settings.TALKAR_DEEPGRAM_KEY or "",
                "model": "aura-2-thalia-en",
                "voice": "aura-2-thalia-en",
            }
            stt_config = {
                "provider": "deepgram",
                "api_key": settings.TALKAR_DEEPGRAM_KEY or "",
                "model": "flux-general-multi",
                "language": "multi",
            }

        model_config = {
            "version": 2,
            "mode": "byok",
            "byok": {
                "mode": "pipeline",
                "pipeline": {
                    "llm": {
                        "provider": "openai",
                        "api_key": settings.TALKAR_OPENAI_KEY or "",
                        "model": tier_cfg["llm_model"],
                    },
                    "tts": tts_config,
                    "stt": stt_config,
                },
            },
        }
        await dograh_client.upsert_org_config(customer.dograh_org_id, "MODEL_CONFIGURATION_V2", model_config)

        # Step 2: Write TALKAR_ORG_TYPE = "customer" (controls sidebar filtering)
        await dograh_client.upsert_org_config(customer.dograh_org_id, "TALKAR_ORG_TYPE", "customer")

        # Step 3: Write CONCURRENT_CALL_LIMIT and WORKFLOW_TIMEOUT_SECONDS based on tier
        call_limit = tier_cfg["concurrent_call_limit"]
        form = customer.onboarding_form or {}
        is_custom_cap = False
        if form.get("concurrent_call_limit"):
            try:
                custom_limit = int(form["concurrent_call_limit"])
                if custom_limit > 0:
                    call_limit = min(call_limit, custom_limit)
                    is_custom_cap = True
            except (ValueError, TypeError):
                pass
        await dograh_client.upsert_org_config(customer.dograh_org_id, "CONCURRENT_CALL_LIMIT", {"value": call_limit, "is_custom_cap": is_custom_cap})
        await dograh_client.upsert_org_config(customer.dograh_org_id, "WORKFLOW_TIMEOUT_SECONDS", {"value": tier_cfg["max_call_duration_seconds"]})

        # Step 3b: Write CONCURRENCY_GROUP_ID so sub-orgs share master org's concurrency pool in Redis
        master_dograh_id = customer.dograh_org_id
        if customer.billing_org_id:
            master_cust_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
            master_cust = master_cust_res.scalar_one_or_none()
            if master_cust and master_cust.dograh_org_id:
                master_dograh_id = master_cust.dograh_org_id
        await dograh_client.upsert_org_config(customer.dograh_org_id, "CONCURRENCY_GROUP_ID", {"value": master_dograh_id})

        # Step 4: Create wallet record in Talkar DB (balance = 0)
        if customer.billing_org_id is None:
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
                start_date=datetime.date.today(),
                custom_config=master_sub.custom_config if (customer.billing_org_id and master_sub and master_sub.plan == "custom") else (sub.custom_config if sub and sub.plan == "custom" else None),
                custom_plan_label=master_sub.custom_plan_label if (customer.billing_org_id and master_sub and master_sub.plan == "custom") else (sub.custom_plan_label if sub and sub.plan == "custom" else None),
            )
            db.add(subscription)
        else:
            # Re-provisioning an existing customer (e.g. tier upgrade)
            # For sub-org under custom master when sub-org tier is custom, sync custom_config and per_minute_rate_paise from master
            if customer.billing_org_id and master_sub and master_sub.plan == "custom" and tier == "custom":
                existing_sub.plan = "custom"
                existing_sub.per_minute_rate_paise = tier_cfg["per_minute_rate_paise"]
                existing_sub.custom_config = master_sub.custom_config
                existing_sub.custom_plan_label = master_sub.custom_plan_label
            elif existing_sub.plan != "custom" or tier != "custom":
                existing_sub.plan = tier
                existing_sub.per_minute_rate_paise = tier_cfg["per_minute_rate_paise"]
            # If master customer plan is 'custom', its sub row already has correct rate/config — do not overwrite.

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

