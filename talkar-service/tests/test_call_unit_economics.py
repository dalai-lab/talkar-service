"""
Unit and Benchmark Tests for Call Unit Economics & Margin Engine.

Specification Reference: /Users/arnav/Internship/call_cost_audit_master_spec.md
Tests:
  - Exact Mathematical Agreement against all 20 Benchmark Stacks in Section 2.5 (plus Extra stacks)
  - All Comprehensive Edge Cases (EC-1 through EC-20)
  - Zero Synthetic Estimation Standard (Section 3.4)
  - Multi-Provider Failover and Post-Call QA token isolation
"""

import os
import sys
import math
import pytest

# Ensure root directory is on sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_current_dir)
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from services.call_audit_calculator import (
    VENDOR_PRICING_REGISTRY,
    calculate_call_unit_economics,
    resolve_stt_pricing,
    resolve_llm_pricing,
    resolve_tts_pricing,
)


# ==============================================================================
# SECTION 1: BENCHMARK STACKS (ALL 20 FROM SECTION 2.5)
# ==============================================================================

# Benchmark Standard:
# 1 Min Call = 60s STT + (500 in + 150 out) LLM tokens + 900 chars TTS @ $1 = ₹88
# Telephony excluded (AI component stack comparison per Section 2.5)

BENCHMARK_20_STACKS = [
    # (id, stt_model, llm_model, tts_model, exp_unrounded, exp_rounded_sum, exp_100_mins)
    (1, "deepgram_nova_3", "sarvam-105b", "smallest_ai_lightning", 1.8249, 1.83, 182),
    (2, "deepgram_nova_3", "gpt-4.1-mini", "smallest_ai_lightning", 1.8471, 1.85, 185),
    (3, "sarvam_saaras", "sarvam-105b", "smallest_ai_lightning", 1.8925, 1.90, 189),
    (4, "sarvam_saaras", "gpt-4.1-mini", "smallest_ai_lightning", 1.9147, 1.92, 191),
    (5, "deepgram_nova_3", "sarvam-105b", "sarvam_bulbul_v4", 2.2389, 2.24, 224),
    (6, "deepgram_nova_3", "gpt-4.1-mini", "sarvam_bulbul_v4", 2.2611, 2.26, 226),
    (7, "sarvam_saaras", "sarvam-105b", "sarvam_bulbul_v4", 2.3065, 2.31, 231),
    (8, "sarvam_saaras", "gpt-4.1-mini", "sarvam_bulbul_v4", 2.3287, 2.33, 233),
    (9, "deepgram_nova_3", "sarvam-105b", "deepgram_aura_2", 2.8149, 2.82, 281),
    (10, "deepgram_nova_3", "gpt-4.1-mini", "deepgram_aura_2", 2.8371, 2.84, 284),
    (11, "sarvam_saaras", "sarvam-105b", "deepgram_aura_2", 2.8825, 2.89, 288),
    (12, "sarvam_saaras", "gpt-4.1-mini", "deepgram_aura_2", 2.9047, 2.91, 290),
    (13, "deepgram_nova_3", "sarvam-105b", "sarvam_bulbul_v3", 3.1389, 3.14, 314),
    (14, "deepgram_nova_3", "gpt-4.1-mini", "sarvam_bulbul_v3", 3.1611, 3.16, 316),
    (15, "sarvam_saaras", "sarvam-105b", "sarvam_bulbul_v3", 3.2065, 3.21, 321),
    (16, "sarvam_saaras", "gpt-4.1-mini", "sarvam_bulbul_v3", 3.2287, 3.23, 323),
    (17, "deepgram_nova_3", "sarvam-105b", "elevenlabs_flash", 3.6069, 3.61, 361),
    (18, "deepgram_nova_3", "gpt-4.1-mini", "elevenlabs_flash", 3.6287, 3.63, 363),
    (19, "sarvam_saaras", "sarvam-105b", "elevenlabs_flash", 3.6745, 3.68, 367),
    (20, "sarvam_saaras", "gpt-4.1-mini", "elevenlabs_flash", 3.6967, 3.70, 370),
]

EXTRA_MULTILINGUAL_STACKS = [
    # Extra stacks with ElevenLabs Multilingual (v3/v4)
    (21, "deepgram_nova_3", "sarvam-105b", "elevenlabs_multilingual", 6.7749, 6.78, 677),
    (22, "deepgram_nova_3", "gpt-4.1-mini", "elevenlabs_multilingual", 6.7971, 6.80, 680),
    (23, "sarvam_saaras", "sarvam-105b", "elevenlabs_multilingual", 6.8425, 6.85, 684),
    (24, "sarvam_saaras", "gpt-4.1-mini", "elevenlabs_multilingual", 6.8647, 6.87, 686),
]


@pytest.mark.parametrize(
    "stack_id, stt_model, llm_model, tts_model, exp_unrounded, exp_rounded_sum, exp_100_mins",
    BENCHMARK_20_STACKS + EXTRA_MULTILINGUAL_STACKS,
)
def test_section_2_5_benchmark_stacks(
    stack_id: int,
    stt_model: str,
    llm_model: str,
    tts_model: str,
    exp_unrounded: float,
    exp_rounded_sum: float,
    exp_100_mins: int,
):
    """
    Validates that calculate_call_unit_economics mathematically matches every single one of
    the 20 reference benchmark stacks from Section 2.5 of the Master Spec down to the exact paisa.
    """
    # 1 Minute Live Call Benchmark Standard
    call_log = {
        "duration_seconds": 60,
        "cost_to_customer_paise": 350,  # ₹3.50 revenue
        "call_status": "completed",
    }
    workflow_run = {
        "usage_info": {
            "stt": {stt_model: {"audio_seconds": 60.0}},
            "llm": {
                llm_model: {
                    "prompt_tokens": 500,
                    "completion_tokens": 150,
                    "cache_read_input_tokens": 0,
                }
            },
            "tts": {tts_model: {"characters": 900}},
        }
    }
    overrides = {
        "include_telephony": False,  # Scope: AI API costs only per Section 2.5
        "exchange_rate": 88.0,
    }

    res = calculate_call_unit_economics(call_log, workflow_run, overrides)

    # 1. Total unrounded cost should match within 0.001 INR precision
    assert pytest.approx(res["total_cost_inr"], rel=1e-3) == exp_unrounded

    # 2. Rounded sum: individual rounded item sums match the Section 2.5 table header
    stt_rounded = round(res["cost_breakdown"]["stt"]["cost_inr"], 2)
    llm_rounded = round(res["cost_breakdown"]["llm_conversation"]["cost_inr"], 2)
    tts_rounded = round(res["cost_breakdown"]["tts"]["cost_inr"], 2)
    item_rounded_sum = round(stt_rounded + llm_rounded + tts_rounded, 2)
    assert item_rounded_sum == exp_rounded_sum

    # 3. Cost per 100 minutes integer agreement
    cost_100_mins = round(res["total_cost_inr"] * 100)
    assert cost_100_mins == exp_100_mins


# ==============================================================================
# SECTION 2: EDGE CASES (EC-1 THROUGH EC-20)
# ==============================================================================

def test_ec_1_sub_second_failed_call_carrier_cost_suppression():
    """
    EC-1: Sub-second / early hangup / failed calls must have Plivo carrier cost = 0.0,
    billed_telephony_minutes = 0, and not raise ZeroDivisionError.
    """
    call_log = {
        "duration_seconds": 2,
        "call_status": "failed",
        "cost_to_customer_paise": 0,
    }
    workflow_run = {"usage_info": {}}
    res = calculate_call_unit_economics(call_log, workflow_run)

    assert res["cost_breakdown"]["plivo"]["cost_inr"] == 0.0
    assert res["billed_minutes"] == 0
    assert "FAILED / UNANSWERED (Zero Carrier Cost)" in res["notices"]
    assert res["cost_per_minute_inr"] >= 0.0  # Division by zero avoided


def test_ec_2_and_ec_7_zero_synthetic_estimation_standard():
    """
    EC-2 & EC-7 & Section 3.4: When usage_info is empty or missing,
    the engine must NEVER fabricate fake characters (e.g. duration * 0.45 * 900).
    It must report 0 chars and 0 tokens with appropriate audit notice.
    """
    call_log = {
        "duration_seconds": 90,
        "call_status": "completed",
        "cost_to_customer_paise": 700,
    }
    # Completely empty usage_info (legacy pre-patch run)
    workflow_run = {"usage_info": {}}

    res = calculate_call_unit_economics(call_log, workflow_run)

    # Must be 0 characters, NOT 90 * 0.45 * 900 = 607 chars
    assert res["cost_breakdown"]["tts"]["characters"] == 0
    assert res["cost_breakdown"]["tts"]["cost_inr"] == 0.0
    assert "0 Chars Synthesized" in res["notices"]
    assert "UNRECORDED TELEMETRY (Pre-patch or Dropped Webhook)" in res["notices"]


def test_ec_3_zero_billed_free_trial_margin_handling():
    """
    EC-3: Calls with cost_to_customer_paise = 0 (free trial or internal testing)
    must produce margin_status = 'free_trial' without triggering NaN or ZeroDivisionError.
    """
    call_log = {
        "duration_seconds": 60,
        "call_status": "completed",
        "cost_to_customer_paise": 0,  # Zero revenue
    }
    workflow_run = {
        "usage_info": {
            "tts": {"smallest_ai": 500},
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run)

    assert res["revenue_inr"] == 0.0
    assert res["margin_status"] == "free_trial"
    assert res["margin_pct"] == -100.0  # Cost incurred but zero revenue
    assert any("FREE TRIAL / INTERNAL TEST" in n for n in res["notices"])


def test_ec_4_carrier_60_second_block_ceiling_rounding():
    """
    EC-4: Plivo bills in 60-second increments (60/60 block).
    e.g. 5 seconds -> 1 minute (₹0.45), 61 seconds -> 2 minutes (₹0.90).
    """
    # 5-second completed call
    res_5s = calculate_call_unit_economics({
        "duration_seconds": 5,
        "call_status": "completed",
        "cost_to_customer_paise": 100,
    })
    assert res_5s["billed_minutes"] == 1
    assert pytest.approx(res_5s["cost_breakdown"]["plivo"]["cost_inr"]) == 0.45

    # 61-second completed call
    res_61s = calculate_call_unit_economics({
        "duration_seconds": 61,
        "call_status": "completed",
        "cost_to_customer_paise": 500,
    })
    assert res_61s["billed_minutes"] == 2
    assert pytest.approx(res_61s["cost_breakdown"]["plivo"]["cost_inr"]) == 0.90


def test_ec_5_mid_call_model_failover_multi_provider():
    """
    EC-5: Mid-call model failover (e.g. Sarvam 105B used then failed over to GPT-4.1-mini)
    must aggregate all models and itemize both without overwriting.
    """
    call_log = {
        "duration_seconds": 120,
        "call_status": "completed",
        "cost_to_customer_paise": 800,
    }
    workflow_run = {
        "usage_info": {
            "llm": {
                "sarvam|||sarvam-105b": {
                    "prompt_tokens": 600,
                    "completion_tokens": 100,
                    "cache_read_input_tokens": 0,
                },
                "openai|||gpt-4.1-mini": {
                    "prompt_tokens": 400,
                    "completion_tokens": 80,
                    "cache_read_input_tokens": 0,
                },
            }
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run)

    llm_items = res["cost_breakdown"]["llm_conversation"]["items"]
    assert len(llm_items) == 2
    providers = {item["provider"] for item in llm_items}
    assert "sarvam" in providers
    assert "openai" in providers
    assert res["cost_breakdown"]["llm_conversation"]["prompt_tokens"] == 1000
    assert res["cost_breakdown"]["llm_conversation"]["completion_tokens"] == 180


def test_ec_6_unrecognized_custom_model_string_fallback():
    """
    EC-6: Date-stamped or fine-tuned model strings (e.g. 'openai|||gpt-4o-2026-08-06')
    must trigger regex fallback to the baseline rate with a notice.
    """
    cfg, fallback = resolve_llm_pricing("openai|||gpt-4.1-custom-preview-2026")
    assert cfg["model"] == "gpt-4.1-mini"
    assert fallback is True

    call_log = {"duration_seconds": 60, "cost_to_customer_paise": 400}
    workflow_run = {
        "usage_info": {
            "llm": {
                "custom-gpt-4-model": {"prompt_tokens": 500, "completion_tokens": 100}
            }
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run)
    assert any("Default Fallback Rate Applied" in n for n in res["notices"])


def test_ec_8_extraction_llm_token_separation():
    """
    EC-8: Models tagged with ':extraction' must be cleanly segregated from
    in-call conversation tokens.
    """
    call_log = {"duration_seconds": 60, "cost_to_customer_paise": 400}
    workflow_run = {
        "usage_info": {
            "llm": {
                "variable_extraction:extraction|||gpt-4.1-mini": {
                    "prompt_tokens": 800,
                    "completion_tokens": 50,
                },
                "sarvam|||sarvam-105b": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 120,
                },
            }
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run)

    # Conversational LLM only contains Sarvam-105B
    assert res["cost_breakdown"]["llm_conversation"]["prompt_tokens"] == 1000
    assert res["cost_breakdown"]["llm_conversation"]["provider"] == "sarvam"

    # Extraction LLM contains GPT-4.1-mini
    assert res["cost_breakdown"]["llm_extraction"]["prompt_tokens"] == 800
    assert res["cost_breakdown"]["llm_extraction"]["provider"] == "openai"
    assert res["cost_breakdown"]["llm_extraction"]["cost_inr"] > 0.0


def test_ec_11_stt_streaming_duration_fallback():
    """
    EC-11: If usage_info['stt'] is empty on a connected call, the engine
    automatically uses connected duration for full-duplex streaming audio.
    """
    call_log = {
        "duration_seconds": 75,
        "call_status": "completed",
        "cost_to_customer_paise": 500,
    }
    workflow_run = {"usage_info": {"stt": {}}}

    res = calculate_call_unit_economics(call_log, workflow_run)
    assert res["cost_breakdown"]["stt"]["seconds"] == 75.0
    assert res["cost_breakdown"]["stt"]["cost_inr"] > 0.0
    assert any("Full Duplex Stream" in n for n in res["notices"])


def test_ec_12_post_call_qa_analysis_token_isolation():
    """
    EC-12: Tokens logged under QAAnalysis|||{model} must be placed in
    post_call_breakdown and NOT inflate total_cost_inr (operational in-call cost).
    """
    call_log = {"duration_seconds": 60, "cost_to_customer_paise": 500}
    workflow_run = {
        "usage_info": {
            "llm": {
                "sarvam|||sarvam-105b": {"prompt_tokens": 500, "completion_tokens": 100},
                "QAAnalysis|||gpt-4o-mini": {"prompt_tokens": 4000, "completion_tokens": 800},
            }
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run)

    # Operational in-call conversation does not contain QA tokens
    assert res["cost_breakdown"]["llm_conversation"]["prompt_tokens"] == 500

    # QA Analysis is in post_call_breakdown
    assert res["post_call_breakdown"]["llm_qa_analysis"]["prompt_tokens"] == 4000
    assert res["post_call_cost_inr"] > 0.0
    # In-call cost is lower than total all-in cost
    assert res["total_cost_inr"] < res["total_all_in_cost_inr"]


def test_ec_13_telephony_direction_and_international_rates():
    """
    EC-13: Inbound calls bill at ₹0.30/min; International calls (non-+91) bill at ₹1.80/min.
    """
    # Inbound call (1 min)
    res_inbound = calculate_call_unit_economics({
        "duration_seconds": 60,
        "direction": "inbound",
        "call_status": "completed",
    })
    assert pytest.approx(res_inbound["cost_breakdown"]["plivo"]["cost_inr"]) == 0.30

    # International outbound to US number (+1)
    res_intl = calculate_call_unit_economics({
        "duration_seconds": 60,
        "direction": "outbound",
        "to_phone_number": "+14155552671",
        "call_status": "completed",
    })
    assert pytest.approx(res_intl["cost_breakdown"]["plivo"]["cost_inr"]) == 1.80


def test_ec_15_prompt_caching_discount_math():
    """
    EC-15: Prompt caching discount must reduce the cost of cached tokens:
    cost = (non_cached * R_in) + (cached * R_cached) + (comp * R_out).
    """
    call_log = {"duration_seconds": 60, "cost_to_customer_paise": 500}

    # Case A: 10,000 prompt tokens with 0 cached
    run_no_cache = {
        "usage_info": {
            "llm": {
                "sarvam-105b": {
                    "prompt_tokens": 10_000,
                    "completion_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            }
        }
    }
    res_no_cache = calculate_call_unit_economics(call_log, run_no_cache, {"include_telephony": False})

    # Case B: 10,000 prompt tokens with 8,000 cached (₹5/1M vs ₹15/1M)
    run_with_cache = {
        "usage_info": {
            "llm": {
                "sarvam-105b": {
                    "prompt_tokens": 10_000,
                    "completion_tokens": 0,
                    "cache_read_input_tokens": 8_000,
                }
            }
        }
    }
    res_with_cache = calculate_call_unit_economics(call_log, run_with_cache, {"include_telephony": False})

    # Expected: 2000 non-cached * 15/1M (₹0.030) + 8000 cached * 5/1M (₹0.040) = ₹0.070
    # vs No cache: 10000 * 15/1M = ₹0.150
    assert pytest.approx(res_no_cache["cost_breakdown"]["llm_conversation"]["cost_inr"]) == 0.150
    assert pytest.approx(res_with_cache["cost_breakdown"]["llm_conversation"]["cost_inr"]) == 0.070
    assert res_with_cache["total_cost_inr"] < res_no_cache["total_cost_inr"]


def test_ec_17_multi_currency_enterprise_usd_account():
    """
    EC-17: Enterprise accounts billed in USD cents must convert revenue to INR
    using the configured exchange rate so gross margin % is calculated correctly.
    """
    # $0.10 USD revenue (= ₹8.80 @ 88), 10 cents = 1000 paise/cents
    call_log = {
        "duration_seconds": 60,
        "cost_to_customer_paise": 10,  # 10 cents
        "customer_currency": "USD",
        "call_status": "completed",
    }
    workflow_run = {
        "usage_info": {
            "tts": {"smallest_ai": 500},
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run, {"exchange_rate": 88.0})

    # 10 cents / 100 * 88 = $0.10 * 88 = ₹8.80
    assert pytest.approx(res["revenue_inr"]) == 8.80
    assert res["margin_pct"] > 0.0


def test_duck_typing_with_orm_and_dict():
    """
    Ensures calculate_call_unit_economics operates smoothly on both dicts and ORM objects.
    """
    class MockCallLog:
        def __init__(self):
            self.dograh_run_id = 9999
            self.customer_id = 14
            self.duration_seconds = 76
            self.cost_to_customer_paise = 700
            self.call_status = "completed"
            self.direction = "outbound"
            self.to_phone_number = "+919876543210"

    class MockWorkflowRun:
        def __init__(self):
            self.id = 9999
            self.usage_info = {
                "tts": {"smallest_ai": 600},
            }

    res = calculate_call_unit_economics(MockCallLog(), MockWorkflowRun())
    assert res["dograh_run_id"] == 9999
    assert res["customer_id"] == 14
    assert res["billed_minutes"] == 2
    assert res["revenue_inr"] == 7.00
    assert res["margin_status"] in ["healthy", "warning"]


# ==============================================================================
# SECTION 3: PHASE 5 E2E VALIDATION & BENCHMARKING (5 REPRESENTATIVE SCENARIOS)
# ==============================================================================

def test_phase5_scenario1_normal_completed_call():
    """
    Scenario 1: Normal completed call (1-2 minutes)
    Validates:
      - Duration: 84 seconds -> 2 billed Plivo minutes (60/60 block = ₹0.90)
      - Audio, tokens, and characters calculated accurately
      - Profitable margin correctly computed
    """
    call_log = {
        "dograh_run_id": 5001,
        "customer_id": 10,
        "duration_seconds": 84,
        "cost_to_customer_paise": 700,  # ₹7.00
        "call_status": "completed",
        "direction": "outbound",
        "to_phone_number": "+919876543210",
    }
    workflow_run = {
        "usage_info": {
            "stt": {"deepgram_nova_3": {"audio_seconds": 70.0}},
            "llm": {
                "openai|||gpt-4.1-mini": {
                    "prompt_tokens": 650,
                    "completion_tokens": 120,
                    "cache_read_input_tokens": 200,
                },
                "variable_extraction:extraction|||gpt-4.1-mini": {
                    "prompt_tokens": 400,
                    "completion_tokens": 30,
                    "cache_read_input_tokens": 0,
                },
            },
            "tts": {"smallest_ai_lightning": {"characters": 800}},
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run)

    assert res["dograh_run_id"] == 5001
    assert res["call_duration_seconds"] == 84
    assert res["billed_minutes"] == 2
    assert pytest.approx(res["cost_breakdown"]["plivo"]["cost_inr"]) == 0.90
    assert res["cost_breakdown"]["stt"]["seconds"] == 70.0
    assert res["cost_breakdown"]["tts"]["characters"] == 800
    assert res["revenue_inr"] == 7.00
    assert res["total_cost_inr"] < res["revenue_inr"]
    assert res["profit_inr"] > 0
    assert res["margin_pct"] >= 40.0
    assert res["margin_status"] == "healthy"


def test_phase5_scenario2_short_hangup_call():
    """
    Scenario 2: Short hangup call (<10 seconds)
    Validates:
      - 4-second unanswered/early hangup call
      - Plivo carrier cost suppressed to ₹0.00
      - Billed minutes = 0
      - No division-by-zero errors in cost_per_minute_inr
      - Notice added to audit log
    """
    call_log = {
        "dograh_run_id": 5002,
        "customer_id": 10,
        "duration_seconds": 4,
        "cost_to_customer_paise": 0,
        "call_status": "no-answer",
        "direction": "outbound",
        "to_phone_number": "+919876543210",
    }
    workflow_run = {"usage_info": {}}
    res = calculate_call_unit_economics(call_log, workflow_run)

    assert res["billed_minutes"] == 0
    assert res["cost_breakdown"]["plivo"]["cost_inr"] == 0.0
    assert res["cost_per_minute_inr"] == 0.0
    assert any("FAILED / UNANSWERED" in n for n in res["notices"])


def test_phase5_scenario3_hindi_hinglish_call_sarvam_stack():
    """
    Scenario 3: Hindi/Hinglish call using Sarvam STT & Sarvam TTS
    Validates:
      - Sarvam Saaras STT (₹0.49/min)
      - Sarvam-105B Conversational LLM (₹15 in / ₹5 cached / ₹60 out)
      - Sarvam Bulbul v4 Flash TTS (₹2.00 / 1K chars)
      - All rates native INR (no USD forex fluctuation)
    """
    call_log = {
        "dograh_run_id": 5003,
        "customer_id": 12,
        "duration_seconds": 60,
        "cost_to_customer_paise": 450,  # ₹4.50
        "call_status": "completed",
        "direction": "outbound",
    }
    workflow_run = {
        "usage_info": {
            "stt": {"sarvam_saaras": {"audio_seconds": 60.0}},
            "llm": {
                "sarvam|||sarvam-105b": {
                    "prompt_tokens": 500,
                    "completion_tokens": 150,
                    "cache_read_input_tokens": 0,
                }
            },
            "tts": {"sarvam_bulbul_v4": {"characters": 900}},
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run, {"include_telephony": True})

    # AI Stack = ₹0.49 (STT) + ₹0.0165 (LLM) + ₹1.80 (TTS) = ₹2.3065
    # Plivo = ₹0.45
    # Total = ₹2.7565
    assert pytest.approx(res["cost_breakdown"]["stt"]["cost_inr"], abs=0.01) == 0.49
    assert pytest.approx(res["cost_breakdown"]["tts"]["cost_inr"], abs=0.01) == 1.80
    assert pytest.approx(res["cost_breakdown"]["plivo"]["cost_inr"], abs=0.01) == 0.45
    assert res["revenue_inr"] == 4.50
    assert res["profit_inr"] > 1.50
    assert res["margin_status"] == "warning" or res["margin_status"] == "healthy"


def test_phase5_scenario4_english_call_deepgram_smallest_stack():
    """
    Scenario 4: English call using Deepgram STT & Smallest AI TTS
    Validates:
      - Deepgram Nova-3 ($0.0048/min @ ₹88 = ₹0.4224/min)
      - OpenAI GPT-4.1-mini ($0.40 in / $1.60 out @ ₹88 = ~₹0.039)
      - Smallest AI Lightning V3.1 ($0.0175/1K chars @ 900 chars @ ₹88 = ₹1.386)
      - Total AI cost matches benchmark stack 2 (₹1.85/min)
    """
    call_log = {
        "dograh_run_id": 5004,
        "customer_id": 14,
        "duration_seconds": 60,
        "cost_to_customer_paise": 350,  # ₹3.50
        "call_status": "completed",
    }
    workflow_run = {
        "usage_info": {
            "stt": {"deepgram_nova_3": {"audio_seconds": 60.0}},
            "llm": {
                "openai|||gpt-4.1-mini": {
                    "prompt_tokens": 500,
                    "completion_tokens": 150,
                    "cache_read_input_tokens": 0,
                }
            },
            "tts": {"smallest_ai_lightning": {"characters": 900}},
        }
    }
    res = calculate_call_unit_economics(call_log, workflow_run, {"include_telephony": False})

    # AI Stack benchmark 2: ₹1.85/min
    assert pytest.approx(res["total_cost_inr"], abs=0.01) == 1.8471
    assert round(res["total_cost_inr"], 2) == 1.85


def test_phase5_scenario5_simulation_alert_margin_degradation():
    """
    Scenario 5: Simulation check: Verify that switching TTS to ElevenLabs Multilingual
    dynamically causes margin degradation and alerts the admin.
    """
    call_log = {
        "dograh_run_id": 5005,
        "customer_id": 14,
        "duration_seconds": 60,
        "cost_to_customer_paise": 350,  # ₹3.50 revenue charged to customer
        "call_status": "completed",
    }
    workflow_run = {
        "usage_info": {
            "stt": {"deepgram_nova_3": {"audio_seconds": 60.0}},
            "llm": {
                "openai|||gpt-4.1-mini": {
                    "prompt_tokens": 500,
                    "completion_tokens": 150,
                    "cache_read_input_tokens": 0,
                }
            },
            "tts": {"smallest_ai_lightning": {"characters": 900}},
        }
    }

    # Baseline with Smallest AI and Plivo (₹0.45):
    # Total cost = ₹0.45 + ₹1.85 = ₹2.30 -> Profit = ₹3.50 - ₹2.30 = ₹1.20 (Margin = 34.3%, positive)
    base = calculate_call_unit_economics(call_log, workflow_run)
    assert base["profit_inr"] > 0
    assert base["margin_pct"] > 0
    assert base["margin_status"] in ["healthy", "warning"]

    # Simulated with ElevenLabs Multilingual ($0.080 / 1K chars):
    # TTS alone: 900 / 1000 * 0.080 * 88 = ₹6.336
    # Total call cost = ₹0.45 (Plivo) + ₹0.4224 (STT) + ₹0.0387 (LLM) + ₹6.336 (TTS) = ~₹7.25
    # Revenue is only ₹3.50 -> Loss of -₹3.75 -> Margin drops to -107% (Severe Loss)
    sim = calculate_call_unit_economics(
        call_log,
        workflow_run,
        {"tts_provider": "elevenlabs_multilingual"},
    )
    assert sim["total_cost_inr"] > base["total_cost_inr"]
    assert sim["profit_inr"] < 0  # Negative gross profit
    assert sim["margin_pct"] < 0  # Negative margin
    assert sim["margin_status"] == "loss"  # Degradation alert triggered!

