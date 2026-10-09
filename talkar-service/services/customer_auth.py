import logging
import secrets
import jwt
from typing import Optional
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from config import settings
from db.models import Customer
from services import dograh_client

logger = logging.getLogger(__name__)

TALKAR_ADMIN_JWT_SECRET = settings.TALKAR_ADMIN_JWT_SECRET
ALGORITHM = "HS256"


def is_admin_or_service_request(authorization: Optional[str]) -> bool:
    """Checks if the request is authenticated as Talkar admin or internal service."""
    if not authorization:
        return False

    token = authorization[7:].strip() if authorization.startswith("Bearer ") else authorization.strip()
    if not token:
        return False

    # Check internal service token
    expected_service_token = getattr(settings, "TALKAR_BILLING_API_TOKEN", "")
    if expected_service_token and secrets.compare_digest(token, expected_service_token):
        return True

    # Check admin JWT
    try:
        payload = jwt.decode(token, TALKAR_ADMIN_JWT_SECRET, algorithms=[ALGORITHM])
        if payload.get("is_admin"):
            return True
    except Exception:
        pass

    return False


async def verify_customer_access(
    customer: Customer,
    db: AsyncSession,
    x_talkar_email: Optional[str] = None,
    authorization: Optional[str] = None,
) -> None:
    """
    Verifies that the caller has permission to access or modify resources belonging to customer.
    Allows:
      1. Talkar Admin or Internal service (via Authorization header).
      2. Authenticated user matching customer contact email.
      3. Authenticated user owning a related workspace (sub-org / master org).
      4. Authenticated user who is a member of the customer's Dograh org.
    Raises:
      HTTPException(401) if unauthenticated.
      HTTPException(403) if unauthorized.
    """
    # 1. Admin or internal service
    if is_admin_or_service_request(authorization):
        return

    # 2. Authenticated user via session header
    if not x_talkar_email:
        logger.warning(f"Unauthenticated access attempt to customer {customer.id} resources.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to access this resource."
        )

    caller_email = x_talkar_email.strip().lower()

    # Direct match on customer contact_email
    if customer.contact_email and customer.contact_email.strip().lower() == caller_email:
        return

    # Check if caller owns a related workspace (sub-org or master org)
    master_id = customer.billing_org_id if customer.billing_org_id else customer.id
    related_res = await db.execute(
        select(Customer).where(
            (Customer.contact_email == caller_email) &
            ((Customer.id == master_id) | (Customer.billing_org_id == master_id))
        )
    )
    if related_res.scalar_one_or_none():
        return

    # Check if caller is a verified member of this customer's Dograh org in Dograh DB
    if customer.dograh_org_id:
        is_member = await dograh_client.verify_dograh_org_user(
            org_id=customer.dograh_org_id,
            email=caller_email
        )
        if is_member is True:
            return

    logger.warning(
        f"IDOR / unauthorized access blocked: Caller '{caller_email}' "
        f"attempted to access customer {customer.id} ('{customer.contact_email}')."
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: You do not have permission to access this resource."
    )
