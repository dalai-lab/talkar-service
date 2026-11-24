from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
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
        
    # Blocker 31: Include Sub-Org Attributed Invoices in Master Agency Query
    if customer.billing_org_id is None:
        sub_ids_query = select(Customer.id).where(Customer.billing_org_id == customer.id)
        invoices_res = await db.execute(
            select(Invoice).where(
                or_(Invoice.customer_id == customer.id, Invoice.customer_id.in_(sub_ids_query))
            ).order_by(Invoice.created_at.desc())
        )
    else:
        # Sub-orgs only see invoices issued directly to their customer_id, never master agency invoices
        invoices_res = await db.execute(
            select(Invoice).where(Invoice.customer_id == customer.id).order_by(Invoice.created_at.desc())
        )
    invoices = invoices_res.scalars().all()
    
    return {
        "invoices": [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "amount_paise": inv.total_paise or inv.amount_paise,
                "subtotal_paise": inv.subtotal_paise or int(round(inv.amount_paise / 1.18)),
                "tax_paise": inv.tax_paise or (inv.amount_paise - int(round(inv.amount_paise / 1.18))),
                "total_paise": inv.total_paise or inv.amount_paise,
                "invoice_type": getattr(inv, "invoice_type", "tax_invoice") or "tax_invoice",
                "status": inv.status,
                "pdf_url": inv.pdf_url,
                "created_at": inv.created_at,
                "wallet_transaction_id": inv.wallet_transaction_id,
                "sub_org_id": getattr(inv, "sub_org_id", None),
                "dograh_org_id": getattr(inv, "dograh_org_id", None),
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
    
    onboarding = customer.onboarding_form or {}
    from config import settings

    subtotal_paise = invoice.subtotal_paise if invoice.subtotal_paise is not None else int(round(invoice.amount_paise / 1.18))
    tax_paise = invoice.tax_paise if invoice.tax_paise is not None else (invoice.amount_paise - subtotal_paise)
    total_paise = invoice.total_paise if invoice.total_paise is not None else invoice.amount_paise
    cgst_paise = getattr(invoice, "cgst_paise", 0) or 0
    sgst_paise = getattr(invoice, "sgst_paise", 0) or 0
    igst_paise = getattr(invoice, "igst_paise", 0) or 0
    tax_rate_bps = getattr(invoice, "tax_rate_bps", 1800) or 1800
    sac_code = getattr(invoice, "sac_code", "998313") or "998313"
    place_of_supply = getattr(invoice, "place_of_supply", None) or getattr(settings, "TALKAR_COMPANY_STATE_CODE", "20")
    invoice_type = getattr(invoice, "invoice_type", "tax_invoice") or "tax_invoice"
    buyer_gstin = getattr(invoice, "buyer_gstin", None) or onboarding.get("gstin") or onboarding.get("tax_id")
    supplier_gstin = getattr(invoice, "supplier_gstin", None) or getattr(settings, "TALKAR_COMPANY_GSTIN", "")

    customer_dict = {
        "id": customer.id,
        "company_name": customer.company_name,
        "contact_email": customer.contact_email,
        "contact_name": customer.contact_name,
        "contact_phone": customer.contact_phone,
        "gstin": buyer_gstin,
        "billing_address": onboarding.get("billing_address") or onboarding.get("address"),
    }

    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "amount_paise": total_paise,
        "status": invoice.status,
        "created_at": invoice.created_at,
        "wallet_transaction_id": invoice.wallet_transaction_id,
        "sub_org_id": getattr(invoice, "sub_org_id", None),
        "dograh_org_id": getattr(invoice, "dograh_org_id", None),
        "invoice_type": invoice_type,
        "financials": {
            "subtotal_paise": subtotal_paise,
            "tax_paise": tax_paise,
            "cgst_paise": cgst_paise,
            "sgst_paise": sgst_paise,
            "igst_paise": igst_paise,
            "total_paise": total_paise,
            "tax_rate_bps": tax_rate_bps,
            "tax_rate_percentage": tax_rate_bps / 100.0,
        },
        "tax_metadata": {
            "sac_code": sac_code,
            "place_of_supply": place_of_supply,
            "reverse_charge": False,
            "irn": getattr(invoice, "irn", None),
            "signed_qr_code": getattr(invoice, "signed_qr_code", None),
        },
        "seller": {
            "company_name": getattr(settings, "TALKAR_COMPANY_NAME", "4THORBIT BUSINESS SOLUTIONS PVT LTD"),
            "cin": getattr(settings, "TALKAR_COMPANY_CIN", "U74999JH2022PTC018848"),
            "gstin": supplier_gstin,
            "pan": getattr(settings, "TALKAR_COMPANY_PAN", "AAACB1234A"),
            "address": getattr(settings, "TALKAR_COMPANY_ADDRESS", "C/O Bundeshwari Devi, PN Bose Compound, Lalpur, Ranchi, Jharkhand 834001"),
            "state_code": getattr(settings, "TALKAR_COMPANY_STATE_CODE", "20"),
            "email": getattr(settings, "TALKAR_COMPANY_EMAIL", "it@4thorbit.in"),
        },
        "buyer": customer_dict,
        "customer": customer_dict,
    }

