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

        # Resolve plan from DB if not explicitly passed
        if plan is None:
            sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer_id))
            existing_sub = sub_res.scalar_one_or_none()
            if existing_sub:
                plan = existing_sub.plan
            elif customer.onboarding_form and customer.onboarding_form.get("approved_plan"):
                # Set at approval time by admin.py — always present for legitimate provisioning calls
                plan = customer.onboarding_form["approved_plan"]
            else:
                logger.error(f"No plan found for customer {customer_id} — cannot provision")
                return

        logger.info(f"Starting provisioning for customer {customer_id} with plan {plan}")

        # Step 1: Overwrite Dograh's auto-injected MPS config with Talkar keys (SOT 158)
        tts_provider = "elevenlabs" if plan == "pro" else "deepgram"
        tts_key = settings.TALKAR_ELEVENLABS_KEY if plan == "pro" else settings.TALKAR_DEEPGRAM_KEY
        model_config = {
            "llm": {
                "provider": "openai",
                "api_key": settings.TALKAR_OPENAI_KEY or "",
                "model": "gpt-4o-mini" if plan == "starter" else "gpt-4o"
            },
            "tts": {"provider": tts_provider, "api_key": tts_key or ""},
            "stt": {"provider": "deepgram", "api_key": settings.TALKAR_DEEPGRAM_KEY or ""}
        }
        await dograh_client.upsert_org_config(customer.dograh_org_id, "MODEL_CONFIGURATION_V2", model_config)

        # Step 2: Write TALKAR_ORG_TYPE = "customer" (controls sidebar filtering)
        await dograh_client.upsert_org_config(customer.dograh_org_id, "TALKAR_ORG_TYPE", "customer")

        # Step 3: Write CONCURRENT_CALL_LIMIT based on plan (SOT Table: 2 for Starter, 10 for Pro)
        limit = 2 if plan == "starter" else 10
        await dograh_client.upsert_org_config(customer.dograh_org_id, "CONCURRENT_CALL_LIMIT", str(limit))

        # Step 3.1: Write max call duration per plan (SOT 657: Starter=20min, Pro=45min)
        max_duration = 1200 if plan == "starter" else 2700
        await dograh_client.upsert_org_config(customer.dograh_org_id, "WORKFLOW_TIMEOUT_SECONDS", str(max_duration))

        # Step 4: Create wallet record in Talkar DB (balance = 0)
        # Check if wallet already exists (idempotency)
        existing_wallet = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
        if not existing_wallet.scalar_one_or_none():
            wallet = Wallet(customer_id=customer.id, balance_paise=0)
            db.add(wallet)

        # Step 5: Create Subscription record in Talkar DB
        # Per SOT pricing: Starter ₹5,000/mo @ ₹18/min | Pro ₹15,000/mo @ ₹14/min
        # Check if subscription already exists (idempotency)
        existing_sub_check = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        if not existing_sub_check.scalar_one_or_none():
            monthly_fee_paise = 500000 if plan == "starter" else 1500000  # ₹5,000 or ₹15,000
            per_minute_paise = 1800 if plan == "starter" else 1400         # ₹18 or ₹14 per SOT

            subscription = Subscription(
                customer_id=customer.id,
                plan=plan,
                status="active",
                monthly_fee_paise=monthly_fee_paise,
                per_minute_rate_paise=per_minute_paise,
                concurrent_call_limit=limit,
                setup_fee_paid=True,
                start_date=datetime.date.today(),
                next_billing_date=datetime.date.today() + datetime.timedelta(days=30)
            )
            db.add(subscription)

        await db.commit()

        # Step 6: Razorpay recurring subscription is created when customer saves card for auto-recharge
        # The actual monthly fee subscription creation requires a card token — deferred to card-save flow

        # Step 7: Customer status → agent_building is set by webhook before this runs
        logger.info(f"Provisioning successful for customer {customer_id}")
        await notification_service.notify_customer_setup_complete(customer_id)
        await notification_service.notify_admin_customer_ready_for_build(customer_id)

    except Exception as e:
        logger.error(f"Provisioning failed for customer {customer_id}: {str(e)}")
        await db.rollback()
        await notification_service.notify_admin_provisioning_failed(customer_id, [], str(e))
        raise  # Re-raise so retry endpoint gets the error
    finally:
        if own_session:
            await session_cm.__aexit__(None, None, None)

