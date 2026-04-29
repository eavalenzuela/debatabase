"""In-memory sliding-window rate limiter.

Built for the cost-bearing endpoints — `/search` (Voyage embed) and
`/cards/{id}/answers` (Anthropic Haiku + Voyage embed) — and for
brute-force defence on `/login` + `/register`.

Single-process by design: the buckets live in this module's globals and
are not shared across workers. The deployment is one uvicorn process
on one EC2 instance, so this is sufficient. If we ever scale to >1
worker, swap the backend for Redis; the public surface
(``check`` / ``make_dependency``) stays the same.

Trusted-proxy handling follows ``PATTERNS.md`` #7: ``X-Forwarded-For`` is
honoured only when the direct peer falls inside one of the configured
``TRUSTED_PROXIES`` CIDRs. Default is empty → headers ignored, peer
address used. Operators put the load balancer's CIDR there once TLS
termination lives in front of uvicorn.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException, Request

from debatabase.config import settings


_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_trusted_proxies(raw: str) -> list[_IPNetwork]:
    nets: list[_IPNetwork] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return nets


_TRUSTED_PROXIES = _parse_trusted_proxies(settings.trusted_proxies)


def client_ip(request: Request) -> str:
    """Best-effort client IP. Honours ``X-Forwarded-For`` only when the
    direct peer is in ``TRUSTED_PROXIES``."""
    direct = request.client.host if request.client else "0.0.0.0"
    if not _TRUSTED_PROXIES:
        return direct
    try:
        peer = ipaddress.ip_address(direct)
    except ValueError:
        return direct
    if not any(peer in net for net in _TRUSTED_PROXIES):
        return direct
    xff = request.headers.get("x-forwarded-for", "")
    first = xff.split(",")[0].strip()
    if not first:
        return direct
    try:
        ipaddress.ip_address(first)
    except ValueError:
        return direct
    return first


@dataclass(frozen=True)
class Window:
    """A (limit, period) pair. ``limit`` requests allowed per ``period`` seconds."""

    limit: int
    period: float
    name: str


class _SlidingWindow:
    """One bucket per key. Each bucket is a deque of monotonic timestamps.

    O(1) amortised per hit; sweeps idle keys every 60s so the dict
    doesn't grow unbounded.
    """

    def __init__(self, window: Window):
        self.window = window
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._next_sweep = 0.0

    def hit(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        cutoff = now - self.window.period
        with self._lock:
            q = self._buckets[key]
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.window.limit:
                retry = q[0] + self.window.period - now
                return False, max(retry, 0.0)
            q.append(now)
            self._maybe_sweep(now)
            return True, 0.0

    def _maybe_sweep(self, now: float) -> None:
        if now < self._next_sweep:
            return
        self._next_sweep = now + 60.0
        cutoff = now - self.window.period
        stale = [k for k, q in self._buckets.items() if not q or q[-1] <= cutoff]
        for k in stale:
            del self._buckets[k]


# ---------------------------------------------------------------------------
# Per-endpoint windows.
#
# Sized for human use, not script use. Numbers are deliberately loose —
# the goal is "stop a runaway loop or a malicious cost-spike," not "shave
# the bill." Tighten if abuse is observed.
# ---------------------------------------------------------------------------

# /search semantic: Voyage embed_query per call. Burst 30/min, sustain 300/hr.
SEARCH_BURST = _SlidingWindow(Window(limit=30, period=60.0, name="search_burst"))
SEARCH_HOURLY = _SlidingWindow(Window(limit=300, period=3600.0, name="search_hourly"))

# /cards/{id}/answers: Haiku + Voyage embed per call (~20× cost of search).
ANSWERS_BURST = _SlidingWindow(Window(limit=10, period=60.0, name="answers_burst"))
ANSWERS_HOURLY = _SlidingWindow(Window(limit=80, period=3600.0, name="answers_hourly"))

# Auth endpoints: brute-force defence. Per-IP only (per-account would let
# an attacker lock out a target by failing logins for them).
LOGIN_BURST = _SlidingWindow(Window(limit=10, period=60.0, name="login_burst"))
LOGIN_HOURLY = _SlidingWindow(Window(limit=60, period=3600.0, name="login_hourly"))

REGISTER_BURST = _SlidingWindow(Window(limit=5, period=60.0, name="register_burst"))
REGISTER_HOURLY = _SlidingWindow(Window(limit=20, period=3600.0, name="register_hourly"))


def _key_for(request: Request, prefix: str, *, ip_only: bool = False) -> str:
    """Build a rate-limit key.

    Authenticated users are bucketed per-user (cheaper to be generous to
    real users than risk a shared-NAT IP starving them). Anonymous
    requests fall back to client IP. ``ip_only`` forces IP keying for
    pre-auth endpoints (login, register).
    """
    if not ip_only:
        uid = request.session.get("user_id")
        if uid is not None:
            return f"{prefix}:u:{uid}"
    return f"{prefix}:ip:{client_ip(request)}"


def check(
    request: Request,
    *windows: _SlidingWindow,
    prefix: str,
    ip_only: bool = False,
) -> None:
    """Raise HTTPException 429 if any window is exhausted for this caller."""
    key = _key_for(request, prefix, ip_only=ip_only)
    worst_retry = 0.0
    for w in windows:
        ok, retry = w.hit(key)
        if not ok:
            worst_retry = max(worst_retry, retry)
    if worst_retry > 0.0:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded; slow down and try again shortly",
            headers={"Retry-After": str(int(worst_retry) + 1)},
        )


def make_dependency(
    *windows: _SlidingWindow,
    prefix: str,
    ip_only: bool = False,
) -> Callable[[Request], None]:
    """FastAPI dependency factory: returns a dep that enforces ``windows``."""

    def dep(request: Request) -> None:
        check(request, *windows, prefix=prefix, ip_only=ip_only)

    return dep


# Convenience deps: one per protected endpoint.
search_rate_limit = make_dependency(
    SEARCH_BURST, SEARCH_HOURLY, prefix="search"
)
answers_rate_limit = make_dependency(
    ANSWERS_BURST, ANSWERS_HOURLY, prefix="answers"
)
login_rate_limit = make_dependency(
    LOGIN_BURST, LOGIN_HOURLY, prefix="login", ip_only=True
)
register_rate_limit = make_dependency(
    REGISTER_BURST, REGISTER_HOURLY, prefix="register", ip_only=True
)
