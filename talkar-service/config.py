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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()

# V2 Config Additions
CALL_BLOCK_THRESHOLD_PAISE = 50000  # ₹500

TIER_CONFIG = {
    "starter": {
        "per_minute_rate_paise": 600,    # ₹6/min
        "concurrent_call_limit": 2,
        "max_call_duration_seconds": 900,
        "llm_model": "gpt-4o-mini",
        "tts_provider": "deepgram",
        "free_phone_numbers": 1,
        "activation_deposit_paise": 600000,  # ₹6,000 min to activate
    },
    "growth": {
        "per_minute_rate_paise": 600,    # ₹6/min — same as starter
        "concurrent_call_limit": 2,
        "max_call_duration_seconds": 900,
        "llm_model": "gpt-4o-mini",
        "tts_provider": "smallest_ai",  # Indian-optimised low-latency TTS
        "default_voice_id": "meera",    # Default Smallest AI voice
        "free_phone_numbers": 1,
        "activation_deposit_paise": 600000,  # ₹6,000 min to activate
    },
    "pro": {
        "per_minute_rate_paise": 400,    # ₹4/min
        "concurrent_call_limit": 10,
        "max_call_duration_seconds": 1800,
        "llm_model": "gpt-4o",
        "tts_provider": "elevenlabs",
        "free_phone_numbers": 2,
        "activation_deposit_paise": 800000,  # ₹8,000 min to activate
    },
    "elite": {
        "per_minute_rate_paise": 1200,   # ₹12/min
        "concurrent_call_limit": 50,
        "max_call_duration_seconds": 1200,
        "llm_model": "gpt-4o",
        "tts_provider": "elevenlabs",
        "free_phone_numbers": 5,
        "activation_deposit_paise": 2500000,
        "disabled": True,
    },
}
