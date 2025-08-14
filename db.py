import asyncio
import aiosqlite

async def init():
    async with aiosqlite.connect("data.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                email TEXT NOT NULL,
brat REAL,
brat_tamer REAL,
degradee  REAL,
degrader REAL,
sub REAL,
dom REAL,
rope_bunny REAL,
rigger REAL,
pet  REAL,
owner REAL,
slave REAL,
master REAL,
exhibitionist REAL,
voyeur REAL,
little REAL,
daddy REAL,
masochist REAL,
sadist REAL,
predator  REAL,
prey REAL,
cuckold REAL,
cuckqueen REAL,
poly REAL,
explorer REAL,
switch REAL
            )
        """)
        await db.commit()
        print("Database initialized.")

asyncio.run(init())
