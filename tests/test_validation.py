"""Tests for API input validation helpers — bid amount, request payload sanitization."""
import sys
import importlib

# api/server.py imports modules that need env (db pool isn't created at import — only on init_db).
# We import only the helper functions so no network connections are attempted.


def test_validate_bid_accepts_basic_payload():
    from api.server import _validate_bid
    req_id, err = _validate_bid({"req_id": 42, "amount": "1500", "currency": "USD"})
    assert err is None
    assert req_id == 42


def test_validate_bid_rejects_negative_amount():
    from api.server import _validate_bid
    _, err = _validate_bid({"req_id": 1, "amount": -100, "currency": "USD"})
    assert err and "out of range" in err


def test_validate_bid_rejects_huge_amount():
    from api.server import _validate_bid
    _, err = _validate_bid({"req_id": 1, "amount": 10**12, "currency": "USD"})
    assert err and "out of range" in err


def test_validate_bid_rejects_non_numeric():
    from api.server import _validate_bid
    _, err = _validate_bid({"req_id": 1, "amount": "abc", "currency": "USD"})
    assert err and "number" in err


def test_validate_bid_handles_european_format():
    from api.server import _validate_bid
    req_id, err = _validate_bid({"req_id": 5, "amount": "1 234,56", "currency": "EUR"})
    assert err is None
    assert req_id == 5


def test_validate_bid_rejects_unknown_currency():
    from api.server import _validate_bid
    _, err = _validate_bid({"req_id": 1, "amount": 100, "currency": "BTC"})
    assert err and "currency" in err


def test_validate_bid_rejects_missing_req_id():
    from api.server import _validate_bid
    _, err = _validate_bid({"amount": 100, "currency": "USD"})
    assert err and "req_id" in err


def test_sanitize_payload_drops_unknown_columns():
    from api.server import _sanitize_request_payload
    raw = {"cargo_name": "Steel", "creator_id": 999, "DROP TABLE users": "x", "route_from": "Tashkent"}
    cleaned = _sanitize_request_payload(raw, is_admin=False)
    assert "cargo_name" in cleaned
    assert "route_from" in cleaned
    # Server-controlled fields and bogus keys must be stripped.
    assert "creator_id" not in cleaned
    assert "DROP TABLE users" not in cleaned


def test_sanitize_payload_caps_string_length():
    from api.server import _sanitize_request_payload
    huge = "x" * 10_000
    cleaned = _sanitize_request_payload({"cargo_name": huge}, is_admin=True)
    assert len(cleaned["cargo_name"]) == 4000


def test_sanitize_payload_drops_invalid_status():
    from api.server import _sanitize_request_payload
    cleaned = _sanitize_request_payload({"status": "ARBITRARY"}, is_admin=True)
    assert "status" not in cleaned


def test_sanitize_payload_keeps_canonical_status():
    from api.server import _sanitize_request_payload
    cleaned = _sanitize_request_payload({"status": "Открыта"}, is_admin=True)
    assert cleaned["status"] == "Открыта"
