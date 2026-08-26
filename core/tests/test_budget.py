"""Cost estimation. Pure functions -- no database or network needed."""
from decimal import Decimal

import pytest

from app.budget import estimate_cost_inr, provider_rates
from app.config import Settings


def make_settings(**overrides) -> Settings:
    defaults = dict(
        usd_to_inr_rate=Decimal("90"),
        price_claude_input_usd_per_1m=Decimal("2.00"),
        price_claude_output_usd_per_1m=Decimal("10.00"),
        price_gemini_input_usd_per_1m=Decimal("0.00"),
        price_gemini_output_usd_per_1m=Decimal("0.00"),
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_zero_tokens_cost_zero():
    assert estimate_cost_inr(0, 0, make_settings(), provider="claude") == Decimal("0.000000")


def test_known_token_counts_for_claude():
    # 1M input @ $2 = $2.00; 1M output @ $10 = $10.00; $12 * 90 = 1080 INR
    cost = estimate_cost_inr(1_000_000, 1_000_000, make_settings(), provider="claude")
    assert cost == Decimal("1080.000000")


def test_gemini_free_tier_costs_nothing():
    cost = estimate_cost_inr(1_000_000, 1_000_000, make_settings(), provider="gemini")
    assert cost == Decimal("0.000000")


def test_gemini_priced_when_billing_is_enabled():
    """If the free tier stops applying, setting real rates must start
    charging -- the zeros are a default, not a hardcoded assumption."""
    paid = make_settings(
        price_gemini_input_usd_per_1m=Decimal("1.25"),
        price_gemini_output_usd_per_1m=Decimal("10.00"),
    )
    cost = estimate_cost_inr(1_000_000, 1_000_000, paid, provider="gemini")
    assert cost == Decimal("1012.500000")


def test_scales_linearly_with_output_tokens():
    s = make_settings()
    assert estimate_cost_inr(0, 2000, s, provider="claude") == estimate_cost_inr(
        0, 1000, s, provider="claude"
    ) * 2


def test_provider_is_priced_independently():
    """The bug this guards: billing a fallback answer at the primary's
    rates. Same tokens, different provider, different cost."""
    s = make_settings()
    claude = estimate_cost_inr(1000, 1000, s, provider="claude")
    gemini = estimate_cost_inr(1000, 1000, s, provider="gemini")
    assert claude > gemini


def test_unknown_provider_and_mock_cost_nothing():
    s = make_settings()
    assert estimate_cost_inr(9999, 9999, s, provider="mock") == Decimal("0.000000")
    assert provider_rates("something-else", s) == (Decimal("0"), Decimal("0"))


@pytest.mark.parametrize("provider", ["claude", "gemini"])
def test_price_settings_actually_take_effect(provider):
    """Regression guard: the previous version of this test set price fields
    that had been renamed. Pydantic ignored the unknown names, both sides
    silently fell back to defaults, and the test compared a value to
    itself. Assert against a known number instead of against another
    computed value."""
    s = make_settings(
        **{
            f"price_{provider}_input_usd_per_1m": Decimal("0.00"),
            f"price_{provider}_output_usd_per_1m": Decimal("100.00"),
        }
    )
    # 1M output @ $100 = $100 * 90 INR = 9000
    assert estimate_cost_inr(0, 1_000_000, s, provider=provider) == Decimal("9000.000000")
