import asyncio
import aiosqlite

async def init():
    async with aiosqlite.connect("data.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                session_id TEXT,
                client_id TEXT,
                ip TEXT,
                ua TEXT,
                event TEXT NOT NULL,
                url TEXT,
                referrer TEXT,
                props TEXT
                )           
                        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_events_event ON events(event)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)" )         
        await db.execute("""
            CREATE TABLE IF NOT EXISTS supporter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL CHECK(LENGTH(user_id) <= 15),
                comment TEXT NOT NULL CHECK(LENGTH(comment) <= 100),
                timestamp INTEGER NOT NULL
                )           
                        """)  
        await db.commit()
        print("Database initialized.")

asyncio.run(init())

    
