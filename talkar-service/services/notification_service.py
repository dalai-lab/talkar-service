import logging
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import settings
from db.session import AsyncSessionLocal
from sqlalchemy import select

logger = logging.getLogger(__name__)


async def send_email(to_email: str, subject: str, body: str):
    """Send email via ZeptoMail SMTP (aiosmtplib would be ideal, but smtplib works via thread)."""
    if not settings.SMTP_PASSWORD:
        logger.info(f"[EMAIL MOCK] To: {to_email} | Subject: {subject} | Body: {body[:120]}")
        return

    def _send():
        formatted_body = body.replace('\n', '<br>')
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f4f5; margin: 0; padding: 0; }}
                .container {{ max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); border: 1px solid #e4e4e7; }}
                .header {{ background-color: #09090b; padding: 28px 32px; text-align: center; border-bottom: 1px solid #27272a; }}
                .header img {{ height: 32px; display: block; margin: 0 auto; }}
                .content {{ padding: 40px 32px; color: #3f3f46; font-size: 15px; line-height: 1.6; }}
                .footer {{ background-color: #fafafa; padding: 24px 32px; text-align: center; font-size: 12px; color: #71717a; border-top: 1px solid #e4e4e7; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <!-- Brand image injected for all notifications -->
                    <img src="https://talkar.in/logo-white.png" alt="Talkar" />
                </div>
                <div class="content">
                    {formatted_body}
                </div>
                <div class="footer">
                    &copy; 2026 Talkar AI. All rights reserved.
                </div>
            </div>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Talkar <{settings.FROM_EMAIL}>"
        msg["To"] = to_email
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_EMAIL, settings.SMTP_PASSWORD)
            server.sendmail(settings.FROM_EMAIL, to_email, msg.as_string())
            logger.info(f"Email sent to {to_email} | Subject: {subject}")

    try:
        await asyncio.to_thread(_send)
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")


async def _get_customer_email(customer_id: int) -> tuple[str, str] | None:
    """Helper to fetch (contact_email, contact_name) for a customer."""
    from db.models import Customer
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Customer).where(Customer.id == customer_id))
        c = result.scalar_one_or_none()
        if c:
            return c.contact_email, c.contact_name
    return None


async def notify_admin_provisioning_failed(customer_id: int, steps: list, error: str):
    await send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar] Provisioning Failed — Customer #{customer_id}",
        body=f"Provisioning failed for customer {customer_id}.\nSteps: {steps}\nError: {error}"
    )


async def notify_customer_setup_complete(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    await send_email(
        to_email=email,
        subject="Your Talkar Agent is Ready!",
        body=f"Hi {name},\n\nYour AI voice agent has been set up and is ready to use. Log in to your Talkar dashboard to explore your agent.\n\nWelcome aboard!\nThe Talkar Team"
    )

async def notify_customer_self_serve_active(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    await send_email(
        to_email=email,
        subject="Your Talkar Account is Active — Start Building",
        body=f"Hi {name},\n\nYour setup fee payment is confirmed and your account is now active.\n\nLog in to your dashboard to start building your AI voice agent. Your API keys and configuration are ready.\n\nThe Talkar Team"
    )


async def notify_admin_customer_ready_for_build(customer_id: int):
    info = await _get_customer_email(customer_id)
    display = f"Customer #{customer_id}" if not info else f"{info[1]} (#{customer_id})"
    await send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar] Setup Fee Paid — {display} Ready for Build",
        body=f"{display} has completed their setup fee payment and is ready for agent building."
    )


async def notify_customer_auto_recharge_failed(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    await send_email(
        to_email=email,
        subject="Talkar — Auto-Recharge Failed",
        body=f"Hi {name},\n\nWe were unable to auto-recharge your wallet. Please update your payment method on the Talkar dashboard to avoid service interruption.\n\nThe Talkar Team"
    )


async def notify_admin_auto_recharge_failed(customer_id: int):
    await send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar] Auto-Recharge Failed — Customer #{customer_id}",
        body=f"Auto-recharge failed for customer #{customer_id}. Manual follow-up may be required."
    )


async def notify_admin_subscription_halted(subscription_id: str):
    await send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar] Subscription Halted — {subscription_id}",
        body=f"Subscription {subscription_id} has been halted. Please review in the Razorpay dashboard."
    )


async def notify_customer_low_balance(customer_id: int, balance_paise: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    balance_rs = balance_paise / 100
    await send_email(
        to_email=email,
        subject="Talkar — Low Wallet Balance Warning",
        body=f"Hi {name},\n\nYour Talkar wallet balance is low (₹{balance_rs:.2f}). Please top up to continue using your AI agent without interruption.\n\nThe Talkar Team"
    )


async def notify_customer_service_paused(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        logger.warning(f"Emailing customer {customer_id}: balance below threshold, calls paused.")
        return
    email, name = info
    await send_email(
        to_email=email,
        subject="Talkar — Service Paused: Low Balance",
        body=f"Hi {name},\n\nYour Talkar wallet balance has dropped below the minimum operating threshold (₹500). Your calls have been paused.\n\nPlease top up your wallet immediately to resume service.\n\nThe Talkar Team"
    )


async def notify_customer_rejected(customer_id: int, reason: str):
    info = await _get_customer_email(customer_id)
    if not info:
        logger.info(f"Emailing customer {customer_id}: application rejected.")
        return
    email, name = info
    await send_email(
        to_email=email,
        subject="Update on Your Talkar Application",
        body=f"Hi {name},\n\nThank you for your interest in Talkar. After reviewing your application, we are unable to proceed at this time.\n\nReason: {reason}\n\nIf you have any questions, please contact us.\n\nThe Talkar Team"
    )
