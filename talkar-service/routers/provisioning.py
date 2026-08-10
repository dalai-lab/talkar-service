# This router is intentionally minimal.
# Admin-triggered provisioning retry is at POST /admin/customers/{id}/provision/retry (guarded with JWT).
# Webhook-triggered provisioning runs from routers/billing.py webhook handler.
from fastapi import APIRouter
router = APIRouter()
