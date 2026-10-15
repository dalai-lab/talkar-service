import logging
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import settings
from db.session import AsyncSessionLocal
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Statuses where the agent is still being built / account not yet live.
# Customer-facing notifications (tier change, credit grants, etc.) should
# be suppressed during these stages — the customer does not have a live
# agent yet, so emailing them about billing events is premature and confusing.
_BUILDING_STATUSES = frozenset({
    "under_review",
    "pending_approval",
    "info_requested",
    "approved",
    "agent_building",
})

async def _is_building_stage(customer_id: int) -> bool:
    """Return True if the customer's account is in a pre-live building stage."""
    from db.models import Customer as _Customer
    async with AsyncSessionLocal() as _db:
        _res = await _db.execute(select(_Customer).where(_Customer.id == customer_id))
        _cust = _res.scalar_one_or_none()
        return _cust is not None and _cust.status in _BUILDING_STATUSES



def format_body_to_html(body: str, subject: str) -> str:
    lines = body.strip().split('\n')
    html_paragraphs = []
    in_list = False
    in_pre = False
    
    for line in lines:
        line_str = line.strip()
        if not line_str:
            if in_list:
                html_paragraphs.append("</ul>")
                in_list = False
            if in_pre:
                html_paragraphs.append("</pre></div>")
                in_pre = False
            continue
            
        # Check if line is a greeting
        if line_str.startswith("Hi ") or line_str.startswith("Hello ") or line_str.startswith("Dear "):
            html_paragraphs.append(f'<p style="font-size: 16px; font-weight: 700; color: #09090b; margin-top: 0; margin-bottom: 16px;">{line_str}</p>')
            continue
            
        # Check if line is the signature
        if line_str in ["The Talkar Team", "Welcome aboard!", "Thank you for using Talkar!", "Sincerely,", "Thank you for partnering with us."]:
            html_paragraphs.append(f'<p style="font-size: 14px; color: #71717a; margin-top: 20px; margin-bottom: 4px; font-weight: 600;">{line_str}</p>')
            continue

        # Check if line looks like a bullet list item
        if line_str.startswith("- ") or line_str.startswith("* ") or line_str.startswith("• "):
            if not in_list:
                html_paragraphs.append('<ul style="margin: 8px 0; padding-left: 20px; color: #3f3f46; font-size: 14px; line-height: 1.6;">')
                in_list = True
            content = line_str[2:]
            html_paragraphs.append(f'<li style="margin-bottom: 8px;">{content}</li>')
            continue
            
        # Check if line looks like a key-value or labeled detail
        if ":" in line_str and not line_str.startswith("http") and len(line_str.split(":")[0]) < 30:
            key, val = line_str.split(":", 1)
            val = val.strip()
            if not val:
                html_paragraphs.append(f'<h4 style="font-size: 14px; font-weight: 700; color: #09090b; margin-top: 20px; margin-bottom: 8px; border-bottom: 1px solid #e4e4e7; padding-bottom: 4px;">{key}</h4>')
            else:
                html_paragraphs.append(f'<p style="margin: 6px 0; font-size: 14px;"><strong style="color: #09090b;">{key}:</strong> <span style="color: #3f3f46;">{val}</span></p>')
            continue
            
        # Check for JSON or details dump
        if line_str.startswith("{") or line_str.startswith("[") or in_pre:
            if not in_pre:
                html_paragraphs.append('<div style="background-color: #fafafa; border: 1px solid #e4e4e7; border-radius: 8px; padding: 16px; font-family: monospace; font-size: 13px; color: #18181b; overflow-x: auto; margin: 16px 0;"><pre style="margin: 0;">')
                in_pre = True
            html_paragraphs.append(line_str)
            continue
            
        # Otherwise, regular paragraph
        html_paragraphs.append(f'<p style="margin: 12px 0; color: #3f3f46; font-size: 14px; line-height: 1.6;">{line_str}</p>')
        
    if in_list:
        html_paragraphs.append("</ul>")
    if in_pre:
        html_paragraphs.append("</pre></div>")
        
    html_content = "\n".join(html_paragraphs)
    
    # Render links or buttons dynamically
    cta_button = ""
    if "talkar.in/admin" in body:
        cta_button = """
        <div style="margin-top: 28px; text-align: center;">
            <a href="https://talkar.in/admin" style="background-color: #fe6905; color: #ffffff; padding: 12px 24px; font-size: 14px; font-weight: 700; text-decoration: none; border-radius: 8px; display: inline-block; box-shadow: 0 4px 10px rgba(254, 105, 5, 0.25);">
                Open Admin Portal
            </a>
        </div>
        """
    elif "talkar.in" in body or "dashboard" in body.lower():
        cta_button = """
        <div style="margin-top: 28px; text-align: center;">
            <a href="https://talkar.in/overview" style="background-color: #fe6905; color: #ffffff; padding: 12px 24px; font-size: 14px; font-weight: 700; text-decoration: none; border-radius: 8px; display: inline-block; box-shadow: 0 4px 10px rgba(254, 105, 5, 0.25);">
                Launch Dashboard
            </a>
        </div>
        """
        
    if cta_button:
        html_content += cta_button
        
    return html_content


def build_email_mime_message(
    to_email: str, subject: str, body: str, cc: str | None = None
) -> tuple[MIMEMultipart, list[str]]:
    """Build a styled multipart HTML + plain-text email message."""
    html_content = format_body_to_html(body, subject)
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{subject}</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #fafafa; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }}
            .container {{ max-width: 580px; margin: 40px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(9, 9, 11, 0.03), 0 1px 4px rgba(9, 9, 11, 0.02); border: 1px solid #e4e4e7; }}
            .header {{ background-color: #09090b; padding: 24px 32px; text-align: center; border-bottom: 2px solid #fe6905; }}
            .content {{ padding: 36px 40px; color: #27272a; font-size: 14px; line-height: 1.6; }}
            .footer {{ background-color: #fafafa; padding: 20px 32px; text-align: center; font-size: 11px; color: #a1a1aa; border-top: 1px solid #f4f4f5; }}
            a {{ color: #fe6905; text-decoration: none; font-weight: 500; }}
            a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <table border="0" cellpadding="0" cellspacing="0" width="100%">
                    <tr>
                        <td align="center">
                            <img src="https://talkar.in/logo-white.png" alt="Talkar" width="150" style="display: block; max-width: 150px; height: auto;" />
                        </td>
                    </tr>
                </table>
            </div>
            <div class="content">
                {html_content}
            </div>
            <div class="footer">
                &copy; 2026 Talkar AI. All rights reserved.<br>
                This is an automated communication regarding your workspace services.
            </div>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Talkar <{settings.FROM_EMAIL}>"
    msg["To"] = to_email
    if cc:
        msg["Cc"] = cc
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    recipients = [to_email, cc] if cc else [to_email]
    return msg, recipients


def _send_bulk_sync(email_items: list[dict], batch_size: int = 50) -> dict:
    """Synchronous worker that reuses a single TLS-authenticated SMTP session across batches."""
    if not email_items:
        return {"total": 0, "sent": 0, "failed": 0}

    if not settings.SMTP_PASSWORD:
        for item in email_items:
            logger.info(
                f"[EMAIL MOCK BULK] To: {item.get('to_email')} | "
                f"Subject: {item.get('subject')} | Body: {str(item.get('body', ''))[:120]}"
            )
        return {"total": len(email_items), "sent": len(email_items), "failed": 0}

    sent_count = 0
    failed_count = 0

    for i in range(0, len(email_items), batch_size):
        chunk = email_items[i : i + batch_size]
        server = None
        try:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_EMAIL, settings.SMTP_PASSWORD)

            for item in chunk:
                to_email = item.get("to_email")
                if not to_email:
                    continue
                subject = item.get("subject", "Talkar Notification")
                body = item.get("body", "")
                cc = item.get("cc")

                try:
                    msg, recipients = build_email_mime_message(to_email, subject, body, cc)
                    server.sendmail(settings.FROM_EMAIL, recipients, msg.as_string())
                    sent_count += 1
                    logger.info(f"Email sent to {to_email}{f' (CC: {cc})' if cc else ''} | Subject: {subject}")
                except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, ConnectionResetError, BrokenPipeError) as conn_err:
                    logger.warning(f"SMTP connection lost sending to {to_email}, reconnecting: {conn_err}")
                    try:
                        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
                        server.ehlo()
                        server.starttls()
                        server.login(settings.SMTP_EMAIL, settings.SMTP_PASSWORD)
                        server.sendmail(settings.FROM_EMAIL, recipients, msg.as_string())
                        sent_count += 1
                        logger.info(f"Email sent to {to_email} after reconnection | Subject: {subject}")
                    except Exception as retry_err:
                        failed_count += 1
                        logger.error(f"Failed sending email to {to_email} after retry: {retry_err}")
                except Exception as item_err:
                    failed_count += 1
                    logger.error(f"Failed sending email to {to_email}: {item_err}")
        except Exception as batch_conn_err:
            logger.error(f"Failed connecting to SMTP server for batch: {batch_conn_err}")
            failed_count += len(chunk)
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    try:
                        server.close()
                    except Exception:
                        pass

    return {"total": len(email_items), "sent": sent_count, "failed": failed_count}


async def send_bulk_emails(email_items: list[dict], batch_size: int = 50) -> dict:
    """
    Sends multiple emails reusing persistent SMTP TLS sessions.
    Runs non-blocking in a thread pool via asyncio.to_thread.
    """
    return await asyncio.to_thread(_send_bulk_sync, email_items, batch_size)


async def send_email(to_email: str, subject: str, body: str, cc: str | None = None) -> bool:
    """Send email via ZeptoMail SMTP reusing persistent connection logic.

    Args:
        to_email: Primary recipient.
        subject:  Email subject line.
        body:     Plain-text body (also rendered as HTML).
        cc:       Optional CC address. Defaults to None.
    """
    result = await send_bulk_emails([{"to_email": to_email, "subject": subject, "body": body, "cc": cc}])
    return result.get("sent", 0) > 0

async def push_notification(
    customer_id: int,
    title: str,
    body: str,
    notification_type: str = "info",
    cascade_to_suborgs: bool = False,
):
    """Push an in-app notification to the database, optionally cascading to sub-orgs in the fleet."""
    from db.models import Customer, Notification, NotificationCategory
    from db.session import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            notif = Notification(
                customer_id=customer_id,
                title=title,
                body=body,
                type=notification_type
            )
            db.add(notif)

            if cascade_to_suborgs:
                cust_res = await db.execute(select(Customer).where(Customer.id == customer_id))
                cust = cust_res.scalar_one_or_none()
                if cust:
                    master_id = cust.billing_org_id or cust.id
                    sub_res = await db.execute(
                        select(Customer.id).where(Customer.billing_org_id == master_id)
                    )
                    sub_ids = sub_res.scalars().all()

                    target_ids = set(sub_ids)
                    target_ids.add(master_id)
                    target_ids.discard(customer_id)

                    for tid in target_ids:
                        db.add(
                            Notification(
                                customer_id=tid,
                                title=title,
                                body=body,
                                type=notification_type,
                                is_read=False
                            )
                        )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to push notification for customer {customer_id}: {e}")

async def send_email_and_push(
    customer_id: int,
    to_email: str,
    subject: str,
    body: str,
    notification_type: str = "info",
    push_body: str | None = None,
    cc: str | None = None,
    cascade_to_suborgs: bool = False,
):
    """Helper to send an email AND push an in-app notification simultaneously."""
    await send_email(to_email, subject, body, cc=cc)
    
    # Generate a plain text push body if none provided (strip formatting, keep it short)
    pb = push_body
    if not pb:
        # Just grab the first non-greeting paragraph
        lines = [l.strip() for l in body.split('\n') if l.strip() and not l.startswith("Hi ") and not l.startswith("The Talkar Team")]
        pb = lines[0] if lines else subject
        
    await push_notification(
        customer_id=customer_id,
        title=subject,
        body=pb,
        notification_type=notification_type,
        cascade_to_suborgs=cascade_to_suborgs,
    )



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
        subject=f"[Talkar Admin] System Alert: Agent Setup Failed",
        body=(
            f"Workspace provisioning encountered an error.\n\n"
            f"Customer ID: #{customer_id}\n"
            f"Failed Steps: {steps}\n"
            f"Error Details: {error}\n\n"
            f"Please inspect the build queues on the admin dashboard: talkar.in/admin"
        )
    )


async def notify_customer_setup_complete(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Your Talkar Agent is Live & Ready",
        body=(
            f"Hi {name},\n\n"
            f"Your AI voice assistant is fully configured and ready to handle live calls. You can now access your dashboard to monitor performance, manage call credits, and customize your agent's response behavior.\n\n"
            f"Thank you for choosing Talkar.\n\n"
            f"The Talkar Team"
        ),
        notification_type="info"
    )

async def notify_customer_self_serve_active(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Welcome to Talkar — Account Activated",
        body=(
            f"Hi {name},\n\n"
            f"We have successfully processed your setup fee. Your account is active and your workspace is fully unlocked.\n\n"
            f"Log in to your dashboard to begin configuring your voice agent and managing call flows.\n\n"
            f"The Talkar Team"
        ),
        notification_type="info",
        push_body="Your account is active and your workspace is unlocked."
    )


async def notify_admin_customer_ready_for_build(customer_id: int):
    info = await _get_customer_email(customer_id)
    display = f"Customer #{customer_id}" if not info else f"{info[1]} (#{customer_id})"
    await send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar Admin] Action Required: New Build Request",
        body=(
            f"A customer is ready for workspace building.\n\n"
            f"Account: {display}\n"
            f"Status: Setup fee successfully processed.\n\n"
            f"Please assign a phone number and build details: talkar.in/admin"
        )
    )


async def notify_customer_auto_recharge_failed(customer_id: int, error_detail: str = None, disabled: bool = False):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    
    if disabled:
        subject = "Action Required: Auto-Recharge Disabled"
        body = (
            f"Hi {name},\n\n"
            f"Your automatic wallet recharge has been disabled after consecutive payment authorization failures.\n\n"
            f"Under RBI regulations, automatic card debits require an active e-mandate registered with your issuing bank. "
            f"If your bank requires pre-debit authorization or did not approve the recurring mandate, you can register a compliant card on your dashboard or make manual wallet top-ups.\n\n"
            f"Please visit your dashboard to re-verify your billing method or add wallet credits to keep your voice agents online: talkar.in/wallet\n\n"
            f"The Talkar Team"
        )
        push_body = "Auto-recharge was paused after payment failures. Please update billing to prevent service interruption."
    else:
        subject = "Action Required: Auto-Recharge Payment Issue"
        body = (
            f"Hi {name},\n\n"
            f"We were unable to process your automatic wallet recharge. Under RBI regulations, automated card debits require an active bank e-mandate.\n\n"
            f"To ensure your voice agent remains online and active, please verify your billing method on your dashboard or top up manually.\n\n"
            f"The Talkar Team"
        )
        push_body = "Auto-recharge payment failed. Please check your billing method."

    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject=subject,
        body=body,
        notification_type="warning",
        push_body=push_body,
        cascade_to_suborgs=True,
    )


async def notify_admin_auto_recharge_failed(customer_id: int, error_detail: str = None, disabled: bool = False):
    status_str = "Auto-Recharge Disabled (3 failures)" if disabled else "Auto-Recharge Failed"
    detail_str = f"\nError: {error_detail}" if error_detail else ""
    await send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar Admin] Billing Warning: {status_str} for Customer #{customer_id}",
        body=(
            f"Auto-recharge transaction failed for customer ID #{customer_id}.{detail_str}\n"
            f"Disabled: {disabled}\n\n"
            f"Please check customer payment details and follow up if needed: talkar.in/admin"
        )
    )


async def notify_admin_subscription_halted(subscription_id: str):
    await send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar Admin] Razorpay Alert: Subscription Halted",
        body=(
            f"Subscription {subscription_id} has been halted in Razorpay.\n\n"
            f"Please review customer account status: talkar.in/admin"
        )
    )


async def notify_customer_low_balance(customer_id: int, balance_paise: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info

    # Check customer's notification preferences from report_settings
    from db.models import Customer as _Customer
    cc_email: str | None = None
    async with AsyncSessionLocal() as _db:
        _res = await _db.execute(select(_Customer).where(_Customer.id == customer_id))
        _cust = _res.scalar_one_or_none()
        if _cust:
            _rep = _cust.report_settings or {}
            # Respect email mute preference — skip email but still push in-app alert
            if not _rep.get("email_notifications_enabled", True):
                balance_rs = balance_paise / 100
                await push_notification(
                    customer_id=customer_id,
                    title="Low Balance Warning: Talkar Wallet",
                    body=f"Low balance: ₹{balance_rs:.2f}. Please add credits.",
                    notification_type="warning",
                    cascade_to_suborgs=True,
                )
                return
            cc_email = (_rep.get("cc_email") or "").strip() or None

    balance_rs = balance_paise / 100
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Low Balance Warning: Talkar Wallet",
        body=(
            f"Hi {name},\n\n"
            f"Your Talkar wallet balance is running low at ₹{balance_rs:.2f}.\n\n"
            f"Please add credits to your wallet on the dashboard to ensure calling services remain active without interruption.\n\n"
            f"The Talkar Team"
        ),
        notification_type="warning",
        push_body=f"Low balance: ₹{balance_rs:.2f}. Please add credits.",
        cc=cc_email,
        cascade_to_suborgs=True,
    )


async def notify_customer_service_paused(customer_id: int):
    try:
        from services import redis_client
        r = await redis_client.get_redis()
        if r:
            cooldown_key = f"alert_paused_cooldown:{customer_id}"
            already_sent = await r.set(cooldown_key, "1", nx=True, ex=86400)
            if not already_sent:
                logger.info(f"Service paused notification for customer {customer_id} in 24h cooldown, skipping duplicate email.")
                return
    except Exception as e:
        logger.error(f"Error checking service paused cooldown in Redis: {e}")

    info = await _get_customer_email(customer_id)
    if not info:
        logger.warning(f"Emailing customer {customer_id}: balance below threshold, calls paused.")
        return
    email, name = info
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Urgent: Talkar Calling Service Paused",
        body=(
            f"Hi {name},\n\n"
            f"Your Talkar wallet balance has dropped below the minimum operating threshold (₹500). To prevent unpaid usage, call routing services have been temporarily paused.\n\n"
            f"Please top up your wallet on the dashboard to instantly reactivate your lines.\n\n"
            f"The Talkar Team"
        ),
        notification_type="warning",
        push_body="Service paused due to low balance. Please top up.",
        cascade_to_suborgs=True,
    )

async def notify_customer_topup_successful(customer_id: int, amount_paise: int, new_balance_paise: int):
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    amount_rs = amount_paise / 100
    balance_rs = new_balance_paise / 100

    # Pick up CC email from notification preferences for billing confirmations
    from db.models import Customer as _Customer, Invoice as _Invoice
    cc_email: str | None = None
    invoice_link = ""
    async with AsyncSessionLocal() as _db:
        _res = await _db.execute(select(_Customer).where(_Customer.id == customer_id))
        _cust = _res.scalar_one_or_none()
        if _cust:
            _rep = _cust.report_settings or {}
            cc_email = (_rep.get("cc_email") or "").strip() or None
            
        _inv_res = await _db.execute(select(_Invoice).where(_Invoice.customer_id == customer_id).order_by(_Invoice.created_at.desc()).limit(1))
        _inv = _inv_res.scalar_one_or_none()
        if _inv:
            invoice_link = f"\n\nYou can view and download your invoice receipt here:\nhttps://talkar.in/invoice/{_inv.id}"

    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Payment Confirmed: Talkar Wallet Credits",
        body=(
            f"Hi {name},\n\n"
            f"Your payment of ₹{amount_rs:,.2f} was processed successfully.\n\n"
            f"Your updated wallet balance is ₹{balance_rs:,.2f}.{invoice_link}\n\n"
            f"Thank you for partnering with us.\n\n"
            f"The Talkar Team"
        ),
        notification_type="billing",
        push_body=f"Payment of ₹{amount_rs:,.2f} processed. Balance: ₹{balance_rs:,.2f}",
        cc=cc_email,
    )

async def notify_customer_tier_upgraded(customer_id: int, new_tier: str):
    # Don't notify customers while their agent is still being built — confusing
    if await _is_building_stage(customer_id):
        logger.info(f"[notify_customer_tier_upgraded] Suppressed for customer {customer_id} (building stage)")
        return
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject=f"Tier Upgrade Confirmed: {new_tier.capitalize()} Engine",
        body=(
            f"Hi {name},\n\n"
            f"Your account has been successfully upgraded to the {new_tier.capitalize()} tier. Your new calling capacity and discounted per-minute rates are now active.\n\n"
            f"Enjoy the upgraded performance!\n\n"
            f"The Talkar Team"
        ),
        notification_type="info",
        push_body=f"Account upgraded to {new_tier.capitalize()} tier."
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
        body=(
            f"Hi {name},\n\n"
            f"Thank you for applying to Talkar.\n\n"
            f"After carefully reviewing your business model and telephony requirements, we are unable to proceed with your workspace activation at this time.\n\n"
            f"Reason for status: {reason}\n\n"
            f"Please reach out to our team if you have any questions or would like to submit additional information.\n\n"
            f"The Talkar Team"
        )
    )
    await push_notification(customer_id, "Application Update", f"Your application was not approved: {reason}", "warning")

async def notify_customer_phone_approved(customer_id: int, numbers: list[str]):
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    phones_str = ", ".join(numbers)
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Phone Number Request Approved",
        body=(
            f"Hi {name},\n\n"
            f"Your phone number request has been approved! The following numbers have been assigned to your workspace:\n\n"
            f"📞 {phones_str}\n\n"
            f"You can now configure these numbers in your dashboard.\n\n"
            f"The Talkar Team"
        ),
        notification_type="info",
        push_body=f"Your request was approved. Numbers assigned: {phones_str}"
    )

async def notify_customer_phone_denied(customer_id: int, admin_note: str):
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    note = admin_note or "Does not meet provider requirements."
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Phone Number Request Update",
        body=(
            f"Hi {name},\n\n"
            f"We have reviewed your recent phone number request.\n\n"
            f"Unfortunately, we are unable to fulfill this request at this time.\n\n"
            f"Reason:\n{note}\n\n"
            f"Please reply to this email or open a support ticket if you need assistance.\n\n"
            f"The Talkar Team"
        ),
        notification_type="warning",
        push_body=f"Your phone number request was denied: {note}"
    )

async def notify_customer_phone_number_lease_billed(
    customer_id: int,
    number: str,
    amount_paise: int,
    new_balance_paise: int,
    is_renewal: bool = True,
):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    action = "renewed" if is_renewal else "activated"
    amount_rupees = amount_paise // 100
    balance_rupees = new_balance_paise / 100.0
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject=f"Phone Number Lease {action.capitalize()}: {number}",
        body=(
            f"Hi {name},\n\n"
            f"Your dedicated phone number {number} lease has been {action}.\n\n"
            f"Monthly Lease Amount: ₹{amount_rupees}\n"
            f"Current Wallet Balance: ₹{balance_rupees:.2f}\n\n"
            f"Thank you for using Talkar!\n\n"
            f"The Talkar Team"
        ),
        notification_type="billing",
        push_body=f"Phone number {number} lease {action} (₹{amount_rupees}). Balance: ₹{balance_rupees:.2f}",
    )

async def notify_customer_support_replied(customer_id: int, subject: str, admin_note: str, status: str):
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    
    body = f"Hi {name},\n\nThere is an update on your support ticket: '{subject}'.\n\n"
    if admin_note:
        body += f"Message from Talkar Support:\n{admin_note}\n\n"
    formatted_status = status.replace('_', ' ').title()
    body += f"Current Ticket Status: {formatted_status}\n\n"
    body += f"Log in to your dashboard to view more details.\n\nThe Talkar Team"
    
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject=f"Update on Support Ticket: {subject}",
        body=body,
        notification_type="support",
        push_body=f"Ticket '{subject}' updated. Status: {formatted_status}."
    )

SUSPENSION_REASON_LABELS = {
    "zero_balance": "zero wallet balance for an extended period",
    "policy_violation": "a violation of our Terms of Service",
    "fraud": "suspicious account activity detected",
    "other": "an administrative review",
}

async def notify_customer_suspended(customer_id: int, reason: str = "zero_balance", custom_message: str = None):
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    reason_label = SUSPENSION_REASON_LABELS.get(reason, "an administrative review")
    body = (
        f"Hi {name},\n\n"
        f"Your Talkar workspace and calling services have been suspended due to {reason_label}.\n\n"
    )
    if custom_message:
        body += f"Additional information from Talkar:\n{custom_message}\n\n"
    body += (
        f"To reactivate your account, please top up your wallet or contact our support team.\n\n"
        f"The Talkar Team"
    )
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Important: Your Talkar Workspace has been Suspended",
        body=body,
        notification_type="warning",
        push_body=f"Your workspace has been suspended ({reason_label}). Top up to reactivate.",
        cascade_to_suborgs=True,
    )

async def notify_customer_credit_granted(customer_id: int, amount_paise: int, description: str):
    # Don't notify customers while their agent is still being built
    if await _is_building_stage(customer_id):
        logger.info(f"[notify_customer_credit_granted] Suppressed for customer {customer_id} (building stage)")
        return
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    amount_rs = abs(amount_paise) / 100
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Talkar Wallet Credit Applied",
        body=(
            f"Hi {name},\n\n"
            f"A promotional or manual credit of ₹{amount_rs:,.2f} has been applied to your Talkar wallet.\n\n"
            f"Description: {description}\n\n"
            f"Thank you for using Talkar!\n\n"
            f"The Talkar Team"
        ),
        notification_type="billing",
        push_body=f"A credit of ₹{amount_rs:,.2f} was applied to your wallet."
    )

async def notify_customer_tier_upgrade_denied(customer_id: int, requested_tier: str):
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    await send_email_and_push(
        customer_id=customer_id,
        to_email=email,
        subject="Update on your Tier Upgrade Request",
        body=(
            f"Hi {name},\n\n"
            f"We have reviewed your request to upgrade to the {requested_tier.title()} tier.\n\n"
            f"We are unable to approve this upgrade automatically based on your current account status and usage history.\n\n"
            f"Please reach out to our enterprise team to discuss your scaling needs.\n\n"
            f"The Talkar Team"
        ),
        notification_type="warning",
        push_body=f"Your request to upgrade to the {requested_tier.title()} tier was denied."
    )


# =====================================================================
# SCHEDULED ACCOUNT SUMMARY REPORTS (Pure SQL & Talkar-Themed HTML)
# =====================================================================

IST_TZ = timezone(timedelta(hours=5, minutes=30))

def format_report_duration(seconds: Any) -> str:
    """Format seconds into human-readable Xh Ym Zs."""
    if not seconds:
        return "0s"
    try:
        sec = int(seconds)
    except (ValueError, TypeError):
        return "0s"
    if sec <= 0:
        return "0s"
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    rem_seconds = sec % 60
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if rem_seconds > 0 or not parts:
        parts.append(f"{rem_seconds}s")
    return " ".join(parts)

def format_report_inr(paise: Any) -> str:
    """Format paise into INR currency display safely handling Decimal, float, int, None."""
    if paise is None:
        return "₹0.00"
    try:
        val = float(paise)
        return f"₹{val / 100.0:,.2f}"
    except (ValueError, TypeError):
        return "₹0.00"

def calculate_next_report_due_date(frequency: str, from_dt: Optional[datetime] = None) -> datetime:
    """Calculate next report delivery timestamp at 08:00 AM IST (02:30 UTC)."""
    now = from_dt or datetime.now(timezone.utc)
    now_ist = now.astimezone(IST_TZ)
    
    if frequency == "daily":
        next_ist = now_ist.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    elif frequency == "monthly":
        year = now_ist.year + (1 if now_ist.month == 12 else 0)
        month = 1 if now_ist.month == 12 else now_ist.month + 1
        next_ist = datetime(year, month, 1, 8, 0, 0, tzinfo=IST_TZ)
    else:  # weekly (every Monday)
        days_ahead = (0 - now_ist.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_ist = (now_ist + timedelta(days=days_ahead)).replace(hour=8, minute=0, second=0, microsecond=0)
        
    return next_ist.astimezone(timezone.utc)

async def get_organization_report_data(
    db: AsyncSession,
    customer_id: int,
    period_start: datetime,
    period_end: datetime,
    customer: Optional[Any] = None
) -> Dict[str, Any]:
    """Pure SQL deterministic metrics calculation for an organization."""
    from sqlalchemy import text, select
    from db.models import Customer

    if customer is None:
        c_res = await db.execute(select(Customer).where(Customer.id == customer_id))
        customer = c_res.scalar_one_or_none()

    is_sub_org = bool(customer and customer.billing_org_id)
    
    # 1. Overall Call Metrics
    call_query = text("""
        SELECT 
            COUNT(id) AS total_calls,
            COUNT(CASE WHEN duration_seconds >= 10 THEN 1 END) AS completed_calls,
            COUNT(CASE WHEN duration_seconds < 10 THEN 1 END) AS dropped_calls,
            COALESCE(SUM(duration_seconds), 0) AS total_duration_seconds,
            COALESCE(SUM(cost_to_customer_paise), 0) AS total_cost_paise
        FROM call_logs
        WHERE customer_id = :customer_id
          AND called_at >= :start_time
          AND called_at <= :end_time
    """)
    call_res = await db.execute(call_query, {
        "customer_id": customer_id,
        "start_time": period_start,
        "end_time": period_end
    })
    call_row = call_res.fetchone()

    total_calls = int(call_row.total_calls or 0) if call_row else 0
    completed_calls = int(call_row.completed_calls or 0) if call_row else 0
    dropped_calls = int(call_row.dropped_calls or 0) if call_row else 0
    total_duration_sec = int(call_row.total_duration_seconds or 0) if call_row else 0
    total_cost_paise = int(call_row.total_cost_paise or 0) if call_row else 0
    
    completion_rate = (completed_calls / total_calls * 100.0) if total_calls > 0 else 0.0
    avg_call_duration_sec = (total_duration_sec // total_calls) if total_calls > 0 else 0

    # 2. Agent Breakdown
    agent_query = text("""
        SELECT 
            COALESCE(a.name, 'Default Agent') AS agent_name,
            COUNT(cl.id) AS calls,
            COALESCE(SUM(cl.duration_seconds), 0) AS duration_seconds,
            COALESCE(SUM(cl.cost_to_customer_paise), 0) AS cost_paise
        FROM call_logs cl
        LEFT JOIN agents a ON a.id = cl.agent_id
        WHERE cl.customer_id = :customer_id
          AND cl.called_at >= :start_time
          AND cl.called_at <= :end_time
        GROUP BY COALESCE(a.name, 'Default Agent')
        ORDER BY calls DESC
    """)
    agent_res = await db.execute(agent_query, {
        "customer_id": customer_id,
        "start_time": period_start,
        "end_time": period_end
    })
    agents_data = [
        {
            "name": r.agent_name,
            "calls": int(r.calls or 0),
            "duration_seconds": int(r.duration_seconds or 0),
            "formatted_duration": format_report_duration(r.duration_seconds),
            "cost_paise": int(r.cost_paise or 0),
            "formatted_cost": format_report_inr(r.cost_paise)
        }
        for r in agent_res.fetchall()
    ]

    # 3. Wallet Balance vs Sub-Org Budget Isolation (Issue 16)
    # Master float balance must NEVER be exposed to sub-org clients in scheduled reports.
    current_balance_paise = None
    formatted_balance = None
    monthly_cap_paise = None
    formatted_cap = None

    if is_sub_org:
        form = (customer.onboarding_form or {}) if customer else {}
        sub_cap = form.get("monthly_spending_cap_paise")
        if sub_cap is not None and int(sub_cap) > 0:
            monthly_cap_paise = int(sub_cap)
            formatted_cap = format_report_inr(monthly_cap_paise)
        formatted_balance = "Managed by Master Organization"
    else:
        from services.billing_service import get_billing_wallet
        wallet, _ = await get_billing_wallet(db, customer_id)
        current_balance_paise = int(wallet.balance_paise or 0) if wallet else 0
        formatted_balance = format_report_inr(current_balance_paise)

    return {
        "total_calls": total_calls,
        "completed_calls": completed_calls,
        "dropped_calls": dropped_calls,
        "completion_rate": round(completion_rate, 1),
        "total_duration_sec": total_duration_sec,
        "formatted_duration": format_report_duration(total_duration_sec),
        "avg_duration_sec": avg_call_duration_sec,
        "formatted_avg_duration": format_report_duration(avg_call_duration_sec),
        "total_cost_paise": total_cost_paise,
        "formatted_cost": format_report_inr(total_cost_paise),
        "is_sub_org": is_sub_org,
        "current_balance_paise": current_balance_paise,
        "formatted_balance": formatted_balance,
        "monthly_cap_paise": monthly_cap_paise,
        "formatted_cap": formatted_cap,
        "agents": agents_data
    }

def render_report_html(data: Dict[str, Any], company_name: str, period_label: str, date_range_str: str) -> str:
    """Generate a pixel-perfect, responsive Talkar-branded HTML email."""
    if data.get("is_sub_org"):
        if data.get("formatted_cap"):
            balance_note = f"Monthly Cap: {data['formatted_cap']}"
        else:
            balance_note = "Billing: Central Master Account"
        cta_url = "https://talkar.in/dashboard"
        cta_text = "View Live Analytics"
    else:
        balance_note = f"Wallet Balance: {data.get('formatted_balance', '₹0.00')}"
        cta_url = "https://talkar.in/wallet"
        cta_text = "View Live Analytics & Wallet"

    agent_rows_html = ""
    if data["agents"]:
        for ag in data["agents"]:
            agent_rows_html += f"""
            <tr style="border-bottom: 1px solid #f4f4f5;">
                <td style="padding: 10px 12px; font-size: 13px; font-weight: 600; color: #18181b;">{ag['name']}</td>
                <td style="padding: 10px 12px; font-size: 13px; color: #3f3f46; text-align: right;">{ag['calls']:,}</td>
                <td style="padding: 10px 12px; font-size: 13px; color: #3f3f46; text-align: right;">{ag['formatted_duration']}</td>
                <td style="padding: 10px 12px; font-size: 13px; font-weight: 600; color: #09090b; text-align: right;">{ag['formatted_cost']}</td>
            </tr>
            """
    else:
        agent_rows_html = """
        <tr>
            <td colspan="4" style="padding: 16px; text-align: center; color: #a1a1aa; font-size: 13px;">
                No calls recorded in this time window.
            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Talkar {period_label} Summary</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #fafafa; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }}
            .container {{ max-width: 600px; margin: 32px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e4e4e7; box-shadow: 0 4px 12px rgba(9, 9, 11, 0.03); }}
            .header {{ background-color: #09090b; padding: 24px 32px; text-align: center; border-bottom: 2px solid #fe6905; }}
            .body-content {{ padding: 32px; }}
            .kpi-grid {{ width: 100%; border-collapse: separate; border-spacing: 10px; margin-bottom: 24px; }}
            .kpi-card {{ background-color: #fafafa; border: 1px solid #e4e4e7; border-radius: 8px; padding: 14px 16px; text-align: left; }}
            .kpi-label {{ font-size: 10px; font-weight: 700; color: #71717a; text-transform: uppercase; letter-spacing: 0.5px; margin: 0 0 4px 0; }}
            .kpi-value {{ font-size: 20px; font-weight: 700; color: #09090b; margin: 0; }}
            .table-container {{ background-color: #fafafa; border: 1px solid #e4e4e7; border-radius: 8px; overflow: hidden; margin-top: 14px; }}
            .agent-table {{ width: 100%; border-collapse: collapse; }}
            .agent-table th {{ background-color: #f4f4f5; padding: 10px 12px; font-size: 11px; font-weight: 700; color: #71717a; text-transform: uppercase; letter-spacing: 0.5px; text-align: left; border-bottom: 1px solid #e4e4e7; }}
            .btn-cta {{ display: inline-block; background-color: #fe6905; color: #ffffff !important; font-size: 13px; font-weight: 600; padding: 12px 28px; border-radius: 6px; text-decoration: none; }}
            .footer {{ background-color: #fafafa; padding: 20px 32px; text-align: center; font-size: 11px; color: #a1a1aa; border-top: 1px solid #f4f4f5; }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Header -->
            <div class="header">
                <table border="0" cellpadding="0" cellspacing="0" width="100%">
                    <tr>
                        <td align="center">
                            <img src="https://talkar.in/logo-white.png" alt="Talkar" width="140" style="display: block; max-width: 140px; height: auto;" />
                        </td>
                    </tr>
                </table>
            </div>

            <!-- Main Content -->
            <div class="body-content">
                <div style="margin-bottom: 24px;">
                    <span style="font-size: 11px; font-weight: 700; color: #fe6905; text-transform: uppercase; letter-spacing: 0.5px;">Account Performance Digest</span>
                    <h2 style="font-size: 20px; font-weight: 700; color: #09090b; margin: 4px 0 6px 0;">{period_label} Summary for {company_name}</h2>
                    <p style="font-size: 13px; color: #71717a; margin: 0;">Period: {date_range_str}</p>
                </div>

                <!-- 4 KPI Cards -->
                <table class="kpi-grid" cellpadding="0" cellspacing="0">
                    <tr>
                        <td class="kpi-card" width="50%">
                            <p class="kpi-label">Total Calls</p>
                            <p class="kpi-value">{data['total_calls']:,}</p>
                            <span style="font-size: 11px; color: #71717a;">Avg: {data['formatted_avg_duration']}/call</span>
                        </td>
                        <td class="kpi-card" width="50%">
                            <p class="kpi-label">Connected Time</p>
                            <p class="kpi-value">{data['formatted_duration']}</p>
                            <span style="font-size: 11px; color: #71717a;">Total voice talk time</span>
                        </td>
                    </tr>
                    <tr>
                        <td class="kpi-card" width="50%">
                            <p class="kpi-label">Call Success Rate</p>
                            <p class="kpi-value" style="color: {'#16a34a' if data['completion_rate'] >= 80 else '#d97706'};">{data['completion_rate']}%</p>
                            <span style="font-size: 11px; color: #71717a;">{data['completed_calls']:,} connected calls</span>
                        </td>
                        <td class="kpi-card" width="50%">
                            <p class="kpi-label">Total Spent</p>
                            <p class="kpi-value">{data['formatted_cost']}</p>
                            <span style="font-size: 11px; color: #71717a;">{balance_note}</span>
                        </td>
                    </tr>
                </table>

                <!-- Agent Breakdown Table -->
                <div style="margin-top: 24px;">
                    <h3 style="font-size: 13px; font-weight: 700; color: #09090b; margin: 0 0 8px 0; text-transform: uppercase; letter-spacing: 0.5px;">Voice Agent Breakdown</h3>
                    <div class="table-container">
                        <table class="agent-table" cellpadding="0" cellspacing="0">
                            <thead>
                                <tr>
                                    <th>Agent Name</th>
                                    <th style="text-align: right;">Calls</th>
                                    <th style="text-align: right;">Duration</th>
                                    <th style="text-align: right;">Deductions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {agent_rows_html}
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- CTA Button -->
                <div style="text-align: center; margin-top: 32px;">
                    <a href="{cta_url}" class="btn-cta" target="_blank">{cta_text}</a>
                </div>
            </div>

            <!-- Footer -->
            <div class="footer">
                &copy; 2026 Talkar AI. All rights reserved.<br>
                This automated summary was sent per your organization's report preferences.<br>
                Manage or unsubscribe anytime in your Platform Settings.
            </div>
        </div>
    </body>
    </html>
    """

async def send_organization_report(
    db: AsyncSession,
    customer: Any,
    frequency: str = "weekly",
    is_test: bool = False,
    recipient_override: Optional[List[str]] = None
) -> bool:
    """Calculates report data, generates the HTML, and sends it to recipients."""
    now_utc = datetime.now(timezone.utc)
    
    if frequency == "daily":
        start_time = now_utc - timedelta(days=1)
        period_label = "Daily"
    elif frequency == "monthly":
        start_time = now_utc - timedelta(days=30)
        period_label = "Monthly"
    else:  # weekly
        start_time = now_utc - timedelta(days=7)
        period_label = "Weekly"

    if is_test:
        period_label = f"Sample {period_label}"

    data = await get_organization_report_data(
        db=db,
        customer_id=customer.id,
        period_start=start_time,
        period_end=now_utc,
        customer=customer
    )

    company_name = customer.company_name or "Your Organization"
    date_range_str = f"{start_time.strftime('%b %d, %Y')} – {now_utc.strftime('%b %d, %Y')}"
    
    html_content = render_report_html(
        data=data,
        company_name=company_name,
        period_label=period_label,
        date_range_str=date_range_str
    )

    recipients = recipient_override
    if not recipients:
        rep_settings = customer.report_settings or {}
        configured = rep_settings.get("recipients", []) if isinstance(rep_settings, dict) else []
        if configured and isinstance(configured, list) and len(configured) > 0:
            recipients = [r.strip() for r in configured if r.strip()]
        else:
            recipients = [customer.contact_email] if customer.contact_email else []

    if not recipients:
        logger.warning(f"No recipients found for report delivery (customer_id={customer.id})")
        return False

    subject = f"Talkar {period_label} Summary - {company_name} ({date_range_str})"
    if is_test:
        subject = f"[PREVIEW] {subject}"

    if not settings.SMTP_HOST or not settings.SMTP_PASSWORD:
        logger.info(f"[REPORT EMAIL MOCK] To: {recipients} | Subject: {subject}")
        return True

    def _send_sync(to_addr: str):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Talkar <{settings.FROM_EMAIL}>"
        msg["To"] = to_addr
        msg.attach(MIMEText("Please view this report in an HTML-compatible email client.", "plain"))
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_EMAIL, settings.SMTP_PASSWORD)
            server.sendmail(settings.FROM_EMAIL, to_addr, msg.as_string())
            logger.info(f"Report email sent to {to_addr} (customer_id={customer.id})")

    success = True
    for to_email in recipients:
        try:
            await asyncio.to_thread(_send_sync, to_email)
        except Exception as e:
            logger.error(f"Failed to send report email to {to_email}: {e}")
            success = False

    return success


async def send_vendor_balance_alert(
    vendor: str,
    display_name: str,
    tier: str,
    balance: float | None,
    units: str,
    threshold: float,
    auto_recharge: bool = False,
    overages_enabled: bool = False,
    next_reset_at: datetime | None = None,
    console_url: str = "",
    recipient: str | None = None,
) -> bool:
    """Send priority infrastructure balance alert to it@4thorbit.com."""
    to_addr = recipient or getattr(settings, "VENDOR_ALERT_EMAIL", "it@4thorbit.com")
    balance_str = f"{balance:,.2f}" if balance is not None else "Unknown"

    if tier == "critical":
        subject = f"🚨 [CRITICAL] {display_name} Balance Exhausted: {balance_str} {units}"
        urgency = "URGENT ACTION REQUIRED: Provider balance is at or below critical threshold. Active campaigns and inbound calls relying on this service may fail immediately."
    elif tier == "warning":
        subject = f"⚠️ [WARNING] {display_name} Balance Low: {balance_str} {units} remaining"
        urgency = "ATTENTION REQUIRED: Provider balance has dropped below the safety threshold. Please refill promptly to prevent campaign disruption."
    elif tier == "recovered":
        subject = f"✅ [RESOLVED] {display_name} Balance Restored: {balance_str} {units}"
        urgency = "STATUS NORMAL: Provider credits have been replenished comfortably above the safety threshold."
    else:
        subject = f"ℹ️ [NOTICE] {display_name} Infrastructure Status Update"
        urgency = "Infrastructure status notification."

    lines = [
        "Hello Infrastructure Team,",
        "",
        urgency,
        "",
        "Infrastructure Service Details:",
        f"- Vendor: {display_name} ({vendor})",
        f"- Current Balance: {balance_str} {units}",
        f"- Safety Threshold: {threshold:,.2f} {units}",
    ]

    if auto_recharge:
        lines.append("- Auto-Recharge: ENABLED (Backup payment method is on file)")
    if overages_enabled:
        lines.append("- Usage Overages: ACTIVE (Account is configured for pay-as-you-go overflow)")
    if next_reset_at:
        lines.append(f"- Scheduled Renewal: {next_reset_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if console_url:
        lines.append(f"- Console Top-Up Link: {console_url}")

    lines.extend([
        "",
        "Please review the Talkar Admin Dashboard for live fleet status:",
        "https://talkar.in/admin",
        "",
        "The Talkar Team",
    ])

    body_text = "\n".join(lines)
    try:
        sent = await send_email(to_email=to_addr, subject=subject, body=body_text)
        if sent:
            logger.info(f"Vendor balance alert sent to {to_addr} for {vendor} (tier={tier})")
        else:
            logger.warning(f"Failed to dispatch vendor balance alert to {to_addr} for {vendor}")
        return sent
    except Exception as e:
        logger.error(f"Error sending vendor balance alert for {vendor} to {to_addr}: {e}")
        return False
