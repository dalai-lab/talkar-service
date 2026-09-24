CREATE TABLE invoices (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    wallet_transaction_id INTEGER REFERENCES wallet_transactions(id) UNIQUE,
    invoice_number TEXT NOT NULL UNIQUE,
    amount_paise BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'paid',
    pdf_url TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_invoices_customer ON invoices(customer_id, created_at DESC);
