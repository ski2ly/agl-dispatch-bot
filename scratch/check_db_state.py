import asyncio
import os
from database import Database

async def check_db():
    db = Database()
    await db.init_db()
    try:
        # Check specific requests
        async with db._pool.acquire() as conn:
            rows = await conn.fetch("SELECT id, channel_msg_id, discussion_msg_id, status FROM requests ORDER BY id DESC LIMIT 5")
            print("--- Last 5 Requests ---")
            for r in rows:
                print(f"ID: {r['id']}, Status: {r['status']}, ChannelMsg: {r['channel_msg_id']}, DiscMsg: {r['discussion_msg_id']}")
            
            # Check if any bids were linked
            bids = await conn.fetch("SELECT id, request_id, amount, manager_name FROM bids ORDER BY id DESC LIMIT 5")
            print("\n--- Last 5 Bids ---")
            for b in bids:
                print(f"BidID: {b['id']}, ReqID: {b['request_id']}, Amount: {b['amount']}, Manager: {b['manager_name']}")

            # Check settings
            settings = await db.get_settings()
            print("\n--- Settings ---")
            print(f"Channel ID: {settings.get('channel_id')}")
            print(f"Discussion ID: {settings.get('discussion_id')}")
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(check_db())
