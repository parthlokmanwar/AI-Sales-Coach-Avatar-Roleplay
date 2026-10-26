"""
Multi-account Tavus support — ordered API-key fallback + custom-replica registry.

WHY THIS EXISTS
───────────────
Tavus limits (concurrent conversations, credits) are per ACCOUNT, and custom
(user-trained) replicas are locked to the account that trained them. Stock
replicas share the same global ID on every account. So a proper fallback must
switch the whole ACCOUNT BUNDLE — the API key AND the custom-replica IDs —
together, never a bare key.

The frontend keeps sending the PRIMARY account's replica IDs (nothing in the UI
changes). This module translates any known custom ID to the equivalent replica
on whichever account is being tried. Stock IDs pass through untouched.

ADDING A FUTURE ACCOUNT
───────────────────────
1. Retrain each custom avatar on the new Tavus account.
2. Add its column to CUSTOM_AVATARS below.
3. Append `name:api_key` to TAVUS_ACCOUNTS in .env. Done — no frontend change.
"""

import os
import logging

import requests

log = logging.getLogger("hdfc")

# ── Custom-replica registry ──────────────────────────────────────────────────
# Per-account replica IDs for CUSTOM (user-trained) avatars only.
# Mapping verified 2026-07-14 against both accounts' "Your Faces" dashboards
# (visual face match, confirmed via fetch_replicas on each account).
# Stock replicas are NOT listed here — their IDs are global across accounts.
CUSTOM_AVATARS: dict[str, dict[str, str]] = {
    # older male, light-blue shirt, bookshelf/lamp background
    "vikas":    {"primary": "rdba02690e60", "backup1": "r4548f71589a"},
    # female, dark blazer, office with orange chair
    "shailaja": {"primary": "r84698ecbd7a", "backup1": "rdd660498c3e"},
    # young bearded male with lanyard, glass-wall office
    "jai":      {"primary": "r7e17b3844d4", "backup1": "rfd9f877a04e"},
}

# Reverse index: ANY account's custom replica ID → logical avatar key.
_ID_TO_KEY: dict[str, str] = {
    rid: key
    for key, accounts in CUSTOM_AVATARS.items()
    for rid in accounts.values()
}


# ── Account configuration ────────────────────────────────────────────────────

def get_accounts() -> list[tuple[str, str]]:
    """Ordered [(name, api_key), ...] — first entry is the primary.

    TAVUS_ACCOUNTS="primary:key1,backup1:key2" wins; if unset, falls back to
    the legacy single TAVUS_API_KEY (named "primary") so existing deployments
    keep working unchanged.
    """
    accounts: list[tuple[str, str]] = []
    raw = os.getenv("TAVUS_ACCOUNTS", "").strip()
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                name, key = part.split(":", 1)
            else:
                name, key = f"account{len(accounts) + 1}", part
            name = name.strip()
            key = key.strip().strip('"').strip("'")
            if name and key:
                accounts.append((name, key))
    if not accounts:
        legacy = os.getenv("TAVUS_API_KEY", "").strip().strip('"').strip("'")
        if legacy:
            accounts.append(("primary", legacy))
    return accounts


def get_key(account: str) -> str:
    """API key for a named account, or "" if not configured."""
    for name, key in get_accounts():
        if name == account:
            return key
    return ""


def primary_key() -> str:
    """API key of the first configured account (for account-agnostic reads
    like listing the avatar gallery)."""
    accounts = get_accounts()
    return accounts[0][1] if accounts else ""


# ── Replica resolution ───────────────────────────────────────────────────────

def resolve_replica(replica_id: str, account: str) -> str:
    """Translate a replica ID (as sent by the frontend — canonically the
    primary account's ID) to the equivalent replica on `account`.

    Stock replicas: returned unchanged (IDs are global).
    Custom replicas: looked up in CUSTOM_AVATARS; raises LookupError when the
    avatar was never trained on that account so the caller can skip it.
    """
    key = _ID_TO_KEY.get(replica_id)
    if key is None:
        return replica_id  # stock replica — same ID on every account
    mapped = CUSTOM_AVATARS[key].get(account, "")
    if not mapped:
        raise LookupError(
            f"Custom avatar '{key}' has no replica trained on account '{account}'"
        )
    return mapped


def owning_account(replica_id: str) -> str:
    """Account name that owns a CUSTOM replica ID, or "" for stock/unknown IDs
    (which any account can serve)."""
    key = _ID_TO_KEY.get(replica_id)
    if key is None:
        return ""
    for account, rid in CUSTOM_AVATARS[key].items():
        if rid == replica_id:
            return account
    return ""


# ── Failover error classification ────────────────────────────────────────────

# 400 bodies that mean "this account is exhausted", not "your request is bad".
# Tavus's documented limit error is:  "User has reached maximum concurrent
# conversations". Credit/quota wording is matched defensively.
_LIMIT_MARKERS = ("maximum concurrent", "credit", "quota", "rate limit", "out of")


def should_failover(exc: Exception) -> bool:
    """True when the error means THIS ACCOUNT can't serve the request and the
    next account should be tried; False for errors failover can't fix
    (validation bugs, Tavus 5xx outages) so they surface immediately.

    - 402 / 403 / 429  → payment, plan, or rate limit → switch account
    - 401              → bad/revoked key → this account is unusable → switch
                         (logged loudly: it's a config problem, not a quota)
    - 400 + limit text → concurrency/credit cap → switch account
    - anything else    → don't cascade across accounts
    """
    resp = getattr(exc, "response", None)
    if resp is None:
        return False  # network error etc. — likely affects every account
    status = getattr(resp, "status_code", 0)
    if status in (402, 403, 429):
        return True
    if status == 401:
        log.error("[TAVUS-ACCOUNTS] API key rejected (401) — check TAVUS_ACCOUNTS")
        return True
    if status == 400:
        try:
            body = resp.text.lower()
        except Exception:
            return False
        return any(marker in body for marker in _LIMIT_MARKERS)
    return False


# ── Health check ─────────────────────────────────────────────────────────────

def check_accounts_health() -> dict:
    """Verify every configured account is reachable and owns the custom
    replicas mapped to it in CUSTOM_AVATARS. Sync (uses requests directly);
    call via asyncio.to_thread from async code.

    Returns {account: {"ok": bool, "missing": [...], "error": str|None}}.
    """
    results: dict[str, dict] = {}
    for account, api_key in get_accounts():
        expected = {
            rid for accounts in CUSTOM_AVATARS.values()
            for acct, rid in accounts.items() if acct == account
        }
        try:
            r = requests.get(
                "https://tavusapi.com/v2/replicas",
                headers={"x-api-key": api_key},
                params={"verbose": "false"},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            items = data if isinstance(data, list) else (
                data.get("data") or data.get("replicas") or [])
            have = {rep.get("replica_id") or rep.get("id", "") for rep in items}
            missing = sorted(expected - have)
            results[account] = {"ok": not missing, "missing": missing, "error": None}
            if missing:
                log.warning("[TAVUS-ACCOUNTS] account '%s' is missing mapped "
                            "custom replicas: %s", account, missing)
        except Exception as e:
            results[account] = {"ok": False, "missing": sorted(expected),
                                "error": str(e)}
            log.warning("[TAVUS-ACCOUNTS] health check failed for '%s': %s",
                        account, e)
    return results
