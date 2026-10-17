import asyncio

import pytest

from rushbase_server.services.cache import TtlCache


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_values_are_cached_until_they_expire() -> None:
    clock = Clock()
    cache = TtlCache(10, clock=clock)
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert [await cache.get_or_compute("k", compute) for _ in range(3)] == [1, 1, 1]
    clock.now = 11
    assert await cache.get_or_compute("k", compute) == 2


async def test_concurrent_callers_share_one_computation() -> None:
    cache = TtlCache(10)
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "done"

    results = await asyncio.gather(*(cache.get_or_compute("k", compute) for _ in range(5)))
    assert results == ["done"] * 5
    assert calls == 1


async def test_zero_ttl_disables_caching_and_errors_are_not_cached() -> None:
    off = TtlCache(0)
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert [await off.get_or_compute("k", compute) for _ in range(2)] == [1, 2]

    cache = TtlCache(10)

    async def failing() -> int:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await cache.get_or_compute("k", failing)
    assert await cache.get_or_compute("k", compute) == 3
