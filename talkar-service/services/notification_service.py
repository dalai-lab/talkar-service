import logging
import asyncio
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import settings
from db.session import AsyncSessionLocal
from sqlalchemy import select

logger = logging.getLogger(__name__)


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


async def send_email(to_email: str, subject: str, body: str):
    """Send email via ZeptoMail SMTP (aiosmtplib would be ideal, but smtplib works via thread)."""
    if not settings.SMTP_PASSWORD:
        logger.info(f"[EMAIL MOCK] To: {to_email} | Subject: {subject} | Body: {body[:120]}")
        return

    def _send():
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
    await send_email(
        to_email=email,
        subject="Your Talkar Agent is Live & Ready",
        body=(
            f"Hi {name},\n\n"
            f"Your AI voice assistant is fully configured and ready to handle live calls. You can now access your dashboard to monitor performance, manage call credits, and customize your agent's response behavior.\n\n"
            f"Thank you for choosing Talkar.\n\n"
            f"The Talkar Team"
        )
    )

async def notify_customer_self_serve_active(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    await send_email(
        to_email=email,
        subject="Welcome to Talkar — Account Activated",
        body=(
            f"Hi {name},\n\n"
            f"We have successfully processed your setup fee. Your account is active and your workspace is fully unlocked.\n\n"
            f"Log in to your dashboard to begin configuring your voice agent and managing call flows.\n\n"
            f"The Talkar Team"
        )
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


async def notify_customer_auto_recharge_failed(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        return
    email, name = info
    await send_email(
        to_email=email,
        subject="Action Required: Auto-Recharge Failed",
        body=(
            f"Hi {name},\n\n"
            f"We were unable to process your automatic wallet recharge. To ensure your voice agent remains online and active, please update your billing method on your dashboard.\n\n"
            f"The Talkar Team"
        )
    )


async def notify_admin_auto_recharge_failed(customer_id: int):
    await send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar Admin] Billing Warning: Auto-Recharge Failed",
        body=(
            f"Auto-recharge transaction failed for customer ID #{customer_id}.\n\n"
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
    balance_rs = balance_paise / 100
    await send_email(
        to_email=email,
        subject="Low Balance Warning: Talkar Wallet",
        body=(
            f"Hi {name},\n\n"
            f"Your Talkar wallet balance is running low at ₹{balance_rs:.2f}.\n\n"
            f"Please add credits to your wallet on the dashboard to ensure calling services remain active without interruption.\n\n"
            f"The Talkar Team"
        )
    )


async def notify_customer_service_paused(customer_id: int):
    info = await _get_customer_email(customer_id)
    if not info:
        logger.warning(f"Emailing customer {customer_id}: balance below threshold, calls paused.")
        return
    email, name = info
    await send_email(
        to_email=email,
        subject="Urgent: Talkar Calling Service Paused",
        body=(
            f"Hi {name},\n\n"
            f"Your Talkar wallet balance has dropped below the minimum operating threshold (₹500). To prevent unpaid usage, call routing services have been temporarily paused.\n\n"
            f"Please top up your wallet on the dashboard to instantly reactivate your lines.\n\n"
            f"The Talkar Team"
        )
    )

async def notify_customer_topup_successful(customer_id: int, amount_paise: int, new_balance_paise: int):
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    amount_rs = amount_paise / 100
    balance_rs = new_balance_paise / 100
    await send_email(
        to_email=email,
        subject="Payment Confirmed: Talkar Wallet Credits",
        body=(
            f"Hi {name},\n\n"
            f"Your payment of ₹{amount_rs:,.2f} was processed successfully.\n\n"
            f"Your updated wallet balance is ₹{balance_rs:,.2f}.\n\n"
            f"Thank you for partnering with us.\n\n"
            f"The Talkar Team"
        )
    )

async def notify_customer_tier_upgraded(customer_id: int, new_tier: str):
    info = await _get_customer_email(customer_id)
    if not info: return
    email, name = info
    await send_email(
        to_email=email,
        subject=f"Tier Upgrade Confirmed: {new_tier.capitalize()} Engine",
        body=(
            f"Hi {name},\n\n"
            f"Your account has been successfully upgraded to the {new_tier.capitalize()} tier. Your new calling capacity and discounted per-minute rates are now active.\n\n"
            f"Enjoy the upgraded performance!\n\n"
            f"The Talkar Team"
        )
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
