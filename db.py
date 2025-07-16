import asyncio
import aiosqlite

async def init():
    async with aiosqlite.connect("data.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                email TEXT NOT NULL,
                dom REAL,
                sub REAL,
                sadist REAL,
                masochist REAL
            )
        """)
        await db.commit()
        print("Database initialized.")

asyncio.run(init())
