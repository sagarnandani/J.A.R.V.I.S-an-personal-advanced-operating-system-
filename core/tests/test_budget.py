"""Pure-function tests for cost estimation. No database or network needed."""
from decimal import Decimal

from app.budget import estimate_cost_inr
from app.config import Settings


def make_settings(**overrides) -> Settings:
    defaults = dict(
        usd_to_inr_rate=Decimal("90"),
        price_input_usd_per_1m=Decimal("3.00"),
        price_output_usd_per_1m=Decimal("15.00"),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_zero_tokens_cost_zero():
    settings = make_settings()
    assert estimate_cost_inr(0, 0, settings) == Decimal("0.000000")


def test_known_token_counts():
    settings = make_settings()
    # 1,000,000 input tokens @ $3/1M = $3.00; 1,000,000 output @ $15/1M = $15.00
    # total $18.00 * 90 INR/USD = 1620 INR
    cost = estimate_cost_inr(1_000_000, 1_000_000, settings)
    assert cost == Decimal("1620.000000")


def test_scales_linearly_with_output_tokens():
    settings = make_settings()
    small = estimate_cost_inr(0, 1000, settings)
    large = estimate_cost_inr(0, 2000, settings)
    assert large == small * 2


def test_different_price_settings_change_result():
    cheap = make_settings(price_output_usd_per_1m=Decimal("1.00"))
    expensive = make_settings(price_output_usd_per_1m=Decimal("100.00"))
    assert estimate_cost_inr(0, 1_000_000, cheap) < estimate_cost_inr(
        0, 1_000_000, expensive
    )
