import logging

logger = logging.getLogger(__name__)

async def notify_admin_provisioning_failed(customer_id: int, steps: list, error: str):
    pass

async def notify_customer_setup_complete(customer_id: int):
    pass

async def notify_admin_customer_ready_for_build(customer_id: int):
    pass

async def notify_customer_auto_recharge_failed(customer_id: int):
    pass

async def notify_admin_auto_recharge_failed(customer_id: int):
    pass

async def notify_admin_subscription_halted(subscription_id: str):
    pass

async def notify_customer_low_balance(customer_id: int, balance_paise: int):
    pass

async def notify_customer_negative_balance(customer_id: int):
    logger.warning(f"Emailing customer {customer_id}: balance is negative, calls paused until you top up.")

async def notify_customer_rejected(customer_id: int, reason: str):
    logger.info(f"Emailing customer {customer_id}: application rejected. Reason: {reason}")

async def send_email(to_email: str, subject: str, body: str):
    """Generic email sender stub. Replace with SES/SendGrid/SMTP in production."""
    logger.info(f"[EMAIL] To: {to_email} | Subject: {subject} | Body: {body[:100]}...")
