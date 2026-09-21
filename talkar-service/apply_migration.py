import asyncio
from db.session import engine
from sqlalchemy import text

async def run():
    with open("migration_automation.sql", "r", encoding="utf-8") as f:
        sql = f.read()
        
    async with engine.begin() as conn:
        for stmt in sql.split(';'):
            if stmt.strip():
                await conn.execute(text(stmt))
    print("Migration applied successfully")

if __name__ == "__main__":
    asyncio.run(run())