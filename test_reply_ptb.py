import os, asyncio
from dotenv import load_dotenv
from telegram import Bot, ReplyParameters
load_dotenv()

async def main():
    bot = Bot(os.getenv('BOT_TOKEN'))
    channel_id = os.getenv('CHANNEL_ID')
    group_id = os.getenv('DISCUSSION_GROUP_ID')
    
    # 1. Send to channel
    msg = await bot.send_message(chat_id=channel_id, text="Test from PTB")
    print(f"Sent to channel. Message ID: {msg.message_id}")
    
    await asyncio.sleep(2)
    
    # 2. Reply in discussion group
    reply_msg = await bot.send_message(
        chat_id=group_id,
        text="Reply using PTB ReplyParameters",
        reply_parameters=ReplyParameters(
            message_id=msg.message_id,
            chat_id=channel_id # This is the magic parameter!
        )
    )
    print("Reply thread id:", reply_msg.message_thread_id)
    
asyncio.run(main())
