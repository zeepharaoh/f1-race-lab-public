from concurrent.futures import ThreadPoolExecutor

import hosting
from hosting import RefreshGate


def test_requests_share_cooldown_without_extending_it():
    gate = RefreshGate()
    assert gate.request(now=0) == 0
    assert gate.request(now=0) == 60
    assert gate.request(now=20) == 40
    assert gate.request(now=59.9) == 1
    assert gate.request(now=60) == 0
    assert gate.request(now=61) == 59


def test_custom_interval_rounds_remaining_seconds_up():
    gate = RefreshGate(interval=5.5)
    assert gate.request(now=100) == 0
    assert gate.request(now=100.1) == 6
    assert gate.request(now=105.49) == 1
    assert gate.request(now=105.5) == 0


def test_default_clock_uses_monotonic_time(monkeypatch):
    times = iter([100, 100.1, 160])
    monkeypatch.setattr(hosting, 'monotonic', lambda: next(times))
    gate = RefreshGate()
    assert gate.request() == 0
    assert gate.request() == 60
    assert gate.request() == 0


def test_concurrent_requests_only_allow_one_refresh():
    gate = RefreshGate()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: gate.request(now=0), range(32)))
    assert results.count(0) == 1
    assert results.count(60) == 31
