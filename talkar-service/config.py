from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator

class Settings(BaseSettings):
    TALKAR_DB_URL: str = "postgresql+asyncpg://user:password@localhost/talkar_db"
    DOGRAH_DB_URL: str | None = None
    POSTGRES_PASSWORD: str = "postgres"
    
    @model_validator(mode='after')
    def set_dograh_db_url(self) -> 'Settings':
        if not self.DOGRAH_DB_URL:
            # Construct it using the provided POSTGRES_PASSWORD
            self.DOGRAH_DB_URL = f"postgresql+asyncpg://postgres:{self.POSTGRES_PASSWORD}@postgres:5432/postgres"
        return self
    
    # Razorpay Keys
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    
    # Dograh API Keys
    DOGRAH_API_URL: str = "http://localhost:8000"
    DOGRAH_ADMIN_TOKEN: str = ""
    
    # Talkar Platform Keys (injected into Dograh Orgs)
    TALKAR_OPENAI_KEY: str = ""
    TALKAR_ELEVENLABS_KEY: str = ""
    TALKAR_DEEPGRAM_KEY: str = ""
    TALKAR_SMALLEST_AI_KEY: str = ""
    TALKAR_PLIVO_AUTH_ID: str = ""
    TALKAR_PLIVO_AUTH_TOKEN: str = ""
    TALKAR_DEEPGRAM_PROJECT_ID: str = ""
    VENDOR_ALERT_EMAIL: str = "it@4thorbit.com"
    VENDOR_BALANCE_POLL_INTERVAL_MINUTES: int = 30
    VENDOR_BALANCE_MANUAL_REFRESH_COOLDOWN_SECONDS: int = 30
    # Admin auth
    TALKAR_ADMIN_JWT_SECRET: str = "change-me-in-production-admin-secret"

    # Internal auth token for Dograh → Talkar billing calls
    TALKAR_BILLING_API_TOKEN: str = "change-me-in-production-billing-token"

    # Redis for ARQ cron worker
    REDIS_URL: str = "redis://localhost:6379"

    # Email (ZeptoMail SMTP)
    SMTP_HOST: str = "smtp.zeptomail.in"
    SMTP_PORT: int = 587
    SMTP_EMAIL: str = "emailapikey"
    SMTP_PASSWORD: str = ""
    FROM_EMAIL: str = "noreply@talkar.in"
    ADMIN_EMAIL: str = "admin@talkar.in"

    # Database connection pool and logging settings
    DB_POOL_SIZE: int = 30
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    DB_ECHO: bool = False

    ENVIRONMENT: str = "development"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

# V2 Config Additions
CALL_BLOCK_THRESHOLD_PAISE = 50000  # ₹500
DEFAULT_PHONE_NUMBER_MONTHLY_COST_PAISE = 50000  # ₹500/month per additional phone number lease (Issue 31)

TIER_CONFIG = {
    "starter": {
        "per_minute_rate_paise": 600,    # ₹6/min
        "concurrent_call_limit": 2,
        "max_call_duration_seconds": 900,
        "llm_model": "gpt-4o-mini",
        "tts_provider": "deepgram",
        "stt_provider": "deepgram",
        "free_phone_numbers": 1,
        "activation_deposit_paise": 600000,  # ₹6,000 min to activate
    },
    "growth": {
        "per_minute_rate_paise": 600,    # ₹6/min — same as starter
        "concurrent_call_limit": 2,
        "max_call_duration_seconds": 900,
        "llm_model": "gpt-4o-mini",
        "tts_provider": "smallest_ai",  # Indian-optimised low-latency TTS
        "stt_provider": "deepgram",     # STT stays on Deepgram
        "default_voice_id": "mishka",    # Default Smallest AI voice
        "free_phone_numbers": 1,
        "activation_deposit_paise": 600000,  # ₹6,000 min to activate
    },
    "pro": {
        "per_minute_rate_paise": 1800,   # ₹18/min (covers ElevenLabs TTS COGS ~₹7.75/min + Plivo + STT + LLM with healthy margin)
        "concurrent_call_limit": 10,
        "max_call_duration_seconds": 1800,
        "llm_model": "gpt-4o-mini",
        "tts_provider": "elevenlabs",
        "stt_provider": "deepgram",
        "free_phone_numbers": 2,
        "activation_deposit_paise": 800000,  # ₹8,000 min to activate
    },
    "elite": {
        "per_minute_rate_paise": 1200,   # ₹12/min
        "concurrent_call_limit": 50,
        "max_call_duration_seconds": 1200,
        "llm_model": "gpt-4o",
        "tts_provider": "elevenlabs",
        "stt_provider": "deepgram",
        "free_phone_numbers": 5,
        "activation_deposit_paise": 2500000,
        "disabled": True,
    },
    "custom": {
        "per_minute_rate_paise": 1800,
        "concurrent_call_limit": 10,
        "max_call_duration_seconds": 1800,
        "llm_model": "gpt-4o-mini",
        "tts_provider": "elevenlabs",
        "stt_provider": "deepgram",
        "free_phone_numbers": 2,
        "activation_deposit_paise": 800000,
    },
}

def resolve_tier_config(subscription=None) -> dict:
    """
    Returns the effective tier config dict for a given subscription row or tier name.
    For custom plans, merges the stored custom_config over sane starter defaults.
    For standard plans, returns from TIER_CONFIG directly.
    Pass None to get the starter fallback.
    """
    if isinstance(subscription, str):
        plan = subscription
        custom_config = None
        rate = None
    elif subscription is not None:
        plan = getattr(subscription, "plan", None)
        custom_config = getattr(subscription, "custom_config", None)
        rate = getattr(subscription, "per_minute_rate_paise", None)
    else:
        plan = "starter"
        custom_config = None
        rate = None

    if plan == "custom":
        base = dict(TIER_CONFIG["custom"])
        if isinstance(custom_config, dict):
            base.update(custom_config)
        if rate is not None and rate > 0:
            base["per_minute_rate_paise"] = rate
        tts_provider = base.get("tts_provider")
        floor = get_minimum_rate_floor_paise(tts_provider)
        if base.get("per_minute_rate_paise", 0) < floor:
            base["per_minute_rate_paise"] = floor
        return base

    effective_plan = plan or "starter"
    return TIER_CONFIG.get(effective_plan, TIER_CONFIG["starter"])

# Minimum cost floors per minute (in paise) to protect against negative gross margins.
# ElevenLabs / Cartesia premium voices cost ~$0.18/1k chars (COGS ~₹8.40+/min with telecom+STT+LLM)
# Standard voices (Deepgram / Smallest AI) cost ~₹1.20 - ₹2.50/min COGS.
PROVIDER_MIN_RATE_FLOOR_PAISE = {
    "elevenlabs": 1200,   # Minimum ₹12.00/min floor
    "cartesia": 1200,     # Minimum ₹12.00/min floor
    "smallest": 500,      # Minimum ₹5.00/min floor
    "smallest_ai": 500,   # Minimum ₹5.00/min floor
    "deepgram": 400,      # Minimum ₹4.00/min floor
}
DEFAULT_MIN_RATE_FLOOR_PAISE = 400

def get_minimum_rate_floor_paise(tts_provider: str | None = None) -> int:
    """Returns the absolute minimum allowable per_minute_rate_paise for a given TTS provider."""
    if not tts_provider:
        return DEFAULT_MIN_RATE_FLOOR_PAISE
    norm = tts_provider.lower().strip()
    for provider, floor in PROVIDER_MIN_RATE_FLOOR_PAISE.items():
        if provider in norm:
            return floor
    return DEFAULT_MIN_RATE_FLOOR_PAISE
