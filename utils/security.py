import time
import hmac
import hashlib
import json
import logging
from urllib.parse import unquote

logger = logging.getLogger(__name__)

def verify_init_data(init_data: str, token: str) -> bool:
    if not init_data or not token:
        return False
    try:
        parsed = dict(x.split("=", 1) for x in init_data.split("&"))
        if "hash" not in parsed:
            return False

        # Reject stale signatures (>24h). Telegram clients refresh initData on every open,
        # so a longer window only enlarges the replay window without UX benefit.
        try:
            auth_date = int(parsed.get("auth_date", "0"))
        except ValueError:
            return False
        if auth_date <= 0 or time.time() - auth_date > 86400:
            logger.warning("initData missing or expired auth_date")
            return False

        hash_val = parsed.pop("hash")
        data_check_string = "\n".join(
            f"{k}={unquote(v)}" for k, v in sorted(parsed.items())
        )

        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(calc_hash, hash_val)
    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"verify_init_data parse error: {e}")
        return False

def extract_user_from_init_data(init_data: str) -> tuple[int | None, str]:
    if not init_data:
        return None, "Сотрудник"
    try:
        parsed = dict(x.split("=", 1) for x in init_data.split("&"))
        user_json = json.loads(unquote(parsed.get("user", "{}")))
        uid = user_json.get("id")
        return (int(uid) if uid is not None else None,
                str(user_json.get("first_name") or "Сотрудник"))
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        logger.error(f"extract_user_from_init_data error: {e}")
        return None, "Сотрудник"
