import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def migrate():
    async with AsyncSessionLocal() as db:
        print("Adding per_minute_rate_paise to agents...")
        try:
            await db.execute(text("ALTER TABLE agents ADD COLUMN per_minute_rate_paise BIGINT;"))
            print("Added column.")
        except Exception as e:
            print(f"Error adding column: {e}")
            
        print("Creating support_requests table...")
        try:
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS support_requests (
                    id SERIAL PRIMARY KEY,
                    customer_id INTEGER NOT NULL REFERENCES customers(id),
                    type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    agent_id INTEGER REFERENCES agents(id),
                    status TEXT NOT NULL DEFAULT 'open',
                    admin_note TEXT,
                    resolved_by INTEGER REFERENCES talkar_admins(id),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP WITH TIME ZONE
                );
            """))
            print("Created table.")
        except Exception as e:
            print(f"Error creating table: {e}")

        await db.commit()
        print("Migration complete.")

if __name__ == "__main__":
    asyncio.run(migrate())
