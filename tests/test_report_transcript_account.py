"""
Regression tests for the ACCOUNT-AWARE report transcript fetch
(core/report_builder.fetch_tavus_transcript).

THE BUG THIS LOCKS DOWN
───────────────────────
RolePlay launches fall over to the next configured Tavus account when the
primary is at its credit/concurrency cap. Conversations belong to the account
that created them, and asking account A for account B's conversation returns
400 — indistinguishable from "not ready yet". The report fetched the transcript
with the PRIMARY key only, so every call served by a fallback account produced
"Report not ready" forever.

Run:  python -m pytest tests/test_report_transcript_account.py -q
"""

import os
import asyncio
import contextlib

import core.report_builder as rb


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


TWO_ACCOUNTS = dict(TAVUS_ACCOUNTS="primary:key-A,backup1:key-B", TAVUS_API_KEY=None)

# A conversation that lives ONLY on backup1 (key-B).
_ON_BACKUP1 = {"transcript": [
    {"role": "user", "content": "Hello, am I speaking with Rajesh?"},
    {"role": "assistant", "content": "Yes, who is this?"},
]}


def _fake_fetch(owner_key, payload=_ON_BACKUP1, calls=None):
    """Stand-in for the Tavus GET: returns the payload only for `owner_key`,
    None (Tavus 400/404) for every other key — exactly like the real API."""
    def _inner(api_key, session_id):
        if calls is not None:
            calls.append(api_key)
        return payload if api_key == owner_key else None
    return _inner


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_transcript_found_on_fallback_account(monkeypatch=None):
    """THE REGRESSION: no hint (backend restarted) → still swept and found."""
    calls = []
    orig = rb._fetch_conversation_sync
    rb._fetch_conversation_sync = _fake_fetch("key-B", calls=calls)
    try:
        with _env(**TWO_ACCOUNTS):
            t = _run(rb.fetch_tavus_transcript("c123"))
    finally:
        rb._fetch_conversation_sync = orig
    assert "Rep: Hello, am I speaking with Rajesh?" in t
    assert "Customer: Yes, who is this?" in t
    assert calls == ["key-A", "key-B"]          # primary tried first, then swept


def test_preferred_account_is_tried_first():
    """The launch-time hint avoids a wasted round-trip to the wrong account."""
    calls = []
    orig = rb._fetch_conversation_sync
    rb._fetch_conversation_sync = _fake_fetch("key-B", calls=calls)
    try:
        with _env(**TWO_ACCOUNTS):
            t = _run(rb.fetch_tavus_transcript("c123", preferred_account="backup1"))
    finally:
        rb._fetch_conversation_sync = orig
    assert t
    assert calls == ["key-B"]                   # owner hit directly, no sweep


def test_stale_hint_still_falls_through():
    """A wrong hint (registry lost/rotated) must not break the fetch."""
    orig = rb._fetch_conversation_sync
    rb._fetch_conversation_sync = _fake_fetch("key-B")
    try:
        with _env(**TWO_ACCOUNTS):
            t = _run(rb.fetch_tavus_transcript("c123", preferred_account="primary"))
    finally:
        rb._fetch_conversation_sync = orig
    assert t


def test_unknown_conversation_raises_recoverable_valueerror():
    """Not on ANY account → ValueError (router turns this into a 404 the UI retries)."""
    orig = rb._fetch_conversation_sync
    rb._fetch_conversation_sync = _fake_fetch("key-NOBODY")
    try:
        with _env(**TWO_ACCOUNTS):
            try:
                _run(rb.fetch_tavus_transcript("c404"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "Could not find a completed call" in str(e)
    finally:
        rb._fetch_conversation_sync = orig


def test_owner_found_but_transcript_empty_is_distinguished():
    """Owner found, Tavus still processing → the 'no transcript yet' message,
    NOT the 'could not find the call' one. Different cause, different fix."""
    orig = rb._fetch_conversation_sync
    rb._fetch_conversation_sync = _fake_fetch("key-B", payload={"status": "ended"})
    try:
        with _env(**TWO_ACCOUNTS):
            try:
                _run(rb.fetch_tavus_transcript("c123"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "no transcript for this session yet" in str(e)
    finally:
        rb._fetch_conversation_sync = orig


def test_legacy_single_key_deployment_still_works():
    """TAVUS_ACCOUNTS unset → legacy TAVUS_API_KEY, exactly as before."""
    orig = rb._fetch_conversation_sync
    rb._fetch_conversation_sync = _fake_fetch("legacy-key")
    try:
        with _env(TAVUS_ACCOUNTS=None, TAVUS_API_KEY="legacy-key"):
            t = _run(rb.fetch_tavus_transcript("c123"))
    finally:
        rb._fetch_conversation_sync = orig
    assert "Rep: Hello" in t


def test_no_account_configured_is_a_clear_error():
    orig = rb._fetch_conversation_sync
    rb._fetch_conversation_sync = _fake_fetch("nope")
    try:
        with _env(TAVUS_ACCOUNTS=None, TAVUS_API_KEY=None):
            try:
                _run(rb.fetch_tavus_transcript("c123"))
                raise AssertionError("expected ValueError")
            except ValueError as e:
                assert "No Tavus account configured" in str(e)
    finally:
        rb._fetch_conversation_sync = orig


def test_report_router_remembers_account_across_end():
    """/end tears down the reaper session; the report still needs the account.
    _conv_account must OUTLIVE _unregister_session."""
    import features.roleplay as rp
    cid = "c-sticky-1"
    rp._register_session(cid, account="backup1")
    rp._remember_account(cid, "backup1")
    rp._unregister_session(cid)                      # what POST /end does
    assert cid not in rp._sessions
    assert rp._conv_account.get(cid) == "backup1"
    rp._conv_account.pop(cid, None)


def test_conv_account_map_is_bounded():
    """It must never grow without limit in a long-running backend."""
    import features.roleplay as rp
    before = dict(rp._conv_account)
    rp._conv_account.clear()
    for i in range(rp._CONV_ACCOUNT_MAX + 50):
        rp._remember_account(f"c{i}", "primary")
    assert len(rp._conv_account) == rp._CONV_ACCOUNT_MAX
    assert "c0" not in rp._conv_account                    # oldest evicted
    assert f"c{rp._CONV_ACCOUNT_MAX + 49}" in rp._conv_account   # newest kept
    rp._conv_account.clear()
    rp._conv_account.update(before)
