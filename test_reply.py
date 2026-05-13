import os, requests
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('BOT_TOKEN')

def test():
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    channel_id = os.getenv('CHANNEL_ID')
    group_id = os.getenv('DISCUSSION_GROUP_ID')
    
    # 1. Send to channel
    res = requests.post(url, json={
        'chat_id': channel_id,
        'text': 'Test channel message'
    }).json()
    
    if not res.get('ok'):
        print("Failed to send to channel", res)
        return
        
    msg_id = res['result']['message_id']
    print(f"Sent to channel. Message ID: {msg_id}")
    
    import time
    time.sleep(2) # Wait for forward to discussion group
    
    # 2. Reply in discussion group
    res2 = requests.post(url, json={
        'chat_id': group_id,
        'text': 'Reply using ReplyParameters',
        'reply_parameters': {
            'message_id': msg_id,
            'chat_id': channel_id
        }
    }).json()
    
    print("Reply result:", res2)

test()
