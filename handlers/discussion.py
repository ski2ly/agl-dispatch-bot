import os
import re
import logging
from telegram import Update
from telegram.ext import ContextTypes
from database import db

logger = logging.getLogger(__name__)
DISCUSSION_GROUP_ID = os.getenv("DISCUSSION_GROUP_ID")
# Match the canonical card header: "#NNNN" or "ЗАЯВКА #NNNN" — at least 1, at most 7 digits.
_REQ_ID_RE = re.compile(r"#(\d{1,7})\b")


async def handle_discussion_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save messages from discussion group as comments to the corresponding request."""
    msg = update.effective_message
    if not msg:
        return

    # Fetch settings to get current IDs
    settings = await db.get_settings()
    target_discussion = settings.get("discussion_id") or os.getenv("DISCUSSION_GROUP_ID")
    target_channel = settings.get("channel_id") or os.getenv("CHANNEL_ID")

    # LOG EVERY MESSAGE IN GROUP FOR DEBUGGING
    fwd_info = "No"
    if hasattr(msg, 'forward_from_chat') and msg.forward_from_chat:
        fwd_info = str(msg.forward_from_chat.id)
    elif hasattr(msg, 'forward_origin') and msg.forward_origin and hasattr(msg.forward_origin, 'chat'):
        fwd_info = str(msg.forward_origin.chat.id)

    logger.info(f"🔍 DEBUG: Msg in chat {msg.chat_id} ({msg.chat.title}). Text: {(msg.text or msg.caption or '')[:50]}. "
                f"FwdFrom: {fwd_info}. "
                f"AutoFwd: {msg.is_automatic_forward}")

    if not target_discussion:
        return

    # Normalize IDs for comparison
    def norm(i):
        if not i: return ""
        s = str(i)
        if s.startswith("-100"): return s[4:]
        return s.lstrip("-")

    if norm(msg.chat_id) != norm(target_discussion):
        return

    # A message is a potential link if it's an automatic forward OR it's a forward from our channel
    is_fwd = msg.is_automatic_forward
    fwd_chat_id = None
    if hasattr(msg, 'forward_from_chat') and msg.forward_from_chat:
        fwd_chat_id = msg.forward_from_chat.id
    elif hasattr(msg, 'forward_origin') and msg.forward_origin and hasattr(msg.forward_origin, 'chat'):
        fwd_chat_id = msg.forward_origin.chat.id

    if not is_fwd and fwd_chat_id:
        if norm(fwd_chat_id) == norm(target_channel):
            is_fwd = True

    if is_fwd:
        text = msg.text or msg.caption or ""
        match = _REQ_ID_RE.search(text)
        
        if match:
            req_id = int(match.group(1))
            fwd_msg_id = None
            if hasattr(msg, 'forward_from_message_id'):
                fwd_msg_id = msg.forward_from_message_id
            elif hasattr(msg, 'forward_origin') and hasattr(msg.forward_origin, 'message_id'):
                fwd_msg_id = msg.forward_origin.message_id
            
            try:
                # Link by ID parsed from text
                await db.update_request(req_id, {
                    "discussion_msg_id": msg.message_id,
                    "channel_msg_id": fwd_msg_id
                })
                logger.info(f"✅ LINK SUCCESS: request #{req_id} linked to discussion_msg_id {msg.message_id}")
                return
            except Exception as e:
                logger.error(f"❌ LINK ERROR (text): {e}")
        
        # Fallback to numeric link
        # We need fwd_from_id and fwd_msg_id which we calculated above
        if fwd_chat_id and fwd_msg_id:
            if norm(fwd_chat_id) == norm(target_channel):
                try:
                    updated = await db.update_request_by_channel_msg_id(
                        fwd_msg_id, 
                        {"discussion_msg_id": msg.message_id}
                    )
                    if updated:
                        logger.info(f"✅ LINK SUCCESS: channel_msg_id {fwd_msg_id} linked to disc_msg {msg.message_id}")
                        return
                except Exception as e:
                    logger.error(f"❌ LINK ERROR (numeric): {e}")
        
        logger.warning(f"⚠️ LINK FAILED: Forward received but could not find request. Text matches: {bool(match)}")
        return

    # 2. Handle User Comments (replies to the forward)
    reply_fwd_chat_id = None
    reply_fwd_msg_id = None
    if msg.reply_to_message:
        rm = msg.reply_to_message
        if hasattr(rm, 'forward_from_chat') and rm.forward_from_chat:
            reply_fwd_chat_id = rm.forward_from_chat.id
            reply_fwd_msg_id = rm.forward_from_message_id
        elif hasattr(rm, 'forward_origin'):
            o = rm.forward_origin
            if o:
                if hasattr(o, 'chat'): reply_fwd_chat_id = o.chat.id
                if hasattr(o, 'message_id'): reply_fwd_msg_id = o.message_id

    if not reply_fwd_chat_id:
        return

    reply_text = msg.reply_to_message.text or ""
    match = _REQ_ID_RE.search(reply_text)
    if not match:
        return

    try:
        req_id = int(match.group(1))
    except (ValueError, OverflowError):
        return

    if not msg.from_user:
        return

    user_id = msg.from_user.id
    user_name = msg.from_user.first_name or "Сотрудник"
    comment_text = (msg.text or "[Вложение]")[:4000]  # bound the column size

    try:
        await db.add_comment(req_id, user_id, user_name, comment_text, "discussion")
        logger.info(f"💬 Saved discussion comment for #{req_id:04d}")
    except Exception as e:
        logger.error(f"Failed to save discussion comment for #{req_id}: {e}")
