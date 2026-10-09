from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from db.session import get_db
from db.models import Customer, Invoice
from services.customer_auth import verify_customer_access
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/by-org/{dograh_org_id}")
async def get_invoices_by_org(
    dograh_org_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    result = await db.execute(select(Customer).where(Customer.dograh_org_id == dograh_org_id))
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization
    )
        
    master_customer_id = customer.billing_org_id if customer.billing_org_id else customer.id

    invoices_res = await db.execute(
        select(Invoice).where(Invoice.customer_id == master_customer_id).order_by(Invoice.created_at.desc())
    )
    invoices = invoices_res.scalars().all()
    
    return {
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "amount_paise": inv.amount_paise,
                "status": inv.status,
                "pdf_url": inv.pdf_url,
                "created_at": inv.created_at,
                "wallet_transaction_id": inv.wallet_transaction_id
            } for inv in invoices
        ]
    }

@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: int,
    db: AsyncSession = Depends(get_db),
    x_talkar_email: Optional[str] = Header(None, alias="X-Talkar-Email"),
    authorization: Optional[str] = Header(None)
):
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(404, "Invoice not found")
        
    customer_res = await db.execute(select(Customer).where(Customer.id == invoice.customer_id))
    customer = customer_res.scalar_one_or_none()
    if not customer:
        raise HTTPException(404, "Customer not found")

    await verify_customer_access(
        customer=customer,
        db=db,
        x_talkar_email=x_talkar_email,
        authorization=authorization
    )
    
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "amount_paise": invoice.amount_paise,
        "status": invoice.status,
        "created_at": invoice.created_at,
        "customer": {
            "company_name": customer.company_name,
            "contact_email": customer.contact_email,
            "contact_name": customer.contact_name
        }
    }

