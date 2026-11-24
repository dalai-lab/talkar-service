-- Migration v9: B2B Tax-Exclusive GST Invoicing & Fiscal Counters

-- 1. Create fiscal invoice sequence counter table (bulletproof across FY rollovers)
CREATE TABLE IF NOT EXISTS fiscal_invoice_counters (
    financial_year VARCHAR(8) PRIMARY KEY,
    last_seq INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. Add new tax columns to invoices table
ALTER TABLE invoices 
    ADD COLUMN IF NOT EXISTS subtotal_paise BIGINT,
    ADD COLUMN IF NOT EXISTS tax_paise BIGINT,
    ADD COLUMN IF NOT EXISTS cgst_paise BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sgst_paise BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS igst_paise BIGINT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_paise BIGINT,
    ADD COLUMN IF NOT EXISTS tax_rate_bps INTEGER DEFAULT 1800,
    ADD COLUMN IF NOT EXISTS sac_code VARCHAR(16) DEFAULT '998313',
    ADD COLUMN IF NOT EXISTS invoice_type VARCHAR(32) DEFAULT 'tax_invoice',
    ADD COLUMN IF NOT EXISTS place_of_supply VARCHAR(64),
    ADD COLUMN IF NOT EXISTS buyer_gstin VARCHAR(15),
    ADD COLUMN IF NOT EXISTS supplier_gstin VARCHAR(15) DEFAULT '',
    ADD COLUMN IF NOT EXISTS irn VARCHAR(64),
    ADD COLUMN IF NOT EXISTS signed_qr_code TEXT;

-- 3. Backfill existing legacy invoices safely
UPDATE invoices 
SET 
  total_paise = amount_paise,
  subtotal_paise = ROUND(amount_paise / 1.18),
  tax_paise = amount_paise - ROUND(amount_paise / 1.18),
  igst_paise = amount_paise - ROUND(amount_paise / 1.18),
  invoice_type = 'tax_invoice'
WHERE subtotal_paise IS NULL;
