"""Focused unit tests for security-critical helpers.

End-to-end HTTP coverage of auth, rate limiting, and workspace scoping
lives in the security validation findings doc — those flows touch the
DB and aren't covered here. This file exercises the pure helpers so a
regression flips a red CI signal without needing a Postgres.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from debatabase.auth import MIN_PASSWORD_LEN, validate_password
from debatabase.web.app import _safe_next


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Same-origin paths pass through.
        ("/workspaces", "/workspaces"),
        ("/workspaces/12", "/workspaces/12"),
        ("/search?q=foo", "/search?q=foo"),
        # Empty / None default.
        (None, "/workspaces"),
        ("", "/workspaces"),
        # Open-redirect blockers.
        ("https://evil.example", "/workspaces"),
        ("http://evil.example/path", "/workspaces"),
        ("//evil.example", "/workspaces"),  # protocol-relative
        ("/\\evil.example", "/workspaces"),  # back-slash variant
        ("javascript:alert(1)", "/workspaces"),
        ("evil.example", "/workspaces"),  # no leading slash
    ],
)
def test_safe_next_blocks_open_redirect(raw: str | None, expected: str) -> None:
    assert _safe_next(raw) == expected


# ---------------------------------------------------------------------------
# REQ-AUTH-02: password minimum length
# ---------------------------------------------------------------------------

def test_validate_password_rejects_below_minimum() -> None:
    for short in ("", "a", "1234567"):
        err = validate_password(short)
        assert err is not None
        assert str(MIN_PASSWORD_LEN) in err


def test_validate_password_accepts_minimum_and_above() -> None:
    assert validate_password("a" * MIN_PASSWORD_LEN) is None
    assert validate_password("a" * (MIN_PASSWORD_LEN + 50)) is None


# ---------------------------------------------------------------------------
# REQ-INPUT-02: structural test that every f-string HTMLResponse with
# user-controlled interpolation wraps the value in html.escape().
#
# Allowlist: pure-int interpolations (path params declared as int are
# safe — FastAPI returns 422 before the handler runs on non-ints). The
# test parses each match in web/app.py and fails on any unescaped name
# that isn't in the int-only allowlist.
# ---------------------------------------------------------------------------

_APP_PY = Path(__file__).resolve().parents[1] / "src" / "debatabase" / "web" / "app.py"

# Names whose handler signatures pin them to int (and therefore can't carry HTML).
_INT_ONLY_NAMES = {
    "card_id", "source_id", "ws_id", "entry_id", "tag_id",
    "canonical_id", "user_id",
}

# Non-text types that don't need escaping (formatted len/count of an int list).
_SAFE_EXPR_PREFIXES = ("len(", "max(", "min(")


def _interpolations_in_fstring(literal: str) -> list[str]:
    """Return the raw expressions inside `{...}` of an f-string body."""
    return re.findall(r"\{([^{}]+)\}", literal)


def test_no_unescaped_user_input_in_html_fstrings() -> None:
    src = _APP_PY.read_text()
    # Find every f-string passed (directly or as the first positional arg) to HTMLResponse.
    pattern = re.compile(r"HTMLResponse\(\s*((?:f\"[^\"]*\"|f'[^']*'|\s)+)", re.MULTILINE)

    offenders: list[str] = []
    for match in pattern.finditer(src):
        body = match.group(1)
        # Pull out each individual f-string literal and inspect its placeholders.
        for lit in re.findall(r"f\"([^\"]*)\"|f'([^']*)'", body):
            literal = lit[0] or lit[1]
            for expr in _interpolations_in_fstring(literal):
                expr = expr.strip()
                # Strip a format spec if any (`x:.6f` → `x`).
                head = expr.split(":", 1)[0].split("!", 1)[0].strip()
                if head.startswith("html_escape("):
                    continue
                if head in _INT_ONLY_NAMES:
                    continue
                if any(head.startswith(p) for p in _SAFE_EXPR_PREFIXES):
                    continue
                offenders.append(
                    f"{_APP_PY.name}: HTMLResponse f-string interpolates "
                    f"`{head}` without html_escape() — wrap it or extend "
                    f"the allowlist if it's structurally non-textual."
                )

    assert not offenders, "\n".join(offenders)


# ---------------------------------------------------------------------------
# REQ-NET-02: X-Forwarded-For only honoured from trusted proxies
# (PATTERNS.md #7). Patches `_TRUSTED_PROXIES` to bypass the
# import-time settings read.
# ---------------------------------------------------------------------------

def _make_request(*, peer: str, xff: str | None) -> object:
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return type(
        "Req",
        (),
        {
            "client": type("C", (), {"host": peer})(),
            "headers": headers,
        },
    )()


def test_client_ip_ignores_xff_when_no_trusted_proxies(monkeypatch) -> None:
    from debatabase import rate_limit

    monkeypatch.setattr(rate_limit, "_TRUSTED_PROXIES", [])
    req = _make_request(peer="203.0.113.5", xff="198.51.100.9")
    assert rate_limit.client_ip(req) == "203.0.113.5"


def test_client_ip_honours_xff_only_from_trusted_peer(monkeypatch) -> None:
    import ipaddress

    from debatabase import rate_limit

    monkeypatch.setattr(
        rate_limit,
        "_TRUSTED_PROXIES",
        [ipaddress.ip_network("10.0.0.0/8")],
    )
    # Trusted peer → XFF respected.
    trusted_req = _make_request(peer="10.1.2.3", xff="198.51.100.9")
    assert rate_limit.client_ip(trusted_req) == "198.51.100.9"
    # Untrusted peer with the same XFF → XFF ignored.
    untrusted_req = _make_request(peer="203.0.113.5", xff="198.51.100.9")
    assert rate_limit.client_ip(untrusted_req) == "203.0.113.5"
    # Trusted peer but malformed XFF → fall back to peer.
    bad_xff_req = _make_request(peer="10.1.2.3", xff="not-an-ip")
    assert rate_limit.client_ip(bad_xff_req) == "10.1.2.3"
    # Trusted peer, no header → fall back to peer.
    no_xff_req = _make_request(peer="10.1.2.3", xff=None)
    assert rate_limit.client_ip(no_xff_req) == "10.1.2.3"
