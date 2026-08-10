import razorpay
import hmac
import hashlib
from config import settings

# Initialize razorpay client
if settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET:
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
else:
    client = None

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

async def create_topup_order(amount_paise: int, receipt: str, customer_id: int) -> dict:
    if not client: return {"id": f"mock_topup_{receipt}", "amount": amount_paise, "currency": "INR"}
    return await asyncio.to_thread(client.order.create, {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "payment_capture": 1,
        "notes": {"customer_id": customer_id}
    })

async def create_subscription(plan_id: str, amount_paise: int, customer_razorpay_id: str) -> dict:
    if not client: return {"id": f"mock_sub_{customer_razorpay_id}"}
    return await asyncio.to_thread(client.subscription.create, {
        "plan_id": plan_id,
        "customer_notify": 1,
        "total_count": 120,  # 10 years
        "customer_id": customer_razorpay_id
    })

async def charge_saved_card(customer_id: str, payment_method_id: str, amount_paise: int) -> dict:
    if not client: return {"id": "mock_charge", "status": "captured"}
    return await asyncio.to_thread(client.payment.create, {
        "amount": amount_paise,
        "currency": "INR",
        "customer_id": customer_id,
        "token": payment_method_id,
        "recurring": "1",
        "description": "Talkar Wallet Auto-Recharge"
    })


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        return True  # mock mode
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
