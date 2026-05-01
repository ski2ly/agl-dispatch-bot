"""Test configuration: ensure project root is importable and required env is set."""
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path so `import database` works.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Pepper required by database.hash_login_key. Real deployments must provide their own.
os.environ.setdefault(
    "LOGIN_KEY_PEPPER",
    "test_pepper_for_unit_tests_at_least_thirty_two_characters_long",
)
# Avoid noisy "missing token" warnings from utils.security tests.
os.environ.setdefault("BOT_TOKEN", "test_bot_token")
