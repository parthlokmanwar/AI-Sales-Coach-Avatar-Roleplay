"""
Unit tests for the multi-account Tavus fallback (core/tavus_accounts.py).

Covers: account parsing (TAVUS_ACCOUNTS + legacy TAVUS_API_KEY fallback),
custom-vs-stock replica resolution across accounts, reverse-index lookups,
and failover error classification.

Run:  python -m pytest tests/test_tavus_fallback.py -q
"""

import os
import contextlib

from core.tavus_accounts import (
    CUSTOM_AVATARS,
    get_accounts,
    get_key,
    primary_key,
    resolve_replica,
    owning_account,
    should_failover,
)


@contextlib.contextmanager
def _env(**kwargs):
    saved = {k: os.environ.get(k) for k in kwargs}
    try:
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeHTTPError(Exception):
    def __init__(self, status, text=""):
        self.response = _FakeResponse(status, text)


# ── Account parsing ──────────────────────────────────────────────────────────

def test_accounts_parse_ordered():
    with _env(TAVUS_ACCOUNTS="primary:key-aaa, backup1:key-bbb"):
        accounts = get_accounts()
    assert accounts == [("primary", "key-aaa"), ("backup1", "key-bbb")]


def test_accounts_legacy_fallback():
    with _env(TAVUS_ACCOUNTS=None, TAVUS_API_KEY="legacy-key"):
        assert get_accounts() == [("primary", "legacy-key")]


def test_accounts_helpers():
    with _env(TAVUS_ACCOUNTS="primary:key-aaa,backup1:key-bbb"):
        assert primary_key() == "key-aaa"
        assert get_key("backup1") == "key-bbb"
        assert get_key("nonexistent") == ""


# ── Replica resolution ───────────────────────────────────────────────────────

def test_stock_replica_passes_through_on_every_account():
    # Raj (stock) — global id, identical on all accounts
    assert resolve_replica("r18e9aebdc33", "primary") == "r18e9aebdc33"
    assert resolve_replica("r18e9aebdc33", "backup1") == "r18e9aebdc33"


def test_custom_replicas_translate_per_account():
    assert resolve_replica("r7e17b3844d4", "primary") == "r7e17b3844d4"   # Jai
    assert resolve_replica("r7e17b3844d4", "backup1") == "rfd9f877a04e"
    assert resolve_replica("r84698ecbd7a", "backup1") == "rdd660498c3e"   # Shailaja
    assert resolve_replica("rdba02690e60", "backup1") == "r4548f71589a"   # Vikas


def test_backup_id_resolves_too():
    # Reverse index covers EVERY account's ids, not just primary's — a backup
    # id arriving (e.g. thumb proxy after failover) still maps correctly.
    assert resolve_replica("rfd9f877a04e", "primary") == "r7e17b3844d4"


def test_missing_account_mapping_raises():
    try:
        resolve_replica("r7e17b3844d4", "backup99")
        assert False, "expected LookupError"
    except LookupError as e:
        assert "backup99" in str(e)


def test_owning_account():
    assert owning_account("r7e17b3844d4") == "primary"
    assert owning_account("rfd9f877a04e") == "backup1"
    assert owning_account("r18e9aebdc33") == ""          # stock → any account


def test_registry_has_no_duplicate_ids():
    ids = [rid for accts in CUSTOM_AVATARS.values() for rid in accts.values()]
    assert len(ids) == len(set(ids))


# ── Failover classification ──────────────────────────────────────────────────

def test_limit_statuses_failover():
    for status in (402, 403, 429, 401):
        assert should_failover(_FakeHTTPError(status)) is True


def test_400_concurrency_cap_fails_over():
    err = _FakeHTTPError(400, "User has reached maximum concurrent conversations")
    assert should_failover(err) is True


def test_400_credit_exhaustion_fails_over():
    assert should_failover(_FakeHTTPError(400, "Insufficient credits remaining")) is True


def test_400_validation_error_does_not_cascade():
    err = _FakeHTTPError(400, "Bad Request. {'_schema': ['replica_id invalid']}")
    assert should_failover(err) is False


def test_5xx_and_network_errors_do_not_cascade():
    assert should_failover(_FakeHTTPError(500, "internal error")) is False
    assert should_failover(ValueError("connection reset")) is False
