from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    TALKAR_DB_URL: str = "postgresql+asyncpg://user:password@localhost/talkar_db"
    DOGRAH_DB_URL: str = "postgresql+asyncpg://user:password@localhost/dograh_db"
    
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

    # Admin auth
    TALKAR_ADMIN_JWT_SECRET: str = "change-me-in-production-admin-secret"

    # Internal auth token for Dograh → Talkar billing calls
    TALKAR_BILLING_API_TOKEN: str = "change-me-in-production-billing-token"

    # Redis for ARQ cron worker
    REDIS_URL: str = "redis://localhost:6379"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
