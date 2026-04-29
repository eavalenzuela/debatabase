"""Unit tests for the in-memory sliding-window rate limiter.

The HTTP-level wiring (login burst, search burst, answers burst) is
exercised end-to-end by tests/test_security.py. Here we just verify
the bucket maths.
"""

from __future__ import annotations

import time

from debatabase.rate_limit import Window, _SlidingWindow


def test_under_limit_allows_all() -> None:
    w = _SlidingWindow(Window(limit=5, period=60.0, name="t"))
    for _ in range(5):
        ok, retry = w.hit("k")
        assert ok is True
        assert retry == 0.0


def test_over_limit_returns_retry_after() -> None:
    w = _SlidingWindow(Window(limit=3, period=60.0, name="t"))
    for _ in range(3):
        assert w.hit("k")[0] is True
    ok, retry = w.hit("k")
    assert ok is False
    # Retry-after should be < period and > 0.
    assert 0 < retry <= 60.0


def test_keys_are_isolated() -> None:
    w = _SlidingWindow(Window(limit=2, period=60.0, name="t"))
    assert w.hit("a")[0] is True
    assert w.hit("a")[0] is True
    assert w.hit("a")[0] is False
    # Other key still has full budget.
    assert w.hit("b")[0] is True
    assert w.hit("b")[0] is True
    assert w.hit("b")[0] is False


def test_window_slides() -> None:
    w = _SlidingWindow(Window(limit=2, period=0.1, name="t"))
    assert w.hit("k")[0] is True
    assert w.hit("k")[0] is True
    assert w.hit("k")[0] is False
    time.sleep(0.12)
    # After the period, both old timestamps are stale, so two more fit.
    assert w.hit("k")[0] is True
    assert w.hit("k")[0] is True
    assert w.hit("k")[0] is False
