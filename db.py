import asyncio
import aiosqlite

async def init():
    async with aiosqlite.connect("data.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                email TEXT NOT NULL,
                ageplayer REAL, brat REAL, brat_tamer REAL, daddy_mommy REAL, degrader REAL,
                dominant REAL, degradee REAL, little REAL, masochist REAL, master_mistress REAL,
                non_monogamist REAL, owner REAL, primal_hunter REAL, pet REAL, primal_prey REAL,
                rigger REAL, rope_bunny REAL, sadist REAL, slave REAL, submissive REAL, switch REAL,
                vanilla REAL, voyeur REAL, exhibitionist REAL, experimentalist REAL
            )
        """)
        await db.commit()
        print("Database initialized.")

asyncio.run(init())
