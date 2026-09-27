"""Token accounting (#23): one meaning of Usage across providers, and cache-aware pricing."""

from types import SimpleNamespace

from hoi4_harness.agent.budget import BudgetGuard
from hoi4_harness.agent.llm import anthropic_provider, google_provider, openai_provider
from hoi4_harness.agent.llm.base import Usage
from hoi4_harness.config import BudgetConfig


def test_anthropic_input_sums_uncached_reads_and_writes():
    raw = SimpleNamespace(input_tokens=200, cache_read_input_tokens=1500,
                          cache_creation_input_tokens=300, output_tokens=90)
    usage = anthropic_provider.usage_from_wire(raw)
    assert usage == Usage(input_tokens=2000, output_tokens=90,
                          cached_input_tokens=1500, cache_write_input_tokens=300)
    assert usage.uncached_input_tokens == 200


def test_anthropic_without_caching_fields_reads_as_uncached():
    usage = anthropic_provider.usage_from_wire(SimpleNamespace(input_tokens=50, output_tokens=5))
    assert usage == Usage(50, 5, 0, 0)


def test_openai_prompt_tokens_already_include_the_cached_part():
    raw = SimpleNamespace(prompt_tokens=2000, completion_tokens=120,
                          prompt_tokens_details=SimpleNamespace(cached_tokens=1500))
    usage = openai_provider.usage_from_wire(raw)
    assert usage == Usage(2000, 120, 1500, 0)


def test_a_local_endpoint_without_details_reads_as_nothing_cached():
    raw = SimpleNamespace(prompt_tokens=10, completion_tokens=5, prompt_tokens_details=None)
    assert openai_provider.usage_from_wire(raw) == Usage(10, 5, 0, 0)
    assert openai_provider.usage_from_wire(None) == Usage()


def test_gemini_thinking_tokens_bill_as_output():
    meta = SimpleNamespace(prompt_token_count=2000, candidates_token_count=100,
                           thoughts_token_count=400, cached_content_token_count=1024)
    assert google_provider.usage_from_wire(meta) == Usage(2000, 500, 1024, 0)


def test_cache_reads_and_writes_are_priced_at_their_own_rates():
    guard = BudgetGuard(BudgetConfig(usd_per_m_input=3.0, usd_per_m_output=15.0,
                                     usd_per_m_cached_input=0.3, usd_per_m_cache_write=3.75))
    usage = Usage(input_tokens=1_000_000 + 2_000_000 + 1_000_000, output_tokens=1_000_000,
                  cached_input_tokens=2_000_000, cache_write_input_tokens=1_000_000)
    # 1M uncached at 3 + 2M reads at 0.3 + 1M writes at 3.75 + 1M out at 15
    assert abs(guard.price(usage) - (3.0 + 0.6 + 3.75 + 15.0)) < 1e-9


def test_unset_cache_rates_fall_back_to_the_input_rate_not_to_free():
    guard = BudgetGuard(BudgetConfig(usd_per_m_input=2.0))
    usage = Usage(input_tokens=1_000_000, cached_input_tokens=1_000_000)
    assert guard.price(usage) == 2.0


def test_the_summary_says_whether_spend_was_priced():
    assert BudgetGuard(BudgetConfig()).summary()["priced"] is False
    assert BudgetGuard(BudgetConfig(usd_per_m_input=1.0)).summary()["priced"] is True


def test_doctor_warns_when_a_real_provider_runs_unpriced(capsys, monkeypatch):
    from hoi4_harness.cli import main

    for name in ("HOI4_USD_PER_M_INPUT", "HOI4_USD_PER_M_OUTPUT"):
        monkeypatch.delenv(name, raising=False)
    main(["doctor", "--provider", "anthropic"])
    assert "pricing          UNSET" in capsys.readouterr().out


def test_resume_carries_cache_writes_forward(tmp_path):
    import json

    from hoi4_harness.agent.resume import rebuild

    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({
        "kind": "llm",
        "usage": {"input_tokens": 100, "output_tokens": 5, "cached_input_tokens": 60,
                  "cache_write_input_tokens": 30},
    }) + "\n", encoding="utf-8")
    assert rebuild(path).usage.cache_write_input_tokens == 30
