"""Tests for login_key hashing — covers normalization, pepper requirement, and uniqueness."""
import os
import importlib

import pytest


def _reload_db():
    import database
    importlib.reload(database)
    return database


def test_hash_is_deterministic():
    db = _reload_db()
    h1 = db.hash_login_key("AGL_AK")
    h2 = db.hash_login_key("AGL_AK")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_hash_is_case_and_whitespace_insensitive():
    db = _reload_db()
    assert db.hash_login_key("agl_ak") == db.hash_login_key("AGL_AK")
    assert db.hash_login_key("  agl_ak  ") == db.hash_login_key("AGL_AK")


def test_hash_differs_per_input():
    db = _reload_db()
    assert db.hash_login_key("AAA") != db.hash_login_key("BBB")


def test_hash_changes_with_pepper(monkeypatch):
    """Without LOGIN_KEY_PEPPER → no hashing. With a different pepper → different hash."""
    db = _reload_db()
    h_default = db.hash_login_key("AGL_AK")
    monkeypatch.setenv("LOGIN_KEY_PEPPER", "another_long_pepper_at_least_thirty_two_chars__")
    db = _reload_db()
    h_other = db.hash_login_key("AGL_AK")
    assert h_default != h_other


def test_pepper_required(monkeypatch):
    monkeypatch.delenv("LOGIN_KEY_PEPPER", raising=False)
    db = _reload_db()
    with pytest.raises(RuntimeError, match="LOGIN_KEY_PEPPER"):
        db.hash_login_key("AGL_AK")


def test_pepper_minimum_length(monkeypatch):
    monkeypatch.setenv("LOGIN_KEY_PEPPER", "short")
    db = _reload_db()
    with pytest.raises(RuntimeError, match="32"):
        db.hash_login_key("AGL_AK")


def test_generate_login_key_uniqueness():
    db = _reload_db()
    keys = {db.generate_login_key() for _ in range(200)}
    assert len(keys) == 200, "generated keys must not collide in 200 trials"


def test_generate_login_key_format():
    db = _reload_db()
    k = db.generate_login_key()
    assert k.isalnum()
    assert k.isupper()
    assert 8 <= len(k) <= 16
