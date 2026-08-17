from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import health, customers, wallet, billing, provisioning, admin
from contextlib import asynccontextmanager
from services import redis_client

@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.init_redis()
    yield
    await redis_client.close_redis()

app = FastAPI(title="Talkar Service API", lifespan=lifespan)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "https://talkar.in",
        "https://admin.talkar.in",
        "https://billing.talkar.in"
    ],
    allow_origin_regex="https://.*\.talkar\.in",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(customers.router, prefix="/customers", tags=["Customers"])
app.include_router(wallet.router, prefix="/wallet", tags=["Wallet"])
app.include_router(billing.router, prefix="/billing", tags=["Billing"])
app.include_router(provisioning.router, prefix="/provisioning", tags=["Provisioning"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
