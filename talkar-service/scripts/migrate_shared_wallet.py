import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def migrate():
    async with AsyncSessionLocal() as db:
        print("Adding billing_org_id to customers...")
        try:
            await db.execute(text("ALTER TABLE customers ADD COLUMN billing_org_id INTEGER REFERENCES customers(id);"))
            print("Added column.")
        except Exception as e:
            print(f"Error adding column: {e}")
            
        print("Dropping contact_email UNIQUE constraint...")
        try:
            # Drop constraint (usually named customers_contact_email_key)
            await db.execute(text("ALTER TABLE customers DROP CONSTRAINT customers_contact_email_key;"))
            print("Dropped constraint.")
        except Exception as e:
            print(f"Error dropping constraint: {e}")

        await db.commit()
        print("Migration complete.")

if __name__ == "__main__":
    asyncio.run(migrate())
