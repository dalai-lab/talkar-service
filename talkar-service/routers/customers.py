from fastapi import APIRouter, Depends, Query, HTTPException, Header
from fastapi.params import Header as HeaderParam
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from db.session import get_db
from db.models import Customer, Subscription, TalkarAdmin
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date, timedelta
import logging
from services import notification_service, dograh_client
from services.admin_auth import get_current_admin
from services.customer_auth import resolve_authenticated_email, verify_customer_access, is_admin_or_service_request
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

class CreateCustomerRequest(BaseModel):
    email: str
    contact_name: str
    company_name: str = ""
    dograh_org_id: int
    dograh_user_id: int
    account_type: Optional[str] = None  # "shared" or "independent"
    link_billing: Optional[bool] = None  # True to link to existing billing, False for independent
    billing_mode: Optional[str] = None  # "shared" or "independent"

class SetBillingAccountTypeRequest(BaseModel):
    account_type: str  # "shared" or "independent"
    master_customer_id: Optional[int] = None

class UpdateStatusRequest(BaseModel):
    status: str
    reason: Optional[str] = None

@router.post("/")
async def create_customer(
    data: CreateCustomerRequest,
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(None),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email")
):
    from services.customer_auth import is_admin_or_service_request
    if not is_admin_or_service_request(authorization, x_talkar_email):
        raise HTTPException(
            status_code=401,
            detail="Authentication required to register customer."
        )

    wants_independent = (
        (data.account_type and data.account_type.strip().lower() == "independent")
        or data.link_billing is False
        or (data.billing_mode and data.billing_mode.strip().lower() == "independent")
    )
    wants_shared = (
        (data.account_type and data.account_type.strip().lower() in ("shared", "sub_org", "linked"))
        or data.link_billing is True
        or (data.billing_mode and data.billing_mode.strip().lower() in ("shared", "sub_org", "linked"))
    )

    result = await db.execute(
        select(Customer)
        .where(Customer.dograh_org_id == data.dograh_org_id)
        .order_by(Customer.updated_at.desc())
        .limit(1)
    )
    existing_org = result.scalars().first()
    if existing_org:
        # If caller explicitly specified account_type and it differs from existing_org state, update it
        if wants_independent and existing_org.billing_org_id is not None:
            existing_org.billing_org_id = None
            await db.commit()
            await db.refresh(existing_org)
            logger.info(f"Updated existing org {data.dograh_org_id} to independent billing (billing_org_id=None)")
        elif wants_shared and existing_org.billing_org_id is None:
            is_owner = await dograh_client.verify_dograh_org_user(
                org_id=data.dograh_org_id,
                user_id=data.dograh_user_id,
                email=data.email,
                require_owner=True
            )
            if is_owner is False:
                raise HTTPException(403, "Only organization owners/admins can link an organization to a shared billing wallet.")

            ESTABLISHED_STATUSES = ["active", "agent_building", "approved", "pending_deposit",
                                    "under_review", "suspended"]
            res_m = await db.execute(
                select(Customer)
                .where(Customer.contact_email == data.email)
                .where(Customer.id != existing_org.id)
                .where(Customer.status.in_(ESTABLISHED_STATUSES))
                .order_by(Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc())
                .limit(1)
            )
            master_cust = res_m.scalar_one_or_none()
            if master_cust:
                target_master = master_cust.billing_org_id if master_cust.billing_org_id else master_cust.id
                if target_master != existing_org.id:
                    existing_org.billing_org_id = target_master
                    await db.commit()
                    await db.refresh(existing_org)
                    logger.info(f"Updated existing org {data.dograh_org_id} to shared billing (billing_org_id={target_master})")
        return existing_org

    # Basic rate limiting: prevent spamming new workspace creations
    recent_spam = await db.execute(
        select(Customer)
        .where(Customer.contact_email == data.email)
        .where(Customer.created_at > func.now() - timedelta(minutes=1))
        .limit(1)
    )
    spam_record = recent_spam.scalar_one_or_none()
    if spam_record:
        # Exclude existing authenticated user workspace linkings from the 60-second creation spam block (Fix 22 / Issue 20)
        is_linking = wants_shared or (not wants_independent)
        if is_linking:
            ESTABLISHED_STATUSES = ["active", "agent_building", "approved", "pending_deposit",
                                    "under_review", "suspended"]
            master_check = await db.execute(
                select(Customer)
                .where(Customer.contact_email == data.email)
                .where(Customer.status.in_(ESTABLISHED_STATUSES))
                .limit(1)
            )
            if not master_check.scalar_one_or_none():
                raise HTTPException(429, "Too many requests. Please wait a minute before creating another workspace.")
        else:
            raise HTTPException(429, "Too many requests. Please wait a minute before creating another workspace.")

    # Only link as a sub-org if an existing record is in a real "established" state.
    # Stale pending_approval or rejected records (e.g. from old test runs or failed
    # applications) should NOT cause a new workspace to be treated as a sub-org.
    ESTABLISHED_STATUSES = ["active", "agent_building", "approved", "pending_deposit",
                            "under_review", "suspended"]
    result_email = await db.execute(
        select(Customer)
        .where(Customer.contact_email == data.email)
        .where(Customer.status.in_(ESTABLISHED_STATUSES))
        # Prefer master orgs (no billing_org_id) so we link to the billing root
        .order_by(Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc())
        .limit(1)
    )
    existing_email = result_email.scalar_one_or_none()

    billing_org_id = None
    if existing_email:
        # Security: Prevent customer impersonation & workspace hijacking.
        # Verify that the requester owns the existing email / customer account.
        
        # 1. User Identity Check:
        # If the existing customer record has a dograh_user_id, the incoming dograh_user_id must match.
        if existing_email.dograh_user_id and existing_email.dograh_user_id != data.dograh_user_id:
            logger.warning(
                f"Security: Impersonation attempt detected. Incoming dograh_user_id={data.dograh_user_id} "
                f"attempted to register workspace for email '{data.email}', which is already owned by "
                f"dograh_user_id={existing_email.dograh_user_id} (Customer ID {existing_email.id})."
            )
            raise HTTPException(
                status_code=403,
                detail="Email is already associated with an existing customer account of another user."
            )

        # 2. Dograh Organization Membership Check:
        # Verify that data.dograh_user_id actually belongs to data.dograh_org_id in Dograh DB
        is_member = await dograh_client.verify_dograh_org_user(
            org_id=data.dograh_org_id,
            user_id=data.dograh_user_id,
            email=data.email
        )
        if is_member is False:
            logger.warning(
                f"Security: dograh_user_id={data.dograh_user_id} is not a member of dograh_org_id={data.dograh_org_id}."
            )
            raise HTTPException(
                status_code=403,
                detail="User is not authorized for this Dograh organization."
            )

        # 3. Billing linkage decision (Issue 8 Prevention):
        # A workspace can ONLY be linked to an existing master billing wallet if the user
        # is the verified Owner / Admin of this Dograh organization.
        # If the user was merely invited as a team member or collaborator to an external
        # client workspace, automatically linking it to their agency's wallet is a critical
        # privilege escalation / account hijacking risk.
        if wants_independent:
            logger.info(
                f"Independent billing requested for org {data.dograh_org_id} under existing email '{data.email}'. "
                f"Creating standalone master customer (billing_org_id=None)."
            )
            billing_org_id = None
        else:
            is_owner = await dograh_client.verify_dograh_org_user(
                org_id=data.dograh_org_id,
                user_id=data.dograh_user_id,
                email=data.email,
                require_owner=True
            )
            if is_owner is False:
                logger.info(
                    f"User '{data.email}' (dograh_user_id={data.dograh_user_id}) is a collaborator/member, "
                    f"NOT the owner/admin of Dograh org {data.dograh_org_id}. "
                    f"Refusing to link billing to agency account {existing_email.id}. Creating standalone customer (billing_org_id=None)."
                )
                if wants_shared:
                    raise HTTPException(
                        status_code=403,
                        detail="Only organization owners/admins can link an organization to a shared billing wallet."
                    )
                billing_org_id = None
            elif existing_email.dograh_user_id and existing_email.dograh_user_id == data.dograh_user_id:
                billing_org_id = existing_email.billing_org_id if existing_email.billing_org_id else existing_email.id
            elif not existing_email.dograh_user_id and is_member is True and is_owner is True:
                # Established customer created before dograh_user_id was tracked, verified as owner via Dograh DB
                billing_org_id = existing_email.billing_org_id if existing_email.billing_org_id else existing_email.id
            else:
                # Cannot verify ownership; do NOT link to existing billing account
                logger.warning(
                    f"Ownership of email '{data.email}' could not be verified against existing customer {existing_email.id}. "
                    f"Creating standalone customer without linking billing."
                )
                billing_org_id = None

    customer = Customer(
        contact_email=data.email,
        contact_name=data.contact_name,
        company_name=data.company_name,
        dograh_org_id=data.dograh_org_id,
        dograh_user_id=data.dograh_user_id,
        billing_org_id=billing_org_id,
        status="pending_approval"
    )
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    
    # Sub-org auto-provisioning is now deferred until they submit the brief
    # in save_agent_brief, so we know what they actually want us to build.
    if billing_org_id:
        logger.info(f"Sub-org {customer.id} created for master {billing_org_id}; awaiting brief submission")
    else:
        logger.info(f"Master customer {customer.id} created for org {customer.dograh_org_id}; independent billing account")
            
    return customer

@router.post("/by-org/{dograh_org_id}/billing-account-type")
async def set_billing_account_type(
    dograh_org_id: int,
    data: SetBillingAccountTypeRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Allow customer to explicitly choose or toggle billing account type for a workspace:
    - 'independent': Separate wallet, separate subscription, separate invoicing and credit cards.
    - 'shared': Link to master account (shared wallet, master plan, no setup fee).
    """
    result = await db.execute(
        select(Customer)
        .where(Customer.dograh_org_id == dograh_org_id)
        .order_by(Customer.updated_at.desc())
        .limit(1)
    )
    customer = result.scalars().first()
    if not isinstance(authorization, str):
        authorization = None
    if not isinstance(x_talkar_email, str):
        x_talkar_email = None
    caller_email = resolve_authenticated_email(authorization, x_talkar_email)
    
    if not customer:
        if not caller_email:
            raise HTTPException(status_code=401, detail="Authentication required to set billing account type.")
        
        is_member = await dograh_client.verify_dograh_org_user(
            org_id=dograh_org_id,
            email=caller_email if caller_email != "service" else None
        )
        if is_member is False:
            raise HTTPException(status_code=403, detail="User is not authorized for this Dograh organization.")

        customer = Customer(
            contact_email=caller_email if caller_email != "service" else "",
            contact_name=caller_email.split("@")[0] if caller_email and caller_email != "service" else "User",
            dograh_org_id=dograh_org_id,
            status="pending_approval",
            billing_org_id=None
        )
        db.add(customer)
        await db.flush()
    else:
        await verify_customer_access(customer, db, x_talkar_email, authorization)

    acct_type = data.account_type.strip().lower()
    if acct_type == "independent":
        customer.billing_org_id = None
        from db.models import Wallet
        from config import TIER_CONFIG
        w_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
        if not w_res.scalar_one_or_none():
            db.add(Wallet(customer_id=customer.id, balance_paise=0))
        s_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        if not s_res.scalar_one_or_none():
            db.add(Subscription(
                customer_id=customer.id,
                plan="starter",
                status="active",
                per_minute_rate_paise=TIER_CONFIG["starter"]["per_minute_rate_paise"],
                setup_fee_paid=True,
                start_date=date.today()
            ))
        customer.status = "pending_deposit"
        if customer.dograh_org_id:
            try:
                await dograh_client.block_org_calls(customer.dograh_org_id)
            except Exception as e:
                logger.error(f"Failed to block calls for detached org {customer.dograh_org_id}: {e}")

        # Blocker 11: When switching to independent, update CONCURRENCY_GROUP_ID to own org ID and invalidate Redis
        if customer.dograh_org_id:
            try:
                await dograh_client.upsert_org_config(
                    customer.dograh_org_id,
                    "CONCURRENCY_GROUP_ID",
                    {"value": customer.dograh_org_id}
                )
            except Exception as e:
                logger.error(f"Failed to reset CONCURRENCY_GROUP_ID for org {customer.dograh_org_id}: {e}")
            try:
                from services.redis_client import invalidate_concurrency_group
                await invalidate_concurrency_group(customer.dograh_org_id)
            except Exception as e:
                logger.error(f"Failed to invalidate redis concurrency group for org {customer.dograh_org_id}: {e}")
            try:
                await dograh_client.invalidate_dograh_concurrency_group(customer.dograh_org_id)
            except Exception as e:
                logger.error(f"Failed to invalidate Dograh concurrency group for org {customer.dograh_org_id}: {e}")

        await db.commit()
        await db.refresh(customer)
        logger.info(f"Customer {customer.id} (org {dograh_org_id}) set to independent billing account")
        return {
            "status": "ok",
            "account_type": "independent",
            "is_sub_org": False,
            "billing_org_id": None,
            "customer_id": customer.id,
            "customer_status": customer.status,
            "redirect_url": "/wallet?deposit_required=true" if customer.status == "pending_deposit" else None,
            "message": "Independent account detached. Please fund your wallet to activate service." if customer.status == "pending_deposit" else "Switched to independent account."
        }

    elif acct_type in ("shared", "sub_org", "linked"):
        # Security: Check that caller is the verified owner/admin of this organization (Issue 8 Prevention)
        is_owner = await dograh_client.verify_dograh_org_user(
            org_id=dograh_org_id,
            user_id=customer.dograh_user_id,
            email=customer.contact_email or (caller_email if caller_email != "service" else None),
            require_owner=True
        )
        if is_owner is False:
            raise HTTPException(status_code=403, detail="Only organization owners/admins can link an organization to a shared billing wallet.")

        master_id = data.master_customer_id
        if master_id:
            master_res = await db.execute(select(Customer).where(Customer.id == master_id))
            master_cust = master_res.scalar_one_or_none()
            if not master_cust:
                raise HTTPException(status_code=404, detail="Specified master billing account not found")
        else:
            ESTABLISHED_STATUSES = ["active", "agent_building", "approved", "pending_deposit",
                                    "under_review", "suspended"]
            email_to_match = customer.contact_email or caller_email
            res_m = await db.execute(
                select(Customer)
                .where(Customer.contact_email == email_to_match)
                .where(Customer.id != customer.id)
                .where(Customer.status.in_(ESTABLISHED_STATUSES))
                .order_by(Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc())
                .limit(1)
            )
            master_cust = res_m.scalar_one_or_none()
            if not master_cust:
                raise HTTPException(status_code=400, detail="No established master account found for this email.")

        if master_cust.dograh_user_id and customer.dograh_user_id:
            if master_cust.dograh_user_id != customer.dograh_user_id:
                raise HTTPException(status_code=403, detail="Cannot link to account owned by a different user.")

        target_master_id = master_cust.billing_org_id if master_cust.billing_org_id else master_cust.id
        if target_master_id == customer.id:
            raise HTTPException(status_code=400, detail="Cannot link customer to itself.")

        master_dograh_id = master_cust.dograh_org_id
        target_master_cust = None
        if master_cust.id != target_master_id:
            t_res = await db.execute(select(Customer).where(Customer.id == target_master_id))
            target_master_cust = t_res.scalar_one_or_none()
            if target_master_cust and target_master_cust.dograh_org_id:
                master_dograh_id = target_master_cust.dograh_org_id

        customer.billing_org_id = target_master_id

        # Blocker 3: Restore calls and status on sub-org relinking
        effective_master = target_master_cust if (master_cust.id != target_master_id and target_master_cust) else master_cust
        if effective_master and effective_master.status == "active":
            customer.status = "active"
            customer.suspended_at = None
            if customer.onboarding_form:
                c_form = dict(customer.onboarding_form)
                c_form.pop("suspension_reason", None)
                c_form.pop("suspension_message", None)
                customer.onboarding_form = c_form

        # Blocker 17: Transfer any positive balance in customer's independent wallet to master wallet
        from db.models import Wallet, WalletTransaction
        try:
            c_wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == customer.id))
            c_wallet = c_wallet_res.scalar_one_or_none() if c_wallet_res else None
        except (StopIteration, StopAsyncIteration):
            c_wallet = None

        if c_wallet and c_wallet.balance_paise > 0:
            transfer_amount = c_wallet.balance_paise
            c_wallet.balance_paise = 0

            try:
                m_wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == target_master_id))
                m_wallet = m_wallet_res.scalar_one_or_none() if m_wallet_res else None
            except (StopIteration, StopAsyncIteration):
                m_wallet = None

            if not m_wallet:
                m_wallet = Wallet(customer_id=target_master_id, balance_paise=0)
                db.add(m_wallet)

            m_wallet.balance_paise += transfer_amount

            txn = WalletTransaction(
                customer_id=target_master_id,
                sub_org_id=customer.id,
                dograh_org_id=customer.dograh_org_id,
                type="credit_transfer",
                amount_paise=transfer_amount,
                description=f"Transferred stranded balance of ₹{transfer_amount / 100:.2f} from independent workspace (org {customer.dograh_org_id}) upon linking to shared wallet",
            )
            db.add(txn)
            logger.info(f"Transferred stranded balance of {transfer_amount} paise from customer {customer.id} to master {target_master_id}")

        # Blocker 11: When linking to a shared wallet, update CONCURRENCY_GROUP_ID to master_dograh_id and invalidate Redis
        if customer.dograh_org_id and master_dograh_id:
            try:
                await dograh_client.upsert_org_config(
                    customer.dograh_org_id,
                    "CONCURRENCY_GROUP_ID",
                    {"value": master_dograh_id}
                )
            except Exception as e:
                logger.error(f"Failed to set CONCURRENCY_GROUP_ID for org {customer.dograh_org_id}: {e}")
            try:
                from services.redis_client import invalidate_concurrency_group
                await invalidate_concurrency_group(customer.dograh_org_id)
            except Exception as e:
                logger.error(f"Failed to invalidate redis concurrency group for org {customer.dograh_org_id}: {e}")
            try:
                await dograh_client.invalidate_dograh_concurrency_group(customer.dograh_org_id)
            except Exception as e:
                logger.error(f"Failed to invalidate Dograh concurrency group for org {customer.dograh_org_id}: {e}")

        await db.commit()
        await db.refresh(customer)
        logger.info(f"Customer {customer.id} (org {dograh_org_id}) linked to master {target_master_id} (shared wallet)")

        # Restore calls and synchronize concurrency limits in Dograh
        from services.billing_service import sync_wallet_block_policy
        try:
            await sync_wallet_block_policy(db, target_master_id)
        except (Exception, StopIteration, StopAsyncIteration) as e:
            logger.error(f"Failed to sync wallet block policy after linking customer {customer.id} to master {target_master_id}: {e}")

        return {
            "status": "ok",
            "account_type": "shared",
            "is_sub_org": True,
            "billing_org_id": customer.billing_org_id,
            "customer_id": customer.id
        }
    else:
        raise HTTPException(status_code=400, detail="Invalid account_type. Must be 'independent' or 'shared'.")

@router.get("/")
async def get_all_customers(
    status: Optional[str] = None,
    plan: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    query = select(Customer)
    if status:
        query = query.where(Customer.status == status)
    query = query.offset((page - 1) * limit).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()

# IMPORTANT: static paths must come before /{customer_id} to avoid shadowing
@router.get("/existing")
async def get_existing_customer(
    contact_email: str,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Check if an email already has an active/approved customer record in any org.
    Used by the frontend to detect returning customers creating a new workspace.
    Returns the master customer_id if found, or 404.
    """
    caller_email = resolve_authenticated_email(authorization, x_talkar_email)
    if not caller_email:
        raise HTTPException(status_code=401, detail="Authentication required to query customer records.")
    if caller_email != contact_email.strip().lower() and caller_email != "service":
        raise HTTPException(status_code=403, detail="Cannot query account status for other email addresses.")
    result = await db.execute(
        select(Customer)
        .where(Customer.contact_email == contact_email)
        .where(Customer.status.in_([
            "active", "agent_building", "pending_deposit",
            "under_review", "approved", "suspended"
        ]))
        # Prefer billing-master orgs (no billing_org_id)
        .order_by(Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc())
        .limit(1)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="No existing customer found")
    return {
        "customer_id": customer.id,
        "status": customer.status,
        "contact_name": customer.contact_name,
        "company_name": customer.company_name,
    }

@router.get("/by-email/{email:path}")
async def get_customer_by_email(
    email: str,
    dograh_org_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Deprecated: Unscoped route. Resolves customer record for an email.
    Deterministic ordering guarantees consistency (master org first, then lowest customer ID)
    preventing non-deterministic collisions between multiple orgs sharing an email.
    """
    logger.warning(f"Unscoped /customers/by-email route accessed for {email}. Use strictly scoped by-org routes instead.")
    query = select(Customer).where(Customer.contact_email == email.strip())
    if dograh_org_id is not None:
        query = query.where(Customer.dograh_org_id == dograh_org_id)
    else:
        query = query.order_by(Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc())

    result = await db.execute(query.limit(1))
    customer = result.scalars().first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(customer, db, email_str, auth_str)

    return {
        "id": customer.id,
        "customer_id": customer.id,
        "dograh_org_id": customer.dograh_org_id,
        "status": customer.status,
        "contact_name": customer.contact_name,
        "company_name": customer.company_name,
        "contact_email": customer.contact_email,
        "billing_org_id": customer.billing_org_id,
    }

@router.post("/by-org/{dograh_org_id}/new-agent-brief")
async def save_agent_brief(
    dograh_org_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Called when a customer fills the brief form on a workspace where
    a sub-org customer record already exists (auto-created by the Dograh workspace hook)
    or is in agent_building. Updates the existing record, preserves documents, and notifies admin.
    """
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="No customer record found for this org. Use /new-agent-request instead.")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(customer, db, email_str, auth_str)

    # Preserve any existing fields (e.g. approved_tier set by auto-provisioning)
    existing_form = customer.onboarding_form or {}
    existing_form.update(data.get("form", {}))
    customer.onboarding_form = existing_form
    
    if "documents" in data:
        customer.documents = data.get("documents", [])
    
    # Check if this is a sub-org and lookup master info
    master_is_active = False
    master_sub = None
    master_name = customer.company_name
    if customer.billing_org_id:
        master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
        master = master_res.scalar_one_or_none()
        if master:
            master_name = master.company_name
            if master.status == "active":
                master_is_active = True
                master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master.id))
                master_sub = master_sub_res.scalar_one_or_none()
            
    # Blocker 16: Only set to under_review if currently pending_approval; preserve active/agent_building
    if customer.status in ("pending_approval", None):
        customer.status = "under_review"
    await db.commit()
    await db.refresh(customer)

    effective_name = master_name or customer.company_name or customer.contact_email
    if master_is_active and master_sub:
        await notification_service.send_email(
            to_email=settings.ADMIN_EMAIL,
            subject=f"New Agent Brief (Returning Customer): {effective_name}",
            body=(
                f"Existing active customer {customer.contact_email} (Tier: {master_sub.plan}) has submitted a new agent brief "
                f"for their new workspace (Dograh Org ID: {customer.dograh_org_id}).\n\n"
                f"Brief:\n{customer.onboarding_form}\n\n"
                f"Please review at talkar.in/admin.\n"
                f"Note: Since they are a returning customer, you can set the Integration Fee to 0 to approve them instantly, or set a custom fee if this specific agent requires complex integration work."
            )
        )
    else:
        await notification_service.send_email(
            to_email=settings.ADMIN_EMAIL,
            subject=f"New Agent Brief Submitted: {effective_name}",
            body=(
                f"{effective_name} ({customer.contact_email}) has submitted a new agent brief "
                f"for workspace org {dograh_org_id}.\n\n"
                f"Brief:\n{customer.onboarding_form}\n\n"
                f"Review at talkar.in/admin"
            )
        )
    return customer

@router.post("/by-org/{dograh_org_id}/new-agent-request")
async def create_new_agent_request(
    dograh_org_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Called when a returning customer fills the brief form on a brand-new workspace
    that has no Talkar customer record yet. Creates a sub-org customer record
    linked to the master billing org and notifies admin.
    """
    master_customer_id = data.get("master_customer_id")
    if not master_customer_id:
        raise HTTPException(status_code=400, detail="master_customer_id is required")

    # Verify master exists
    master_res = await db.execute(select(Customer).where(Customer.id == master_customer_id))
    master = master_res.scalar_one_or_none()
    if not master:
        raise HTTPException(status_code=404, detail="Master customer not found")

    # Guard: don't create duplicates
    existing_res = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    existing = existing_res.scalar_one_or_none()
    if existing:
        return existing  # Idempotent

    # Security: Verify caller identity against master customer to prevent workspace hijacking
    caller_email = resolve_authenticated_email(authorization, x_talkar_email)
    if not caller_email:
        raise HTTPException(status_code=401, detail="Authentication required to create a new agent request.")
    master_email = master.contact_email.strip().lower() if master.contact_email else ""

    if caller_email != "service" and caller_email != master_email:
        logger.warning(
            f"Security: Workspace hijacking attempt blocked. Authenticated caller '{caller_email}' "
            f"attempted to link dograh_org_id={dograh_org_id} to master_customer_id={master.id} ('{master_email}')."
        )
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Caller is not authorized to link workspaces to this master customer account."
        )

    # Verify that the master customer is an owner/admin of this dograh_org_id in Dograh DB
    is_org_owner = await dograh_client.verify_dograh_org_user(
        org_id=dograh_org_id,
        user_id=master.dograh_user_id,
        email=master.contact_email,
        require_owner=True
    )
    if is_org_owner is False:
        logger.warning(
            f"Security: Master customer {master.id} ({master.contact_email}) is not an owner/admin of Dograh org {dograh_org_id}."
        )
        raise HTTPException(
            status_code=403,
            detail="Forbidden: Master customer is not an owner/admin of this organization."
        )

    if not caller_email and is_org_owner is not True:
        logger.warning(
            f"Security: Unverified request for dograh_org_id={dograh_org_id} to link to master_customer_id={master.id}."
        )
        raise HTTPException(
            status_code=401,
            detail="Authentication required: Unable to verify caller identity."
        )

    # Determine billing master (if master is itself a sub-org, use its master)
    billing_org_id = master.billing_org_id or master.id

    new_customer = Customer(
        company_name=master.company_name,
        industry=master.industry,
        contact_name=master.contact_name,
        contact_email=master.contact_email,
        contact_phone=master.contact_phone,
        status="under_review",  # Admin must review and build
        onboarding_form=data.get("form", {}),
        billing_org_id=billing_org_id,
        dograh_org_id=dograh_org_id,
        dograh_user_id=master.dograh_user_id,
    )
    db.add(new_customer)
    await db.commit()
    await db.refresh(new_customer)

    await notification_service.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"New Agent Request: {master.company_name}",
        body=(
            f"{master.company_name} ({master.contact_email}) has requested a new agent "
            f"for workspace org {dograh_org_id}.\n\n"
            f"Brief:\n{data.get('form', {})}\n\n"
            f"Review at talkar.in/admin"
        )
    )
    return new_customer

@router.get("/status")
async def get_customer_status(
    dograh_org_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    contact_email: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Called by Dograh UI middleware to gate account access.
    dograh_org_id or customer_id as query param.
    Unknown orgs return 404 so the UI can redirect new workspaces to /onboarding.
    contact_email fallback only used as last resort (no org_id available) and
    returns the customer with no dograh_org_id set (i.e. legacy single-org users).
    """
    customer = None
    if dograh_org_id:
        # Use updated_at DESC + limit(1) so that if duplicate records exist for the same
        # org (shouldn't happen but has occurred from test cleanup), we always return the
        # most recently updated one (e.g. the just-approved one, not the stale under_review).
        result = await db.execute(
            select(Customer)
            .where(Customer.dograh_org_id == dograh_org_id)
            .order_by(Customer.updated_at.desc())
            .limit(1)
        )
        customer = result.scalars().first()
    elif customer_id:
        result = await db.execute(select(Customer).where(Customer.id == customer_id))
        customer = result.scalar_one_or_none()
    elif contact_email:
        # Match by contact_email with deterministic priority (master first, then lowest ID)
        result = await db.execute(
            select(Customer)
            .where(Customer.contact_email == contact_email.strip())
            .order_by(Customer.billing_org_id.asc().nullsfirst(), Customer.id.asc())
            .limit(1)
        )
        customer = result.scalars().first()

    if not customer:
        raise HTTPException(status_code=404, detail="No Talkar account found for this workspace")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(customer, db, email_str, auth_str)

    resp: dict = {
        "status": customer.status, 
        "customer_id": customer.id,
        "dograh_org_id": customer.dograh_org_id,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "is_sub_org": bool(customer.billing_org_id),
        "account_type": "shared" if customer.billing_org_id else "independent",
        "billing_org_id": customer.billing_org_id,
        "has_onboarding_form": bool(customer.onboarding_form)
    }
    
    # Check provisioning state: MODEL_CONFIGURATION_V2 must exist in Dograh for the agent
    # to be usable. This is written by run_provisioning() — it happens at agent_building time
    # for the fee=0 fast path, or at mark_ready time otherwise.
    # Customers can be in 'active' or 'agent_building' but still have no config if provisioning
    # failed silently. is_provisioned tells the frontend whether voice selection is meaningful.
    is_provisioned = False
    if customer.dograh_org_id and customer.status in ("active", "agent_building", "pending_deposit", "suspended"):
        try:
            cfg = await dograh_client.get_org_config(customer.dograh_org_id, "MODEL_CONFIGURATION_V2")
            if cfg and "byok" in cfg and "pipeline" in cfg["byok"] and "tts" in cfg["byok"]["pipeline"]:
                is_provisioned = True
                resp["voice_id"] = cfg["byok"]["pipeline"]["tts"].get("voice")
        except Exception:
            pass
    resp["is_provisioned"] = is_provisioned

    # Resolve the customer's active plan — sub-orgs inherit from billing master
    billing_customer_id = customer.billing_org_id if customer.billing_org_id else customer.id
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == billing_customer_id))
    sub = sub_res.scalar_one_or_none()
    resp["plan"] = sub.plan if sub else None
    resp["master_plan"] = sub.plan if customer.billing_org_id and sub else None
    resp["custom_plan_label"] = getattr(sub, "custom_plan_label", None) if sub and sub.plan == "custom" else None

    if customer.status == "rejected" and customer.onboarding_form:
        form = customer.onboarding_form or {}
        resp["rejection_reason"] = form.get("rejection_reason")
        raw_days = form.get("reapply_countdown_days")
        try:
            countdown_days = int(raw_days) if raw_days is not None and str(raw_days).strip() != "" else 30
        except (ValueError, TypeError):
            countdown_days = 30

        rejected_at_str = form.get("rejected_at")
        ref_time = None
        if rejected_at_str:
            try:
                from datetime import datetime
                ref_time = datetime.fromisoformat(rejected_at_str)
            except Exception:
                pass
        if not ref_time and customer.updated_at:
            ref_time = customer.updated_at

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if ref_time:
            if ref_time.tzinfo is None:
                ref_time = ref_time.replace(tzinfo=timezone.utc)
            elapsed_days = max(0, (now - ref_time).days)
            remaining_days = max(0, countdown_days - elapsed_days)
        else:
            remaining_days = countdown_days

        resp["reapply_countdown_days"] = remaining_days
        resp["remaining_days"] = remaining_days
        resp["reapply_countdown"] = f"{remaining_days}d 0h"
    if customer.status == "info_requested" and customer.onboarding_form:
        form = customer.onboarding_form or {}
        resp["info_requested_message"] = form.get("info_requested_message")
        resp["info_requested_at"] = form.get("info_requested_at")
    if customer.onboarding_form:
        resp["onboarding_form"] = customer.onboarding_form
    if customer.status == "approved" and customer.setup_fee_order_id:
        resp["setup_fee_order_id"] = customer.setup_fee_order_id
    if customer.status == "suspended":
        from db.models import Wallet
        wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == billing_customer_id))
        wallet = wallet_res.scalar_one_or_none()
        if not wallet:
            from services.billing_service import get_billing_wallet
            wallet, _ = await get_billing_wallet(db, customer.id, allow_suspended=True)
        resp["balance_paise"] = wallet.balance_paise if wallet else 0
        form = customer.onboarding_form or {}
        susp_reason = form.get("suspension_reason")
        susp_msg = form.get("suspension_message")
        if not susp_reason and customer.billing_org_id:
            m_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
            m_cust = m_res.scalar_one_or_none()
            if m_cust and m_cust.onboarding_form:
                susp_reason = m_cust.onboarding_form.get("suspension_reason")
                if not susp_msg:
                    susp_msg = m_cust.onboarding_form.get("suspension_message")
        resp["suspension_reason"] = susp_reason or "zero_balance"
        if susp_msg:
            resp["suspension_message"] = susp_msg
    return resp


@router.get("/by-org/{dograh_org_id}")
async def get_customer_by_org(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    return customer

@router.get("/by-org/{dograh_org_id}/agents")
async def get_org_agents(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )
    
    from db.models import Agent
    from services import dograh_client
    
    # Sync from Dograh workflows
    existing_res = await db.execute(select(Agent).where(Agent.customer_id == customer.id))
    existing_agents = {a.dograh_workflow_id: a for a in existing_res.scalars().all() if a.dograh_workflow_id}
    
    try:
        workflows = await dograh_client.get_org_workflows(customer.dograh_org_id)
        for w in workflows:
            wf_id = w["id"] if isinstance(w, dict) else getattr(w, "id")
            wf_name = w["name"] if isinstance(w, dict) else getattr(w, "name")
            wf_status = w["status"] if isinstance(w, dict) else getattr(w, "status")
            wf_created = w.get("created_at") if isinstance(w, dict) else getattr(w, "created_at", None)
            if wf_id not in existing_agents:
                new_ag = Agent(
                    customer_id=customer.id,
                    name=wf_name,
                    dograh_workflow_id=wf_id,
                    dograh_org_id=customer.dograh_org_id,
                    status=wf_status,
                    built_at=wf_created
                )
                db.add(new_ag)
        await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to sync workflows for customer {customer.id}: {e}")

    # Re-fetch the now-synced agents
    agents_res = await db.execute(select(Agent).where(Agent.customer_id == customer.id))
    agents = agents_res.scalars().all()
    
    return [
        {
            "id": a.id,
            "dograh_agent_id": a.dograh_workflow_id,
            "name": a.name,
            "crm_link": getattr(a, "crm_link", None)
        }
        for a in agents if a.dograh_workflow_id
    ]

@router.get("/by-org/{dograh_org_id}/crm-links")
async def get_org_crm_links(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )
    
    links = getattr(customer, "crm_links", None)
    if links and isinstance(links, list) and len(links) > 0:
        cleaned = []
        for l in links:
            if isinstance(l, dict) and l.get("url"):
                raw_url = str(l.get("url", "")).strip()
                if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
                    raw_url = f"https://{raw_url}"
                cleaned.append({
                    "name": str(l.get("name") or "CRM").strip(),
                    "url": raw_url
                })
        if cleaned:
            return cleaned
    
    # Fallback to agent crm_link if customer-level list is empty
    from db.models import Agent
    agents_res = await db.execute(select(Agent).where(Agent.customer_id == customer.id))
    fallback_links = []
    for ag in agents_res.scalars().all():
        if ag.crm_link:
            raw_url = ag.crm_link.strip()
            if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
                raw_url = f"https://{raw_url}"
            fallback_links.append({
                "name": f"{ag.name} CRM",
                "url": raw_url
            })
    return fallback_links

class UpdateReportSettingsRequest(BaseModel):
    enabled: bool
    frequency: str = "weekly"  # "daily" | "weekly" | "monthly"
    recipients: List[str] = []

class SendTestReportRequest(BaseModel):
    frequency: Optional[str] = "weekly"
    recipients: Optional[List[str]] = None

@router.get("/by-org/{dograh_org_id}/report-settings")
async def get_org_report_settings(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )
    
    defaults = {
        "enabled": False,
        "frequency": "weekly",
        "recipients": [customer.contact_email] if customer.contact_email else [],
        "last_sent_at": None,
        "next_due_at": None
    }
    current_settings = getattr(customer, "report_settings", None) or {}
    return {**defaults, **current_settings}

@router.patch("/by-org/{dograh_org_id}/report-settings")
async def update_org_report_settings(
    dograh_org_id: int,
    data: UpdateReportSettingsRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    clean_recipients = [r.strip() for r in data.recipients if r.strip() and "@" in r]
    if not clean_recipients and customer.contact_email:
        clean_recipients = [customer.contact_email]

    freq = data.frequency.lower()
    if freq not in ("daily", "weekly", "monthly"):
        freq = "weekly"

    next_due = None
    if data.enabled:
        next_due = notification_service.calculate_next_report_due_date(freq).isoformat()

    updated = {
        "enabled": data.enabled,
        "frequency": freq,
        "recipients": clean_recipients,
        "last_sent_at": (customer.report_settings or {}).get("last_sent_at"),
        "next_due_at": next_due
    }
    customer.report_settings = updated
    await db.commit()
    return updated

@router.post("/by-org/{dograh_org_id}/send-test-report")
async def send_test_org_report(
    dograh_org_id: int,
    data: SendTestReportRequest = SendTestReportRequest(),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    clean_recipients = None
    if data.recipients:
        clean_recipients = [r.strip() for r in data.recipients if r.strip() and "@" in r]

    success = await notification_service.send_organization_report(
        db=db,
        customer=customer,
        frequency=data.frequency or "weekly",
        is_test=True,
        recipient_override=clean_recipients
    )
    if not success:
        raise HTTPException(status_code=500, detail="Failed to dispatch test report email")

    return {"status": "ok", "message": "Test report dispatched successfully"}

@router.post("/by-org/{dograh_org_id}/onboarding")
async def submit_onboarding_by_org(
    dograh_org_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    form_data = data.get("form", {})
    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None

    if not customer:
        # Blocker 6: Create customer on-the-fly if missing due to unsynced webhook
        contact_email = (
            form_data.get("email")
            or form_data.get("contactEmail")
            or form_data.get("contact_email")
            or email_str
            or f"org_{dograh_org_id}@talkar.in"
        )
        company_name = (
            form_data.get("businessName")
            or form_data.get("company_name")
            or f"Organization {dograh_org_id}"
        )

        # Resolve existing established master by contact email (Fix 20 / Issue 17)
        established_master_res = await db.execute(
            select(Customer)
            .where(Customer.contact_email == contact_email)
            .where(Customer.billing_org_id.is_(None))
            .where(Customer.status.in_(("active", "agent_building", "approved", "pending_deposit", "suspended")))
            .order_by(Customer.created_at.asc())
            .limit(1)
        )
        established_master = established_master_res.scalar_one_or_none()

        customer = Customer(
            dograh_org_id=dograh_org_id,
            company_name=company_name,
            contact_email=contact_email,
            contact_name=form_data.get("pocName") or form_data.get("contact_name") or "",
            contact_phone=form_data.get("pocPhone") or form_data.get("contact_phone") or "",
            industry=form_data.get("industry") or "",
            billing_org_id=established_master.id if established_master else None,
            status="under_review",
            onboarding_form=form_data,
            documents=data.get("documents", [])
        )
        if not isinstance(authorization, HeaderParam):
            await verify_customer_access(customer, db, email_str, auth_str)
        db.add(customer)
        await db.commit()
        await db.refresh(customer)
        await notification_service.send_email(
            to_email=settings.ADMIN_EMAIL,
            subject=f"New Application: {customer.company_name}",
            body=f"{customer.company_name} ({customer.contact_email}) submitted their onboarding form. Review at talkar.in/admin"
        )
        return customer

    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(customer, db, email_str, auth_str)

    if customer.status not in ("pending_approval", "info_requested", "rejected"):
        # Customer is already past the submission stage — silently succeed so the
        # frontend doesn't break, but DO NOT overwrite status or form data.
        # Critically: do NOT allow agent_building/approved/active to be reset to under_review.
        return customer

    # Blocker 15: If rejected, verify reapply countdown has elapsed or is 0
    if customer.status == "rejected":
        old_form = customer.onboarding_form or {}
        countdown_days = int(old_form.get("reapply_countdown_days", 0) or 0)
        if countdown_days > 0:
            rejected_at_str = old_form.get("rejected_at")
            ref_time = None
            if rejected_at_str:
                try:
                    from datetime import datetime
                    ref_time = datetime.fromisoformat(rejected_at_str)
                except Exception:
                    pass
            if not ref_time and customer.updated_at:
                ref_time = customer.updated_at

            if ref_time:
                from datetime import datetime, timezone, timedelta
                now = datetime.now(timezone.utc)
                if ref_time.tzinfo is None:
                    ref_time = ref_time.replace(tzinfo=timezone.utc)
                if now < ref_time + timedelta(days=countdown_days):
                    remaining_days = max(1, ((ref_time + timedelta(days=countdown_days)) - now).days)
                    raise HTTPException(
                        status_code=400,
                        detail=f"Reapplication is locked for {remaining_days} more day(s)."
                    )

    form_data = data.get("form", {})
    customer.onboarding_form = form_data
    if form_data.get("businessName"):
        customer.company_name = form_data.get("businessName")
    elif form_data.get("company_name"):
        customer.company_name = form_data.get("company_name")
    if form_data.get("industry"):
        customer.industry = form_data.get("industry")
    if form_data.get("pocName"):
        customer.contact_name = form_data.get("pocName")
    if form_data.get("pocPhone"):
        customer.contact_phone = form_data.get("pocPhone")
    customer.documents = data.get("documents", [])
    customer.status = "under_review"
    await db.commit()
    await db.refresh(customer)
    await notification_service.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"New Application: {customer.company_name}",
        body=f"{customer.company_name} ({customer.contact_email}) submitted their onboarding form. Review at talkar.in/admin"
    )
    return customer



@router.post("/{customer_id}/onboarding")
async def submit_onboarding_by_id(
    customer_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(customer, db, email_str, auth_str)

    if customer.status not in ("pending_approval", "info_requested", "rejected"):
        return customer # D-14: Silent success if already processed

    # Blocker 15: If rejected, verify reapply countdown has elapsed or is 0
    if customer.status == "rejected":
        old_form = customer.onboarding_form or {}
        countdown_days = int(old_form.get("reapply_countdown_days", 0) or 0)
        if countdown_days > 0:
            rejected_at_str = old_form.get("rejected_at")
            ref_time = None
            if rejected_at_str:
                try:
                    from datetime import datetime
                    ref_time = datetime.fromisoformat(rejected_at_str)
                except Exception:
                    pass
            if not ref_time and customer.updated_at:
                ref_time = customer.updated_at

            if ref_time:
                from datetime import datetime, timezone, timedelta
                now = datetime.now(timezone.utc)
                if ref_time.tzinfo is None:
                    ref_time = ref_time.replace(tzinfo=timezone.utc)
                if now < ref_time + timedelta(days=countdown_days):
                    remaining_days = max(1, ((ref_time + timedelta(days=countdown_days)) - now).days)
                    raise HTTPException(
                        status_code=400,
                        detail=f"Reapplication is locked for {remaining_days} more day(s)."
                    )
    form_data = data.get("form", {})
    customer.onboarding_form = form_data
    if form_data.get("businessName"):
        customer.company_name = form_data.get("businessName")
    elif form_data.get("company_name"):
        customer.company_name = form_data.get("company_name")
    if form_data.get("industry"):
        customer.industry = form_data.get("industry")
    if form_data.get("pocName"):
        customer.contact_name = form_data.get("pocName")
    if form_data.get("pocPhone"):
        customer.contact_phone = form_data.get("pocPhone")
    customer.documents = data.get("documents", [])
    customer.status = "under_review"
    await db.commit()
    await db.refresh(customer)
    await notification_service.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"New Application: {customer.company_name}",
        body=f"{customer.company_name} ({customer.contact_email}) submitted their onboarding form. Review at talkar.in/admin"
    )
    return customer
class SupportRequestPayload(BaseModel):
    type: str
    category: Optional[str] = None
    subject: str
    description: str
    priority: Optional[str] = "medium"
    agent_id: Optional[int] = None

@router.post("/support-requests")
async def create_support_request(
    data: SupportRequestPayload,
    dograh_org_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization,
    )

    from db.models import SupportRequest
    desc = data.description
    if data.category:
        desc = f"[{data.category.upper()}] {desc}"
    if data.priority and data.priority != "medium":
        desc = f"{desc}\n\nPriority: {data.priority.upper()}"

    req = SupportRequest(
        customer_id=customer.id,
        type=data.type,
        subject=data.subject,
        description=desc,
        agent_id=data.agent_id,
        status="open"
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    
    try:
        await notification_service.send_email(
            to_email="admin@talkar.ai",
            subject=f"[Talkar {data.type.replace('_', ' ').title()}] {data.subject} - {customer.company_name}",
            body=f"New {data.type} request from {customer.company_name} ({customer.contact_email}):\n\nSubject: {data.subject}\n\nDescription:\n{desc}"
        )
    except Exception as e:
        logger.warning(f"Failed to send support notification email: {e}")

    return {
        "status": "success",
        "id": req.id,
        "type": req.type,
        "subject": req.subject,
        "created_at": req.created_at.isoformat() if req.created_at else None
    }

@router.get("/support-requests")
async def get_support_requests(
    dograh_org_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization,
    )

    from db.models import SupportRequest, PhoneNumberRequest
    reqs = await db.execute(
        select(SupportRequest)
        .where(SupportRequest.customer_id == customer.id)
        .order_by(SupportRequest.created_at.desc())
    )
    items = reqs.scalars().all()

    phone_reqs = await db.execute(
        select(PhoneNumberRequest)
        .where(PhoneNumberRequest.customer_id == customer.id)
        .order_by(PhoneNumberRequest.requested_at.desc())
    )
    phone_items = phone_reqs.scalars().all()

    combined = []
    for r in items:
        combined.append({
            "id": r.id,
            "type": r.type,
            "subject": r.subject,
            "description": r.description,
            "status": r.status,
            "admin_note": r.admin_note,
            "resolved_by": r.resolved_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        })
        
    for p in phone_items:
        # Map 'denied' to 'rejected' for UI badge compatibility across existing and new dashboard versions
        st = "rejected" if p.status == "denied" else p.status
        combined.append({
            "id": p.id,
            "type": "phone_number_request",
            "subject": f"Phone Number Request ({p.quantity}x {p.region})",
            "description": p.use_case,
            "status": st,
            "admin_note": p.admin_note,
            "resolved_by": getattr(p, "resolved_by", None),
            "created_at": p.requested_at.isoformat() if p.requested_at else None,
            "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
        })
        
    combined.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return combined


# Parameterized paths after static ones

@router.get("/{customer_id}")
async def get_customer(
    customer_id: int,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


class AssignPhoneNumberRequest(BaseModel):
    number: str
    plivo_number_id: Optional[str] = None
    monthly_cost_paise: Optional[int] = None


@router.post("/{customer_id}/assign-phone-number")
async def assign_customer_phone_number(
    customer_id: int,
    data: AssignPhoneNumberRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: TalkarAdmin = Depends(get_current_admin),
):
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    from config import DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE
    monthly_cost = data.monthly_cost_paise if (data.monthly_cost_paise is not None and data.monthly_cost_paise > 0) else DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE

    from db.models import PhoneNumber
    pn = PhoneNumber(
        customer_id=customer_id,
        number=data.number,
        plivo_number_id=data.plivo_number_id,
        monthly_cost_paise=monthly_cost,
    )
    db.add(pn)
    await db.commit()

    if customer.dograh_org_id:
        try:
            from db.models import Agent
            agent_res = await db.execute(
                select(Agent.dograh_workflow_id)
                .where(Agent.customer_id == customer_id, Agent.dograh_workflow_id.isnot(None))
                .order_by(Agent.id.desc())
                .limit(1)
            )
            wf_id = agent_res.scalar_one_or_none()
            await dograh_client.sync_phone_number_to_dograh(customer.dograh_org_id, data.number, workflow_id=wf_id)
        except Exception as e:
            logger.error(f"Failed to sync phone number {data.number} to Dograh org {customer.dograh_org_id}: {e}")

    try:
        from services.billing_service import charge_initial_phone_number_lease
        await charge_initial_phone_number_lease(db, customer_id, pn)
    except Exception as e:
        logger.warning(f"Initial lease charge bypassed or failed for phone {pn.number}: {e}")

    return {"status": "assigned"}


@router.delete("/by-org/{org_id}/phone-numbers/{phone_number_id}")
async def release_org_phone_number(
    org_id: int,
    phone_number_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    from db.models import PhoneNumber
    pn_res = await db.execute(
        select(PhoneNumber).where(PhoneNumber.id == phone_number_id, PhoneNumber.customer_id == customer.id)
    )
    pn = pn_res.scalar_one_or_none()
    if not pn:
        raise HTTPException(404, "Phone number not found")

    pn.status = "released"
    await db.commit()

    if customer.dograh_org_id and pn.number:
        try:
            await dograh_client.release_phone_number_from_dograh(customer.dograh_org_id, pn.number)
        except Exception as e:
            logger.warning(f"Failed to deactivate released phone number {pn.number} in Dograh for customer {customer.id}: {e}")

    return {"status": "released"}






class SelectTierRequest(BaseModel):
    tier: str  # "starter" | "pro" | "elite"

@router.post("/by-org/{org_id}/select-tier")
async def select_tier(
    org_id: int,
    data: SelectTierRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    from config import TIER_CONFIG, CALL_BLOCK_THRESHOLD_PAISE
    
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    if data.tier == "custom":
        raise HTTPException(400, "Cannot self-select a custom plan.")
    tier_cfg = TIER_CONFIG.get(data.tier)
    if not tier_cfg: raise HTTPException(400, "Invalid tier")
    if tier_cfg.get("disabled"): raise HTTPException(400, f"The {data.tier} plan is not currently available.")

    ALLOWED_PLAN_SELECTION_STATUSES = ("pending_deposit", "approved", "agent_building", "under_review", "pending_approval", "pending_plan_selection")
    if customer.status not in ALLOWED_PLAN_SELECTION_STATUSES:
        raise HTTPException(400, f"Cannot select plan in current status: {customer.status}")

    # Sub-Org Tier Ceiling Check (Issue 9 Protection):
    # A sub-organization cannot independently select a tier that exceeds the master organization's plan.
    # Allowing a sub-org on a Starter master to pick Pro (ElevenLabs voices) while Master is billed at Starter (₹6/min)
    # creates direct negative gross margins.
    if customer.billing_org_id:
        master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.billing_org_id))
        master_sub = master_sub_res.scalar_one_or_none()
        master_tier = master_sub.plan if master_sub else "starter"

        TIER_RANK = {"starter": 1, "growth": 1, "pro": 2, "elite": 3, "custom": 10}
        req_rank = TIER_RANK.get(data.tier, 1)
        master_rank = TIER_RANK.get(master_tier, 1)

        if req_rank > master_rank:
            raise HTTPException(
                status_code=400,
                detail=f"Sub-organization plan '{data.tier}' cannot exceed master organization plan '{master_tier}'. Please upgrade the master organization account to {data.tier} first."
            )

    activation_min = tier_cfg.get("activation_deposit_paise", 600000)

    # Store selected tier (even if switching plans during pending_deposit)
    existing_form = customer.onboarding_form or {}
    existing_form["approved_tier"] = data.tier
    customer.onboarding_form = dict(existing_form)

    # Check wallet right now
    from services.billing_service import get_billing_wallet
    wallet, _ = await get_billing_wallet(db, customer.id)
    current_balance = wallet.balance_paise if wallet else 0

    if current_balance >= activation_min:
        # Wallet already funded — activate immediately (handles pre-funded wallets)
        sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        sub = sub_res.scalar_one_or_none()
        if sub:
            sub.plan = data.tier
            sub.per_minute_rate_paise = tier_cfg["per_minute_rate_paise"]
        customer.status = "active"
        await db.commit()
        from services.provisioning_service import run_provisioning
        await run_provisioning(customer.id, data.tier, db=None)
        await notification_service.send_email(
            to_email=customer.contact_email,
            subject="Your Talkar Agent is Live!",
            body=f"Hi {customer.contact_name}, your AI agent is fully active on the {data.tier} tier!"
        )
        return {"status": "active"}
    else:
        # Not funded enough — move to pending_deposit, redirect to wallet
        customer.status = "pending_deposit"
        await db.commit()
        shortfall = (activation_min - current_balance) // 100
        return {
            "status": "pending_deposit",
            "redirect": f"/wallet?activation=true&plan={data.tier}",
            "activation_deposit_rupees": activation_min // 100,
            "shortfall_rupees": shortfall,
            "message": f"Add ₹{shortfall} more to activate your {data.tier} plan."
        }

class TierUpgradeRequest(BaseModel):
    requested_tier: str

@router.post("/by-org/{org_id}/request-tier-upgrade")
async def request_tier_upgrade(
    org_id: int,
    data: TierUpgradeRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    if data.requested_tier == "custom":
        raise HTTPException(400, "Cannot self-assign a custom plan.")
        
    from config import TIER_CONFIG
    
    if data.requested_tier not in TIER_CONFIG:
        raise HTTPException(400, f"Invalid tier: {data.requested_tier}")
        
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )
    
    if customer.status != "active":
        raise HTTPException(400, f"Customer must be active to upgrade tier (current status: {customer.status})")

    # Blocker 23: Enforce Master Agency Ownership on request_tier_upgrade
    if customer.billing_org_id is not None:
        raise HTTPException(403, "Sub-organizations cannot change agency-wide billing plans. Only the Master Organization owner can upgrade or modify tiers.")
    
    master_id = customer.billing_org_id if customer.billing_org_id else customer.id
    master_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == master_id))
    master_sub = master_sub_res.scalar_one_or_none()
    if master_sub and master_sub.plan == "custom":
        raise HTTPException(400, "Your plan is custom-configured. Contact Talkar to modify it.")
    current_tier = master_sub.plan if master_sub else "starter"
    
    if current_tier == data.requested_tier:
        raise HTTPException(400, f"Already on {data.requested_tier} tier")

    # Fix 7: Enforce balance check on request_tier_upgrade
    from db.models import Wallet
    wallet_res = await db.execute(select(Wallet).where(Wallet.customer_id == master_id))
    wallet = wallet_res.scalar_one_or_none() if wallet_res else None
    wallet_balance = getattr(wallet, "balance_paise", 0) if wallet else 0
    if not isinstance(wallet_balance, (int, float)):
        wallet_balance = 0
    activation_min = TIER_CONFIG[data.requested_tier].get("activation_deposit_paise", 0)
    if wallet_balance < activation_min:
        shortfall = (activation_min - int(wallet_balance)) // 100
        raise HTTPException(400, f"Insufficient balance to upgrade to {data.requested_tier}. Please add ₹{shortfall} more.")
        
    # Instantly update master subscription
    if master_sub:
        master_sub.plan = data.requested_tier
        master_sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
    else:
        master_sub = Subscription(
            customer_id=master_id,
            plan=data.requested_tier,
            per_minute_rate_paise=TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"],
            status="active",
        )
        db.add(master_sub)
    
    # Also update caller customer's subscription if separate
    if customer.id != master_id:
        sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        sub = sub_res.scalar_one_or_none()
        if sub:
            sub.plan = data.requested_tier
            sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
        else:
            sub = Subscription(
                customer_id=customer.id,
                plan=data.requested_tier,
                per_minute_rate_paise=TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"],
                status="active",
            )
            db.add(sub)

    existing_form = customer.onboarding_form or {}
    # Apply the new tier
    existing_form["approved_tier"] = data.requested_tier
    # Clear any old upgrade requests
    existing_form.pop("tier_upgrade_requested", None)
    existing_form.pop("tier_upgrade_requested_at", None)
    customer.onboarding_form = dict(existing_form) 
    
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(customer, "onboarding_form")

    if customer.id != master_id:
        master_res = await db.execute(select(Customer).where(Customer.id == master_id))
        master = master_res.scalar_one_or_none()
        if master:
            m_form = master.onboarding_form or {}
            m_form["approved_tier"] = data.requested_tier
            master.onboarding_form = dict(m_form)
            flag_modified(master, "onboarding_form")

    await db.commit()
    
    # Run best-effort synchronous provisioning to sync to Dograh
    from services.provisioning_service import run_provisioning
    await run_provisioning(master_id, data.requested_tier, db)
    if customer.id != master_id:
        await run_provisioning(customer.id, data.requested_tier, db)
    
    # Cascade to all sub-orgs under master
    sub_orgs_res = await db.execute(select(Customer).where(Customer.billing_org_id == master_id))
    for sub_org in sub_orgs_res.scalars().all():
        if sub_org.id == customer.id:
            continue
        sub_sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == sub_org.id))
        sub_sub = sub_sub_res.scalar_one_or_none()
        if sub_sub:
            sub_sub.plan = data.requested_tier
            sub_sub.per_minute_rate_paise = TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"]
        else:
            sub_sub = Subscription(
                customer_id=sub_org.id,
                plan=data.requested_tier,
                per_minute_rate_paise=TIER_CONFIG[data.requested_tier]["per_minute_rate_paise"],
                status="active",
            )
            db.add(sub_sub)
            
        sub_form = sub_org.onboarding_form or {}
        sub_form["approved_tier"] = data.requested_tier
        sub_org.onboarding_form = dict(sub_form)
        flag_modified(sub_org, "onboarding_form")
        
        await db.commit()
        if sub_org.dograh_org_id:
            try:
                await run_provisioning(sub_org.id, data.requested_tier, db)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to cascade provisioning to sub-org {sub_org.id}: {e}")

    from services.billing_service import sync_wallet_block_policy
    try:
        await sync_wallet_block_policy(db, master_id)
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to sync wallet block policy after tier upgrade for {master_id}: {e}")
    
    # Notify customer of instant upgrade/downgrade
    await notification_service.send_email(
        to_email=customer.contact_email,
        subject=f"Your Talkar Tier has been updated to {data.requested_tier.title()}",
        body=f"Hi {customer.contact_name},\n\nYour tier has been instantly updated from {current_tier} to {data.requested_tier}. Your agent's concurrency limits and pricing have been adjusted automatically.\n\nThank you for using Talkar!"
    )
    return {"status": "success", "new_tier": data.requested_tier}

class PhoneNumberRequestBody(BaseModel):
    quantity: int
    region: str
    use_case: str

@router.post("/by-org/{org_id}/request-phone-numbers")
async def request_phone_numbers(
    org_id: int,
    data: PhoneNumberRequestBody,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    if customer.status != "active": raise HTTPException(400, "Customer not active")
    
    from db.models import PhoneNumberRequest
    # Check for existing pending request
    existing = await db.execute(
        select(PhoneNumberRequest).where(
            PhoneNumberRequest.customer_id == customer.id,
            PhoneNumberRequest.status == "pending"
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "You already have a pending phone number request")
        
    req = PhoneNumberRequest(
        customer_id=customer.id,
        quantity=data.quantity,
        region=data.region,
        use_case=data.use_case
    )
    db.add(req)
    await db.commit()
    
    await notification_service.send_email(
        to_email=settings.ADMIN_EMAIL,
        subject=f"[Talkar] Phone Number Request — {customer.company_name}",
        body=f"{customer.company_name} requested {data.quantity} numbers in {data.region}. Use Case: {data.use_case}."
    )
    return {"status": "request_submitted"}


class UpdateVoiceRequest(BaseModel):
    voice_id: str
    provider: str


# ---------------------------------------------------------------------------
# Customer self-serve: profile update
# ---------------------------------------------------------------------------

class UpdateProfileRequest(BaseModel):
    company_name: str
    contact_name: str
    contact_phone: Optional[str] = None

@router.patch("/by-org/{dograh_org_id}/profile")
async def update_customer_profile(
    dograh_org_id: int,
    data: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Allows an active customer to update their own company name, contact name,
    and contact phone.  Email is intentionally NOT editable — it is the auth
    identity used for login and all notification delivery.

    Blocked for customers still in pre-activation statuses to avoid overwriting
    data that the admin is currently reviewing.
    """
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    # Guard: don't allow edits while the admin is reviewing the application.
    LOCKED_STATUSES = {"pending_approval", "under_review", "info_requested", "rejected"}
    if customer.status in LOCKED_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Profile cannot be updated while account is in '{customer.status}' status."
        )

    # Validate inputs
    company = data.company_name.strip()
    name = data.contact_name.strip()
    phone = (data.contact_phone or "").strip() or None

    if len(company) < 2:
        raise HTTPException(status_code=422, detail="Company name must be at least 2 characters.")
    if len(name) < 2:
        raise HTTPException(status_code=422, detail="Contact name must be at least 2 characters.")
    if phone:
        import re
        # Accept E.164 (+91XXXXXXXXXX) or a bare 10-digit number
        if not re.match(r"^\+?\d{10,15}$", phone):
            raise HTTPException(status_code=422, detail="Phone must be a valid number (e.g. +919876543210 or 9876543210).")

    customer.company_name = company
    customer.contact_name = name
    customer.contact_phone = phone
    await db.commit()
    await db.refresh(customer)
    return {
        "status": "ok",
        "company_name": customer.company_name,
        "contact_name": customer.contact_name,
        "contact_phone": customer.contact_phone,
        "contact_email": customer.contact_email,  # returned read-only for UI display
    }


# ---------------------------------------------------------------------------
# Customer self-serve: notification preferences
# ---------------------------------------------------------------------------

NOTIF_DEFAULT_THRESHOLD_PAISE = 150000   # ₹1,500 — matches cron default
NOTIF_MIN_THRESHOLD_PAISE = 50000        # ₹500  — CALL_BLOCK_THRESHOLD
NOTIF_MAX_THRESHOLD_PAISE = 5000000      # ₹50,000

class UpdateNotificationSettingsRequest(BaseModel):
    low_balance_alert_paise: Optional[int] = None
    cc_email: Optional[str] = None          # empty string = clear CC
    email_notifications_enabled: Optional[bool] = None

@router.get("/by-org/{dograh_org_id}/notification-settings")
async def get_notification_settings(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    rep = customer.report_settings or {}
    return {
        "low_balance_alert_paise": rep.get("low_balance_alert_paise", NOTIF_DEFAULT_THRESHOLD_PAISE),
        "cc_email": rep.get("cc_email") or None,
        "email_notifications_enabled": rep.get("email_notifications_enabled", True),
    }

@router.patch("/by-org/{dograh_org_id}/notification-settings")
async def update_notification_settings(
    dograh_org_id: int,
    data: UpdateNotificationSettingsRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """
    Merges notification preferences into the existing report_settings JSON.
    Preserves all other keys (enabled, frequency, recipients, profitability_overrides, etc.).
    """
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found for this org")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )

    # Validate low balance threshold
    if data.low_balance_alert_paise is not None:
        if data.low_balance_alert_paise < NOTIF_MIN_THRESHOLD_PAISE:
            raise HTTPException(
                status_code=422,
                detail=f"Threshold cannot be below ₹{NOTIF_MIN_THRESHOLD_PAISE // 100} (calls are paused below this level)."
            )
        if data.low_balance_alert_paise > NOTIF_MAX_THRESHOLD_PAISE:
            raise HTTPException(
                status_code=422,
                detail=f"Threshold cannot exceed ₹{NOTIF_MAX_THRESHOLD_PAISE // 100}."
            )

    # Validate CC email if provided (empty string = intentional clear)
    cc_email_value = None  # sentinel: not in payload
    if data.cc_email is not None:
        cc = data.cc_email.strip()
        if cc == "":
            cc_email_value = ""  # explicit clear
        else:
            import re
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cc):
                raise HTTPException(status_code=422, detail="CC email is not a valid email address.")
            if customer.contact_email and cc.lower() == customer.contact_email.lower():
                raise HTTPException(status_code=422, detail="CC email cannot be the same as your primary email.")
            cc_email_value = cc

    # Merge — read-modify-write the JSON to avoid clobbering other keys
    from sqlalchemy.orm.attributes import flag_modified
    current = dict(customer.report_settings or {})

    if data.low_balance_alert_paise is not None:
        current["low_balance_alert_paise"] = data.low_balance_alert_paise
    if cc_email_value is not None:
        current["cc_email"] = cc_email_value if cc_email_value else None
    if data.email_notifications_enabled is not None:
        current["email_notifications_enabled"] = data.email_notifications_enabled

    customer.report_settings = current
    flag_modified(customer, "report_settings")
    await db.commit()

    return {
        "status": "ok",
        "low_balance_alert_paise": current.get("low_balance_alert_paise", NOTIF_DEFAULT_THRESHOLD_PAISE),
        "cc_email": current.get("cc_email") or None,
        "email_notifications_enabled": current.get("email_notifications_enabled", True),
    }


@router.patch("/by-org/{org_id}/voice")
async def update_customer_voice(
    org_id: int,
    data: UpdateVoiceRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == org_id))
    customer = result.scalar_one_or_none()
    if not customer: raise HTTPException(404, "Customer not found")

    auth_str = authorization if isinstance(authorization, str) else None
    email_str = x_talkar_email if isinstance(x_talkar_email, str) else None
    if not isinstance(authorization, HeaderParam):
        await verify_customer_access(
            customer=customer,
            db=db,
            x_talkar_email=email_str,
            authorization=auth_str,
        )
    
    if customer.status not in ("active", "agent_building"):
        raise HTTPException(400, "Customer must be active to change voice")

    # Fetch effective subscription to check tier limits (check master if sub-org to prevent voice/tier desync)
    effective_customer_id = customer.billing_org_id if customer.billing_org_id else customer.id
    sub_res = await db.execute(select(Subscription).where(Subscription.customer_id == effective_customer_id))
    sub = sub_res.scalar_one_or_none()
    if not sub and customer.billing_org_id:
        sub_own_res = await db.execute(select(Subscription).where(Subscription.customer_id == customer.id))
        sub = sub_own_res.scalar_one_or_none()

    tier = sub.plan if sub else "starter"
    from config import resolve_tier_config
    tier_cfg = resolve_tier_config(sub)

    form = customer.onboarding_form or {}
    sub_tier = form.get("approved_tier")
    if sub_tier:
        tier = sub_tier
        if sub_tier == "custom" and sub and sub.plan == "custom":
            tier_cfg = resolve_tier_config(sub)
        else:
            tier_cfg = resolve_tier_config(sub_tier)
    elif not sub:
        tier = form.get("approved_plan", "starter")
        tier_cfg = resolve_tier_config(tier)

    def normalize_provider(p: str) -> str:
        if not p:
            return ""
        p = p.lower().strip()
        if p in ("smallest", "smallest_ai"):
            return "smallest_ai"
        return p

    allowed_provider = tier_cfg.get("tts_provider", "deepgram")
    allowed_providers = tier_cfg.get("allowed_tts_providers")
    if isinstance(allowed_providers, (list, tuple, set)):
        allowed_set = {normalize_provider(p) for p in allowed_providers}
    else:
        allowed_set = {normalize_provider(allowed_provider)}
    allowed_set.add(normalize_provider(allowed_provider))

    if normalize_provider(data.provider) not in allowed_set:
        raise HTTPException(
            403,
            f"Your current tier ({tier}) only supports {allowed_provider} voices. Please upgrade to use {data.provider}."
        )

    # Fetch current model config from Dograh
    cfg = await dograh_client.get_org_config(org_id, "MODEL_CONFIGURATION_V2")
    if not cfg:
        # Provisioning hasn't run yet (or failed). This is not a server error — it's
        # a business state issue. Return 409 so the client can show a sensible message.
        raise HTTPException(
            409,
            "Your agent hasn't been provisioned yet. Voice selection will be available once your agent goes live."
        )
    
    tts_cfg = cfg.get("byok", {}).get("pipeline", {}).get("tts")
    if not tts_cfg:
        raise HTTPException(409, "Agent voice configuration is missing. Please contact support.")

    # Map frontend provider names → canonical provider strings that Dograh/pipecat
    # actually recognises.  provisioning_service.py writes "smallest" (not "smallest_ai"),
    # so we must normalise here before writing back to avoid Dograh falling back to ElevenLabs.
    DOGRAH_PROVIDER_MAP: dict[str, str] = {
        "smallest_ai": "smallest",
        "elevenlabs": "elevenlabs",
        "deepgram": "deepgram",
    }
    dograh_provider = DOGRAH_PROVIDER_MAP.get(data.provider, data.provider)

    tts_cfg["voice"] = data.voice_id
    tts_cfg["provider"] = dograh_provider
    if dograh_provider == "elevenlabs" and settings.TALKAR_ELEVENLABS_KEY:
        tts_cfg["api_key"] = settings.TALKAR_ELEVENLABS_KEY
        if not tts_cfg.get("model") or "aura" in tts_cfg.get("model", "") or "lightning" in tts_cfg.get("model", ""):
            tts_cfg["model"] = "eleven_flash_v2_5"
    elif dograh_provider == "smallest" and settings.TALKAR_SMALLEST_AI_KEY:
        tts_cfg["api_key"] = settings.TALKAR_SMALLEST_AI_KEY
        if not tts_cfg.get("model") or "aura" in tts_cfg.get("model", "") or "eleven" in tts_cfg.get("model", ""):
            tts_cfg["model"] = "lightning_v3.1_pro"
    elif dograh_provider == "deepgram" and settings.TALKAR_DEEPGRAM_KEY:
        tts_cfg["api_key"] = settings.TALKAR_DEEPGRAM_KEY
        if not tts_cfg.get("model") or "eleven" in tts_cfg.get("model", "") or "lightning" in tts_cfg.get("model", ""):
            tts_cfg["model"] = "aura-2-thalia-en"

    cfg["byok"]["pipeline"]["tts"] = tts_cfg

    await dograh_client.upsert_org_config(org_id, "MODEL_CONFIGURATION_V2", cfg)
    
    return {"status": "success", "voice_id": data.voice_id, "provider": dograh_provider}


# ---------------------------------------------------------------------------
# Sub-Org Spending Limits & Concurrency Quotas (Issue 5 Protection)
# ---------------------------------------------------------------------------

class SubOrgSpendingLimitsRequest(BaseModel):
    monthly_spending_cap_paise: Optional[int] = None
    concurrent_call_limit: Optional[int] = None

@router.get("/by-org/{dograh_org_id}/spending-limits")
async def get_suborg_spending_limits(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None),
):
    """
    Get spending limits and current month spend for a sub-organization.
    Accessible by Master owner, Sub-Org owner, or Admin/Service.
    """
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    await verify_customer_access(customer, db, x_talkar_email, authorization)

    form = customer.onboarding_form or {}
    cap = form.get("monthly_spending_cap_paise")
    concurrent_limit = form.get("concurrent_call_limit")

    from services.billing_service import get_customer_month_spend
    month_spend = await get_customer_month_spend(db, customer.id)
    is_blocked = bool(cap is not None and int(cap) > 0 and month_spend >= int(cap))

    return {
        "dograh_org_id": customer.dograh_org_id,
        "customer_id": customer.id,
        "billing_org_id": customer.billing_org_id,
        "monthly_spending_cap_paise": cap,
        "concurrent_call_limit": concurrent_limit,
        "current_month_spend_paise": month_spend,
        "is_blocked_by_cap": is_blocked,
    }

@router.patch("/by-org/{dograh_org_id}/spending-limits")
async def update_suborg_spending_limits(
    dograh_org_id: int,
    data: SubOrgSpendingLimitsRequest,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None),
):
    """
    Configure monthly spending caps and concurrency limits for a sub-organization.
    Restricted to Master Organization owners (and Talkar Admin/Service) to prevent
    rogue sub-orgs from modifying their own caps and starving the fleet.
    """
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not customer.billing_org_id:
        raise HTTPException(
            status_code=400,
            detail="Spending limits can only be configured for sub-organizations."
        )

    # Privileged action check: only master owner or admin/service can modify limits
    if not is_admin_or_service_request(authorization, x_talkar_email):
        caller_email = resolve_authenticated_email(authorization, x_talkar_email)
        if not caller_email:
            raise HTTPException(status_code=401, detail="Authentication required.")
        if caller_email != "service":
            master_res = await db.execute(select(Customer).where(Customer.id == customer.billing_org_id))
            master = master_res.scalar_one_or_none()
            is_master_owner = False
            if master:
                if master.contact_email and master.contact_email.strip().lower() == caller_email:
                    is_master_owner = True
                elif master.dograh_org_id:
                    is_master_owner = bool(await dograh_client.verify_dograh_org_user(
                        org_id=master.dograh_org_id,
                        email=caller_email,
                        require_owner=True
                    ))
            if not is_master_owner:
                raise HTTPException(
                    status_code=403,
                    detail="Sub-organization spending limits can only be managed by the Master Organization owner."
                )

    if data.monthly_spending_cap_paise is not None and data.monthly_spending_cap_paise < 0:
        raise HTTPException(status_code=422, detail="monthly_spending_cap_paise cannot be negative.")
    if data.concurrent_call_limit is not None and data.concurrent_call_limit < 1:
        raise HTTPException(status_code=422, detail="concurrent_call_limit must be at least 1.")

    from sqlalchemy.orm.attributes import flag_modified
    form = dict(customer.onboarding_form or {})
    if data.monthly_spending_cap_paise is not None:
        form["monthly_spending_cap_paise"] = data.monthly_spending_cap_paise
    if data.concurrent_call_limit is not None:
        form["concurrent_call_limit"] = data.concurrent_call_limit

    customer.onboarding_form = form
    flag_modified(customer, "onboarding_form")
    await db.commit()
    await db.refresh(customer)

    # Immediately synchronize Dograh call block/limits according to the new settings
    from services.billing_service import sync_wallet_block_policy, get_customer_month_spend
    await sync_wallet_block_policy(db, customer.id)

    month_spend = await get_customer_month_spend(db, customer.id)
    cap = form.get("monthly_spending_cap_paise")
    is_blocked = bool(cap is not None and int(cap) > 0 and month_spend >= int(cap))

    return {
        "status": "ok",
        "dograh_org_id": customer.dograh_org_id,
        "customer_id": customer.id,
        "billing_org_id": customer.billing_org_id,
        "monthly_spending_cap_paise": form.get("monthly_spending_cap_paise"),
        "concurrent_call_limit": form.get("concurrent_call_limit"),
        "current_month_spend_paise": month_spend,
        "is_blocked_by_cap": is_blocked,
    }


@router.get("/by-org/{dograh_org_id}/sub-orgs")
async def get_master_sub_orgs(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None),
):
    """
    List all sub-organizations connected to the master customer,
    including their monthly spending caps, concurrency limits, and current month spend.
    """
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    await verify_customer_access(customer, db, x_talkar_email, authorization)

    # If this customer itself is a sub-org, return empty list (sub-orgs don't have sub-orgs)
    if customer.billing_org_id:
        return {"sub_orgs": []}

    sub_res = await db.execute(
        select(Customer)
        .where(Customer.billing_org_id == customer.id)
        .order_by(Customer.company_name.asc())
    )
    sub_customers = sub_res.scalars().all()

    from services.billing_service import get_customer_month_spend

    items = []
    for sub in sub_customers:
        form = sub.onboarding_form or {}
        cap = form.get("monthly_spending_cap_paise")
        concurrent_limit = form.get("concurrent_call_limit")
        month_spend = await get_customer_month_spend(db, sub.id)
        is_blocked = bool(cap is not None and int(cap) > 0 and month_spend >= int(cap))

        items.append({
            "id": sub.id,
            "company_name": sub.company_name or f"Sub-Org #{sub.dograh_org_id}",
            "contact_name": sub.contact_name,
            "contact_email": sub.contact_email,
            "dograh_org_id": sub.dograh_org_id,
            "status": sub.status,
            "monthly_spending_cap_paise": cap,
            "concurrent_call_limit": concurrent_limit,
            "current_month_spend_paise": month_spend,
            "is_blocked_by_cap": is_blocked,
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
        })

    return {"sub_orgs": items}
