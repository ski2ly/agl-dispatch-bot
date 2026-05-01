"""Tests for utils.security: HMAC verification of Telegram WebApp initData."""
import time
import hmac
import hashlib
from urllib.parse import quote

from utils.security import verify_init_data, extract_user_from_init_data


TOKEN = "test_bot_token"


def _build_init_data(user_payload: str = '{"id":42,"first_name":"Albert"}',
                     auth_date: int | None = None,
                     extra: dict | None = None,
                     token: str = TOKEN) -> str:
    """Build a valid initData string with a correct HMAC for the given token."""
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": user_payload,
        "query_id": "abc123",
    }
    if extra:
        fields.update(extra)
    # Sort keys, build data_check_string, sign with derived secret.
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    # Telegram URL-encodes values in initData.
    pairs = [f"{k}={quote(v, safe='')}" for k, v in fields.items()]
    pairs.append(f"hash={sig}")
    return "&".join(pairs)


def test_valid_init_data_passes():
    init = _build_init_data()
    assert verify_init_data(init, TOKEN) is True


def test_tampered_user_field_fails():
    init = _build_init_data()
    # Replace user payload after signing → signature must fail.
    tampered = init.replace("Albert", "Hacker")
    assert verify_init_data(tampered, TOKEN) is False


def test_missing_hash_fails():
    init = _build_init_data()
    no_hash = "&".join(p for p in init.split("&") if not p.startswith("hash="))
    assert verify_init_data(no_hash, TOKEN) is False


def test_expired_auth_date_fails():
    # 25 hours ago = past 24h window.
    old = int(time.time()) - 25 * 3600
    init = _build_init_data(auth_date=old)
    assert verify_init_data(init, TOKEN) is False


def test_wrong_token_fails():
    init = _build_init_data(token="not_the_real_token")
    assert verify_init_data(init, TOKEN) is False


def test_empty_inputs_fail():
    assert verify_init_data("", TOKEN) is False
    assert verify_init_data("foo=bar", "") is False


def test_extract_user_returns_int_id():
    init = _build_init_data(user_payload='{"id":12345,"first_name":"Bob"}')
    uid, name = extract_user_from_init_data(init)
    assert uid == 12345
    assert isinstance(uid, int)
    assert name == "Bob"


def test_extract_user_handles_missing_first_name():
    init = _build_init_data(user_payload='{"id":1}')
    uid, name = extract_user_from_init_data(init)
    assert uid == 1
    assert name == "Сотрудник"  # default fallback


def test_extract_user_handles_garbage():
    uid, name = extract_user_from_init_data("not_a_real_init_data_string")
    assert uid is None
    assert name == "Сотрудник"
