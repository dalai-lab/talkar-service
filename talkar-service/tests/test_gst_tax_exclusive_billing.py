import datetime
from services.invoice_service import (
    validate_gstin_format,
    get_current_financial_year,
    compute_tax_breakdown,
)

def test_validate_gstin_format():
    # Valid 15-character GSTINs
    assert validate_gstin_format("20AAACB1234A1Z5") is True
    assert validate_gstin_format("27AABCU9603R1ZM") is True
    assert validate_gstin_format("07AAAAA0000A1Z5") is True
    # Case insensitivity & whitespace stripping
    assert validate_gstin_format("  20aaacb1234a1z5  ") is True
    
    # Invalid formats
    assert validate_gstin_format(None) is False
    assert validate_gstin_format("") is False
    assert validate_gstin_format("12345") is False
    assert validate_gstin_format("20AAACB1234A1Z") is False  # 14 chars
    assert validate_gstin_format("20AAACB1234A1Z59") is False # 16 chars
    assert validate_gstin_format("XXAAACB1234A1Z5") is False # State code not 2 digits

def test_financial_year_calculation():
    # Before April (e.g. Feb 2026 -> FY 25-26)
    feb_date = datetime.datetime(2026, 2, 15, tzinfo=datetime.timezone.utc)
    assert get_current_financial_year(feb_date) == "25-26"
    
    # After April (e.g. May 2026 -> FY 26-27)
    may_date = datetime.datetime(2026, 5, 20, tzinfo=datetime.timezone.utc)
    assert get_current_financial_year(may_date) == "26-27"
    
    # Border: March 31, 2027 -> FY 26-27
    mar_date = datetime.datetime(2027, 3, 31, 23, 59, tzinfo=datetime.timezone.utc)
    assert get_current_financial_year(mar_date) == "26-27"
    
    # Border: April 1, 2027 -> FY 27-28
    apr_date = datetime.datetime(2027, 4, 1, 0, 0, tzinfo=datetime.timezone.utc)
    assert get_current_financial_year(apr_date) == "27-28"

def test_intra_state_gst_breakdown():
    # Supplier is Jharkhand (state code "20")
    # Base: ₹10,000 = 1,000,000 paise
    base = 1000000
    res = compute_tax_breakdown(base, buyer_state_code="20", is_sez=False)
    
    assert res["base_credits_paise"] == 1000000
    assert res["tax_paise"] == 180000  # 18% of 10,000 = ₹1,800
    assert res["gross_payable_paise"] == 1180000  # ₹11,800 Total
    assert res["tax_type"] == "CGST+SGST"
    assert res["cgst_paise"] == 90000  # 9%
    assert res["sgst_paise"] == 90000  # 9%
    assert res["igst_paise"] == 0
    assert res["gross_payable_paise"] == res["base_credits_paise"] + res["tax_paise"]

def test_inter_state_gst_breakdown():
    # Buyer in Maharashtra ("27")
    # Base: ₹50,000 = 5,000,000 paise
    base = 5000000
    res = compute_tax_breakdown(base, buyer_state_code="27", is_sez=False)
    
    assert res["base_credits_paise"] == 5000000
    assert res["tax_paise"] == 900000  # 18% of 50,000 = ₹9,000
    assert res["gross_payable_paise"] == 5900000  # ₹59,000 Total
    assert res["tax_type"] == "IGST"
    assert res["cgst_paise"] == 0
    assert res["sgst_paise"] == 0
    assert res["igst_paise"] == 900000

def test_sez_zero_rated_supply():
    # SEZ Unit under LUT (0% GST)
    base = 1000000
    res = compute_tax_breakdown(base, buyer_state_code="27", is_sez=True)
    
    assert res["base_credits_paise"] == 1000000
    assert res["tax_paise"] == 0
    assert res["gross_payable_paise"] == 1000000
    assert res["tax_type"] == "ZERO_RATED_SEZ"
    assert res["cgst_paise"] == 0
    assert res["sgst_paise"] == 0
    assert res["igst_paise"] == 0

def test_inverse_fallback_calculation():
    # If Razorpay gross is ₹11,800 (1,180,000 paise), verify inverse mathematical formula
    gross = 1180000
    base = int(round(gross / 1.18))
    tax = gross - base
    
    assert base == 1000000
    assert tax == 180000
    assert base + tax == gross

if __name__ == "__main__":
    test_validate_gstin_format()
    test_financial_year_calculation()
    test_intra_state_gst_breakdown()
    test_inter_state_gst_breakdown()
    test_sez_zero_rated_supply()
    test_inverse_fallback_calculation()
    print("ALL GST TAX EXCLUSIVE BILLING UNIT TESTS PASSED!")
