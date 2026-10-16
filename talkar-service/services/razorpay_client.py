import razorpay
import hmac
import hashlib
import logging
from config import settings

logger = logging.getLogger(__name__)

# Initialize razorpay client
if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
else:
    client = None

class _OrderWrapper:
    def create(self, data: dict = None, **kwargs):
        payload = data if data is not None else kwargs
        if client and hasattr(client, "order"):
            return client.order.create(data=payload)
        receipt = payload.get("receipt", "upg") if payload else "upg"
        return {
            "id": f"mock_order_{receipt}",
            "amount": payload.get("amount", 0) if payload else 0,
            "currency": payload.get("currency", "INR") if payload else "INR",
            "status": "created"
        }

order = _OrderWrapper()

import asyncio

async def create_setup_fee_order(amount_paise: int, receipt: str, customer_id: int, plan: str) -> dict:
    if not client: return {"id": f"mock_order_{receipt}", "amount": amount_paise, "currency": "INR"}
    return await asyncio.to_thread(client.order.create, {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "payment_capture": 1,
        "notes": {"customer_id": customer_id, "plan": plan}
    })

async def create_topup_order(amount_paise: int, receipt: str, customer_id: int, extra_notes: dict = None) -> dict:
    if not client: return {"id": f"mock_topup_{receipt}", "amount": amount_paise, "currency": "INR"}
    notes = {"customer_id": customer_id}
    if extra_notes:
        notes.update(extra_notes)
    return await asyncio.to_thread(client.order.create, {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "payment_capture": 1,
        "notes": notes
    })

async def create_subscription(plan_id: str, amount_paise: int, customer_razorpay_id: str) -> dict:
    if not client: return {"id": f"mock_sub_{customer_razorpay_id}"}
    return await asyncio.to_thread(client.subscription.create, {
        "plan_id": plan_id,
        "customer_notify": 1,
        "total_count": 120,  # 10 years
        "customer_id": customer_razorpay_id
    })

async def charge_saved_card(
    customer_id: str,
    payment_method_id: str,
    amount_paise: int,
    email: str = None,
    contact: str = None,
    order_id: str = None,
    talkar_customer_id: int | str = None,
    notes: dict = None,
) -> dict:
    if payment_method_id and str(payment_method_id).startswith("pay_"):
        raise ValueError(
            f"Invalid recurring token '{payment_method_id}'. Payment IDs (pay_xxx) cannot be charged as tokens under RBI mandate guidelines; expected token_xxx."
        )

    if not client: return {"id": "mock_charge", "status": "captured"}

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "customer_id": customer_id,
        "token": payment_method_id,
        "recurring": "1",
        "description": "Talkar Wallet Auto-Recharge"
    }
    if email:
        payload["email"] = email
    if contact:
        payload["contact"] = contact
    if order_id:
        payload["order_id"] = order_id

    req_notes = dict(notes) if notes else {}
    if talkar_customer_id:
        req_notes["customer_id"] = str(talkar_customer_id)
    if req_notes:
        payload["notes"] = req_notes

    # In Razorpay Python SDK, recurring payments use client.payment.createRecurring (POST /v1/payments/create/recurring).
    # client.payment.create does not exist in the SDK.
    if hasattr(client.payment, "createRecurring"):
        return await asyncio.to_thread(client.payment.createRecurring, payload)
    elif hasattr(client.payment, "post_url"):
        return await asyncio.to_thread(client.payment.post_url, f"{client.payment.base_url}/create/recurring", payload)
    else:
        raise AttributeError("Razorpay client has no recurring payment method")


async def fetch_order(order_id: str) -> dict | None:
    if not client:
        return None
    try:
        return await asyncio.to_thread(client.order.fetch, order_id)
    except Exception as e:
        logger.error(f"Failed to fetch Razorpay order {order_id}: {e}")
        return None

async def fetch_payment(payment_id: str) -> dict | None:
    if not client:
        return None
    try:
        return await asyncio.to_thread(client.payment.fetch, payment_id)
    except Exception as e:
        logger.error(f"Failed to fetch Razorpay payment {payment_id}: {e}")
        return None


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        env = getattr(settings, "ENVIRONMENT", "development").lower()
        if env in ("production", "prod", "staging"):
            logger.critical("RAZORPAY_WEBHOOK_SECRET is not configured in production! Rejecting webhook.")
            return False
        return True  # mock mode for local dev/testing
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
