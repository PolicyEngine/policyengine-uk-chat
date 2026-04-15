from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.billing import calculate_cost_gbp


def test_haiku_is_cheaper_than_sonnet_for_same_usage():
    usage = {
        "input_tokens": 50_000,
        "output_tokens": 2_000,
        "cache_creation_input_tokens": 10_000,
        "cache_read_input_tokens": 10_000,
    }
    haiku_cost = calculate_cost_gbp(model="claude-haiku-4-5", **usage)
    sonnet_cost = calculate_cost_gbp(model="claude-sonnet-4-6", **usage)
    assert haiku_cost < sonnet_cost


def test_cache_tokens_contribute_to_cost():
    baseline = calculate_cost_gbp(
        model="claude-haiku-4-5",
        input_tokens=10_000,
        output_tokens=500,
    )
    with_cache = calculate_cost_gbp(
        model="claude-haiku-4-5",
        input_tokens=10_000,
        output_tokens=500,
        cache_creation_input_tokens=5_000,
        cache_read_input_tokens=5_000,
    )
    assert with_cache > baseline
