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
    if not msg or str(msg.chat_id) != str(DISCUSSION_GROUP_ID):
        return

    if not (msg.reply_to_message and msg.reply_to_message.forward_from_chat):
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
