"""限流器单元测试"""
import pytest
from src.utils.rate_limiter import ServiceRateLimiter


@pytest.fixture
def limiter():
    return ServiceRateLimiter(max_per_minute=2, max_per_hour=5)


@pytest.mark.asyncio
async def test_allows_first_request(limiter):
    assert await limiter.check("order-service") is True


@pytest.mark.asyncio
async def test_blocks_after_minute_limit(limiter):
    await limiter.record("order-service")
    await limiter.record("order-service")
    assert await limiter.check("order-service") is False


@pytest.mark.asyncio
async def test_different_services_independent(limiter):
    await limiter.record("order-service")
    await limiter.record("order-service")
    assert await limiter.check("order-service") is False
    assert await limiter.check("user-service") is True


@pytest.mark.asyncio
async def test_should_alert_after_threshold(limiter):
    await limiter.record("order-service")
    await limiter.record("order-service")
    for _ in range(10):
        await limiter.check("order-service")
    assert limiter.should_alert("order-service") is True


@pytest.mark.asyncio
async def test_no_alert_when_not_limited(limiter):
    await limiter.record("order-service")
    assert limiter.should_alert("order-service") is False
