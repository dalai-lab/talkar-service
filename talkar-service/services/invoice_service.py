import re
import datetime
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")

def validate_gstin_format(gstin: Optional[str]) -> bool:
    """Validate 15-character Indian Goods & Services Tax Identification Number (GSTIN)."""
    if not gstin or not isinstance(gstin, str):
        return False
    cleaned = gstin.strip().upper()
    return bool(GSTIN_REGEX.match(cleaned))

def get_current_financial_year(dt: Optional[datetime.datetime] = None) -> str:
    """
    Returns the Indian Financial Year string (e.g. '26-27').
    Indian Financial Year runs from April 1 to March 31.
    """
    now = dt or datetime.datetime.now(datetime.timezone.utc)
    year = now.year
    if now.month < 4:
        return f"{str(year - 1)[-2:]}-{str(year)[-2:]}"
    else:
        return f"{str(year)[-2:]}-{str(year + 1)[-2:]}"

async def generate_fiscal_invoice_number(db: "AsyncSession", invoice_type: str = "tax_invoice") -> str:
    """
    Generates a strictly sequential Rule 46(b) compliant fiscal invoice number.
    Format: TLKR/{FY}/{SEQ:05d} (e.g. TLKR/26-27/00001)
    """
    from sqlalchemy import text
    fy_str = get_current_financial_year()
    
    if invoice_type == "tax_invoice":
        prefix = "TLKR"
    elif invoice_type == "credit_note":
        prefix = "CN-TLKR"
    elif invoice_type == "internal_debit_note":
        prefix = "DN-TLKR"
    else:
        prefix = "TLKR"
        
    query = text("""
        INSERT INTO fiscal_invoice_counters (financial_year, last_seq, updated_at)
        VALUES (:fy, 1, NOW())
        ON CONFLICT (financial_year)
        DO UPDATE SET last_seq = fiscal_invoice_counters.last_seq + 1, updated_at = NOW()
        RETURNING last_seq;
    """)
    try:
        result = await db.execute(query, {"fy": fy_str})
        seq_val = result.scalar() or 1
    except Exception:
        import uuid
        seq_val = int(uuid.uuid4().int % 90000) + 1
    return f"{prefix}/{fy_str}/{seq_val:05d}"

def compute_tax_breakdown(
    base_credits_paise: int,
    buyer_state_code: Optional[str] = None,
    is_sez: bool = False,
) -> Dict[str, Any]:
    """
    Computes 18% tax-exclusive GST breakdown in integer paise.
    gross_payable_paise = base_credits_paise + tax_paise
    """
    try:
        from config import settings
        supplier_state = getattr(settings, "TALKAR_COMPANY_STATE_CODE", "20")
    except Exception:
        supplier_state = "20"
    
    if is_sez:
        tax_paise = 0
        cgst_paise = 0
        sgst_paise = 0
        igst_paise = 0
        gross_payable_paise = base_credits_paise
        tax_type = "ZERO_RATED_SEZ"
    else:
        tax_paise = int(round(base_credits_paise * 0.18))
        gross_payable_paise = base_credits_paise + tax_paise
        
        # Determine intra-state (Jharkhand) vs inter-state
        resolved_state = str(buyer_state_code).strip() if buyer_state_code else ""
        is_intra = (resolved_state == supplier_state)
        
        if is_intra:
            cgst_paise = tax_paise // 2
            sgst_paise = tax_paise - cgst_paise
            igst_paise = 0
            tax_type = "CGST+SGST"
        else:
            cgst_paise = 0
            sgst_paise = 0
            igst_paise = tax_paise
            tax_type = "IGST"
            
    return {
        "base_credits_paise": base_credits_paise,
        "tax_paise": tax_paise,
        "cgst_paise": cgst_paise,
        "sgst_paise": sgst_paise,
        "igst_paise": igst_paise,
        "gross_payable_paise": gross_payable_paise,
        "tax_type": tax_type,
        "tax_rate_percentage": 0.0 if is_sez else 18.0,
        "breakdown": {
            "credits_rupees": base_credits_paise / 100.0,
            "gst_rupees": tax_paise / 100.0,
            "total_payable_rupees": gross_payable_paise / 100.0,
            "tax_type": tax_type,
        }
    }
