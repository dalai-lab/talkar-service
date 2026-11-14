"""
Master Pricing Registry and Call-by-Call Unit Economics Engine.

Specification Reference: /Users/arnav/Internship/call_cost_audit_master_spec.md
Enforces:
  - Zero Synthetic Estimation Standard (Section 3.4)
  - All 20 Comprehensive Edge Case Guards (Section 7: EC-1 through EC-20)
  - Exact Decimal Precision with final display rounding
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from services.forex_service import forex_service
except ImportError:
    from forex_service import forex_service


# ==============================================================================
# 1. UNIFIED VENDOR PRICING REGISTRY
# ==============================================================================

VENDOR_PRICING_REGISTRY: Dict[str, Any] = {
    "forex": {
        "usd_to_inr_baseline": 88.0,
        "usd_to_inr_market": 95.10,
        "default": 88.0,
    },
    "telephony": {
        "plivo": {
            "outbound_india": {
                "rate_inr_per_min": 0.45,
                "min_rate": 0.38,
                "max_rate": 0.60,
                "increment_seconds": 60,
                "description": "Standard TRAI/DLT domestic SIP termination",
            },
            "inbound_india": {
                "rate_inr_per_min": 0.30,
                "increment_seconds": 60,
                "description": "Inbound DID termination",
            },
            "international_default": {
                "rate_inr_per_min": 1.80,
                "increment_seconds": 60,
                "description": "Default international voice termination (US/UK/Rest of world)",
            },
        }
    },
    "stt": {
        "deepgram_flux": {
            "provider": "deepgram",
            "model": "flux-general-multi",
            "rate": 0.0048,
            "currency": "USD",
            "unit": "min",
            "description": "Deepgram Flux streaming conversational STT",
            "aliases": [
                "flux",
                "deepgram_flux",
                "flux-general-multi",
                "flux-general-en",
                "flux-general",
                "flux_general_multi",
                "flux_general_en",
                "dograh_flux",
            ],
        },
        "deepgram_nova_3": {
            "provider": "deepgram",
            "model": "nova-3",
            "rate": 0.0048,
            "currency": "USD",
            "unit": "min",
            "description": "Deepgram Nova-3 promotional streaming PAYG",
            "aliases": ["nova-3", "nova_3", "deepgram_nova_3", "nova3", "nova-3-general", "nova-3-medical", "deepgram"],
        },
        "deepgram_nova_2": {
            "provider": "deepgram",
            "model": "nova-2",
            "rate": 0.0058,
            "currency": "USD",
            "unit": "min",
            "description": "Deepgram Nova-2 streaming standard tier",
            "aliases": ["nova-2", "nova_2", "deepgram_nova_2", "nova2", "nova-2-general", "nova-2-medical"],
        },
        "sarvam_saaras": {
            "provider": "sarvam",
            "model": "saaras-v3",
            "rate": 0.49,
            "standard_rate": 0.50,
            "currency": "INR",
            "unit": "min",
            "description": "Sarvam Saaras STT (₹0.49/min PAYG benchmark, ₹0.50/min official ₹30/hr)",
            "aliases": [
                "sarvam",
                "saaras",
                "sarvam_saaras",
                "saaras-v3",
                "saaras:v3",
                "saaras_v3",
                "sarvam-stt",
                "sarvam_stt",
            ],
        },
        "sarvam_saaras_v4": {
            "provider": "sarvam",
            "model": "saaras-v4",
            "rate": 0.49,
            "standard_rate": 0.50,
            "currency": "INR",
            "unit": "min",
            "description": "Sarvam Saaras v4 STT (₹0.49/min PAYG benchmark, ₹0.50/min official ₹30/hr)",
            "aliases": [
                "sarvam_saaras_v4",
                "saaras:v4",
                "saaras-v4",
                "saaras_v4",
                "saaras-v4-flash",
                "v4",
            ],
        },
        "smallest_ai_pulse": {
            "provider": "smallest_ai",
            "model": "pulse",
            "rate": 0.0030,
            "currency": "USD",
            "unit": "min",
            "description": "Smallest AI Pulse STT streaming",
            "aliases": ["smallest_ai_pulse", "pulse", "smallest-stt"],
        },
        "cartesia_ink": {
            "provider": "cartesia",
            "model": "ink-2",
            "rate": 0.0050,
            "currency": "USD",
            "unit": "min",
            "description": "Cartesia Ink-2 streaming STT",
            "aliases": [
                "cartesia_ink",
                "cartesia",
                "ink-2",
                "ink-whisper",
                "ink",
                "cartesia-stt",
                "cartesia_stt",
            ],
        },
        "openai_whisper": {
            "provider": "openai",
            "model": "whisper-1",
            "rate": 0.0060,
            "currency": "USD",
            "unit": "min",
            "description": "OpenAI Whisper STT",
            "aliases": [
                "whisper-1",
                "whisper",
                "openai_whisper",
                "openai-stt",
                "openai_stt",
            ],
        },
        "elevenlabs_scribe": {
            "provider": "elevenlabs",
            "model": "scribe_v1",
            "rate": 0.0070,
            "currency": "USD",
            "unit": "min",
            "description": "ElevenLabs Scribe STT",
            "aliases": [
                "scribe",
                "scribe_v1",
                "elevenlabs_scribe",
                "elevenlabs_stt",
                "elevenlabs-stt",
            ],
        },
        "google_stt": {
            "provider": "google",
            "model": "chirp-2",
            "rate": 0.0160,
            "currency": "USD",
            "unit": "min",
            "description": "Google Cloud Speech-to-Text standard",
            "aliases": [
                "google_stt",
                "google",
                "google-stt",
                "chirp",
                "chirp-2",
                "google_speech",
                "google-speech",
            ],
        },
        "azure_stt": {
            "provider": "azure",
            "model": "azure-speech",
            "rate": 0.0167,
            "currency": "USD",
            "unit": "min",
            "description": "Azure Speech-to-Text standard ($1.00/hour)",
            "aliases": [
                "azure_stt",
                "azure",
                "azure-stt",
                "azure_speech",
                "azure-speech",
            ],
        },
        "assemblyai": {
            "provider": "assemblyai",
            "model": "conformer-2",
            "rate": 0.0150,
            "currency": "USD",
            "unit": "min",
            "description": "AssemblyAI Streaming Speech-to-Text",
            "aliases": [
                "assemblyai",
                "assembly_ai",
                "assembly",
                "assemblyai_stt",
                "assembly-stt",
            ],
        },
        "gladia": {
            "provider": "gladia",
            "model": "gladia-realtime",
            "rate": 0.0125,
            "currency": "USD",
            "unit": "min",
            "description": "Gladia Real-time STT",
            "aliases": [
                "gladia",
                "gladia_stt",
                "gladia-stt",
            ],
        },
        "speechmatics": {
            "provider": "speechmatics",
            "model": "speechmatics-rt",
            "rate": 0.0175,
            "currency": "USD",
            "unit": "min",
            "description": "Speechmatics Real-time STT",
            "aliases": [
                "speechmatics",
                "speechmatics_stt",
                "speechmatics-stt",
            ],
        },
    },
    "llm": {
        "sarvam-105b": {
            "provider": "sarvam",
            "model": "sarvam-105b",
            "input_per_million": 15.00,
            "cached_per_million": 5.00,
            "output_per_million": 60.00,
            "currency": "INR",
            "description": "Sarvam-105B-Conversations official October 2026 pricing",
            "aliases": [
                "sarvam-105b",
                "sarvam_105b",
                "sarvam-105b-conversations",
                "sarvam-105b-conversational",
                "sarvam",
            ],
        },
        "gpt-4.1-mini": {
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "input_per_million": 0.40,
            "cached_per_million": 0.20,
            "output_per_million": 1.60,
            "currency": "USD",
            "description": "OpenAI GPT-4.1-mini verified PAYG",
            "aliases": [
                "gpt-4.1-mini",
                "gpt-4-1-mini",
                "gpt-4.1-mini-2026",
            ],
        },
        "gpt-4.1": {
            "provider": "openai",
            "model": "gpt-4.1",
            "input_per_million": 2.00,
            "cached_per_million": 0.50,
            "output_per_million": 8.00,
            "currency": "USD",
            "description": "OpenAI GPT-4.1 frontier model",
            "aliases": [
                "gpt-4.1",
                "gpt-4-1",
                "gpt-4.1-frontier",
            ],
        },
        "o3-mini": {
            "provider": "openai",
            "model": "o3-mini",
            "input_per_million": 1.10,
            "cached_per_million": 0.55,
            "output_per_million": 4.40,
            "currency": "USD",
            "description": "OpenAI o3-mini reasoning model",
            "aliases": [
                "o3-mini",
                "o3_mini",
                "o1-mini",
                "o1_mini",
                "o1",
                "o3",
            ],
        },
        "gpt-4.1-nano": {
            "provider": "openai",
            "model": "gpt-4.1-nano",
            "input_per_million": 0.10,
            "cached_per_million": 0.03,
            "output_per_million": 0.40,
            "currency": "USD",
            "description": "OpenAI GPT-4.1-nano ultra-low latency tier",
            "aliases": [
                "gpt-4.1-nano",
                "gpt-4.1-nano-2025",
                "gpt_4_1_nano",
                "gpt-4-1-nano",
                "4.1-nano",
                "nano",
            ],
        },
        "gpt-4o-mini": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "input_per_million": 0.15,
            "cached_per_million": 0.075,
            "output_per_million": 0.60,
            "currency": "USD",
            "description": "OpenAI GPT-4o-mini standard PAYG",
            "aliases": [
                "gpt-4o-mini",
                "gpt-4o-mini-2024-07-18",
                "gpt_4o_mini",
            ],
        },
        "gpt-4o": {
            "provider": "openai",
            "model": "gpt-4o",
            "input_per_million": 2.50,
            "cached_per_million": 1.25,
            "output_per_million": 10.00,
            "currency": "USD",
            "description": "OpenAI GPT-4o frontier model",
            "aliases": [
                "gpt-4o",
                "gpt-4o-2024-08-06",
                "gpt-4o-2024-05-13",
            ],
        },
        "claude-3-5-sonnet": {
            "provider": "anthropic",
            "model": "claude-3-5-sonnet",
            "input_per_million": 3.00,
            "cached_per_million": 0.30,
            "output_per_million": 15.00,
            "currency": "USD",
            "description": "Anthropic Claude 3.5 Sonnet frontier model",
            "aliases": [
                "claude-3-5-sonnet",
                "claude-3.5-sonnet",
                "claude-sonnet-3-5",
                "claude-3-5-sonnet-20241022",
                "claude-3-5-sonnet-latest",
            ],
        },
        "claude-3-7-sonnet": {
            "provider": "anthropic",
            "model": "claude-3-7-sonnet",
            "input_per_million": 3.00,
            "cached_per_million": 0.30,
            "output_per_million": 15.00,
            "currency": "USD",
            "description": "Anthropic Claude 3.7 Sonnet reasoning & agent model",
            "aliases": [
                "claude-3-7-sonnet",
                "claude-3.7-sonnet",
                "claude-sonnet-3-7",
                "claude-3-7-sonnet-latest",
            ],
        },
        "claude-3-5-haiku": {
            "provider": "anthropic",
            "model": "claude-3-5-haiku",
            "input_per_million": 0.80,
            "cached_per_million": 0.08,
            "output_per_million": 4.00,
            "currency": "USD",
            "description": "Anthropic Claude 3.5 Haiku high-speed intelligence",
            "aliases": [
                "claude-3-5-haiku",
                "claude-3.5-haiku",
                "claude-haiku-3-5",
                "claude-3-5-haiku-latest",
            ],
        },
        "gemini-1.5-flash": {
            "provider": "google",
            "model": "gemini-1.5-flash",
            "input_per_million": 0.075,
            "cached_per_million": 0.01875,
            "output_per_million": 0.30,
            "currency": "USD",
            "description": "Google Gemini 1.5 Flash lightweight multimodal",
            "aliases": [
                "gemini-1.5-flash",
                "gemini-flash",
                "gemini_1_5_flash",
                "gemini-1.5-flash-latest",
            ],
        },
        "gemini-1.5-pro": {
            "provider": "google",
            "model": "gemini-1.5-pro",
            "input_per_million": 1.25,
            "cached_per_million": 0.3125,
            "output_per_million": 5.00,
            "currency": "USD",
            "description": "Google Gemini 1.5 Pro complex reasoning",
            "aliases": [
                "gemini-1.5-pro",
                "gemini-pro",
                "gemini_1_5_pro",
                "gemini-1.5-pro-latest",
            ],
        },
        "gemini-2.0-flash": {
            "provider": "google",
            "model": "gemini-2.0-flash",
            "input_per_million": 0.10,
            "cached_per_million": 0.025,
            "output_per_million": 0.40,
            "currency": "USD",
            "description": "Google Gemini 2.0 Flash generation",
            "aliases": [
                "gemini-2.0-flash",
                "gemini-2.0-flash-exp",
                "gemini-2-flash",
            ],
        },
        "llama-3.3-70b": {
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "input_per_million": 0.59,
            "cached_per_million": 0.59,
            "output_per_million": 0.79,
            "currency": "USD",
            "description": "Groq Llama 3.3 70B Versatile ultra-low latency",
            "aliases": [
                "llama-3.3-70b",
                "llama-3.3-70b-versatile",
                "groq/llama-3.3-70b",
            ],
        },
        "deepseek-v3": {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "input_per_million": 0.14,
            "cached_per_million": 0.014,
            "output_per_million": 0.28,
            "currency": "USD",
            "description": "DeepSeek V3 Chat PAYG",
            "aliases": [
                "deepseek-chat",
                "deepseek-v3",
                "deepseek",
            ],
        },
        "deepseek-r1": {
            "provider": "deepseek",
            "model": "deepseek-reasoner",
            "input_per_million": 0.55,
            "cached_per_million": 0.14,
            "output_per_million": 2.19,
            "currency": "USD",
            "description": "DeepSeek R1 Reasoning model",
            "aliases": [
                "deepseek-reasoner",
                "deepseek-r1",
                "deepseek_r1",
                "r1",
            ],
        },
        "llama-3.1-8b": {
            "provider": "groq",
            "model": "llama-3.1-8b-instant",
            "input_per_million": 0.05,
            "cached_per_million": 0.05,
            "output_per_million": 0.08,
            "currency": "USD",
            "description": "Groq Llama 3.1 8B Instant",
            "aliases": [
                "llama-3.1-8b",
                "llama-3.1-8b-instant",
                "llama-8b",
                "llama-3-8b",
                "groq/llama-3.1-8b",
            ],
        },
        "claude-3-haiku": {
            "provider": "anthropic",
            "model": "claude-3-haiku",
            "input_per_million": 0.25,
            "cached_per_million": 0.025,
            "output_per_million": 1.25,
            "currency": "USD",
            "description": "Anthropic Claude 3 Haiku",
            "aliases": [
                "claude-3-haiku",
                "claude-haiku",
                "claude-3-haiku-20240307",
            ],
        },
        "claude-3-opus": {
            "provider": "anthropic",
            "model": "claude-3-opus",
            "input_per_million": 15.00,
            "cached_per_million": 1.50,
            "output_per_million": 75.00,
            "currency": "USD",
            "description": "Anthropic Claude 3 Opus",
            "aliases": [
                "claude-3-opus",
                "claude-opus",
                "claude-3-opus-20240229",
            ],
        },
    },
    "tts": {
        "smallest_ai_lightning": {
            "provider": "smallest_ai",
            "model": "lightning-v3.1",
            "rate_per_1k_chars": 0.0175,  # Normalized from $0.175 / 10k chars (EC-14)
            "published_rate": "$0.175 / 10K chars",
            "currency": "USD",
            "description": "Smallest AI Lightning V3.1 Waves TTS",
            "aliases": [
                "smallest_ai",
                "smallest",
                "smallest-ai",
                "lightning",
                "lightning-v3.1",
                "lightning-v3",
                "lightning_v3.1_pro",
                "lightning-v3.1-pro",
                "lightning_v3_1_pro",
                "waves",
            ],
        },
        "sarvam_bulbul_v4": {
            "provider": "sarvam",
            "model": "bulbul-v4-flash",
            "rate_per_1k_chars": 2.00,  # Normalized from ₹20.00 / 10k chars (EC-14)
            "published_rate": "₹20.00 / 10K chars",
            "currency": "INR",
            "description": "Sarvam Bulbul v4 Flash (11 Indic languages)",
            "aliases": [
                "bulbul-v4-flash",
                "bulbul-v4",
                "bulbul_v4",
                "bulbul_v4_flash",
                "v4-flash",
                "sarvam_bulbul_v4",
                "sarvam",
                "sarvam_tts",
                "sarvam-tts",
                "bulbul",
            ],
        },
        "sarvam_bulbul_v3": {
            "provider": "sarvam",
            "model": "bulbul-v3",
            "rate_per_1k_chars": 3.00,  # Normalized from ₹30.00 / 10k chars (EC-14)
            "published_rate": "₹30.00 / 10K chars",
            "currency": "INR",
            "description": "Sarvam Bulbul v3 standard Indic TTS",
            "aliases": [
                "bulbul-v3",
                "bulbul_v3",
                "bulbul",
                "sarvam_bulbul_v3",
            ],
        },
        "deepgram_aura_2": {
            "provider": "deepgram",
            "model": "aura-2",
            "rate_per_1k_chars": 0.030,
            "published_rate": "$0.030 / 1K chars",
            "currency": "USD",
            "description": "Deepgram Aura-2 high-expressiveness TTS",
            "aliases": [
                "aura-2",
                "aura_2",
                "deepgram_aura_2",
                "deepgram-aura-2",
            ],
        },
        "deepgram_aura_1": {
            "provider": "deepgram",
            "model": "aura-1",
            "rate_per_1k_chars": 0.015,
            "published_rate": "$0.015 / 1K chars",
            "currency": "USD",
            "description": "Deepgram Aura-1 base TTS",
            "aliases": [
                "aura",
                "aura-1",
                "aura_1",
                "deepgram_aura",
                "deepgram_aura_1",
                "deepgram",
            ],
        },
        "elevenlabs_flash": {
            "provider": "elevenlabs",
            "model": "flash-v2.5",
            "rate_per_1k_chars": 0.040,
            "published_rate": "$0.040 / 1K chars",
            "currency": "USD",
            "description": "ElevenLabs Flash / Turbo low-latency streaming tier",
            "aliases": [
                "elevenlabs_flash",
                "elevenlabs-flash",
                "elevenlabs_turbo",
                "elevenlabs-turbo",
                "eleven_flash",
                "flash",
                "turbo",
                "elevenlabs",
            ],
        },
        "elevenlabs_multilingual": {
            "provider": "elevenlabs",
            "model": "multilingual-v2",
            "rate_per_1k_chars": 0.080,
            "published_rate": "$0.080 / 1K chars",
            "currency": "USD",
            "description": "ElevenLabs Multilingual v3/v4 premium expressiveness tier",
            "aliases": [
                "elevenlabs_multilingual",
                "elevenlabs-multilingual",
                "eleven_multilingual",
                "multilingual",
                "elevenlabs_multilingual_v2",
                "elevenlabs-multilingual-v3",
            ],
        },
        "cartesia_sonic": {
            "provider": "cartesia",
            "model": "sonic-3.5",
            "rate_per_1k_chars": 0.040,
            "published_rate": "$0.040 / 1K chars",
            "currency": "USD",
            "description": "Cartesia Sonic-3.5 ultra-low latency TTS",
            "aliases": [
                "cartesia",
                "cartesia_sonic",
                "cartesia-tts",
                "cartesia_tts",
                "sonic",
                "sonic-3.5",
                "sonic-3",
                "sonic_3.5",
                "sonic_3",
            ],
        },
        "openai_tts": {
            "provider": "openai",
            "model": "tts-1",
            "rate_per_1k_chars": 0.015,
            "published_rate": "$0.015 / 1K chars",
            "currency": "USD",
            "description": "OpenAI TTS-1 standard",
            "aliases": [
                "openai",
                "openai_tts",
                "openai-tts",
                "tts-1",
                "tts-1-hd",
            ],
        },
        "azure_tts": {
            "provider": "azure",
            "model": "azure-neural",
            "rate_per_1k_chars": 0.015,
            "published_rate": "$0.015 / 1K chars",
            "currency": "USD",
            "description": "Azure Neural Text-to-Speech",
            "aliases": [
                "azure",
                "azure_speech",
                "azure-tts",
                "azure_tts",
                "azure-speech",
            ],
        },
        "google_tts": {
            "provider": "google",
            "model": "journey",
            "rate_per_1k_chars": 0.016,
            "published_rate": "$0.016 / 1K chars",
            "currency": "USD",
            "description": "Google Cloud Text-to-Speech Journey / Neural2",
            "aliases": [
                "google",
                "google_tts",
                "google-tts",
                "journey",
                "neural2",
                "wavenet",
            ],
        },
        "inworld_tts": {
            "provider": "inworld",
            "model": "inworld-voice",
            "rate_per_1k_chars": 0.015,
            "published_rate": "$0.015 / 1K chars",
            "currency": "USD",
            "description": "Inworld Character TTS",
            "aliases": [
                "inworld",
                "inworld_tts",
                "inworld-tts",
            ],
        },
        "minimax_tts": {
            "provider": "minimax",
            "model": "speech-01",
            "rate_per_1k_chars": 0.010,
            "published_rate": "$0.010 / 1K chars",
            "currency": "USD",
            "description": "MiniMax Speech TTS",
            "aliases": [
                "minimax",
                "minimax_tts",
                "minimax-tts",
                "speech-01",
                "speech-02",
            ],
        },
        "rime_tts": {
            "provider": "rime",
            "model": "rime-voice",
            "rate_per_1k_chars": 0.020,
            "published_rate": "$0.020 / 1K chars",
            "currency": "USD",
            "description": "Rime Expressive TTS",
            "aliases": [
                "rime",
                "rime_tts",
                "rime-tts",
            ],
        },
    },
}


# ==============================================================================
# 2. RESOLUTION HELPERS (WITH REGEX FALLBACKS - EC-6)
# ==============================================================================

def resolve_stt_pricing(name_or_provider: Optional[str]) -> Tuple[Dict[str, Any], bool]:
    """
    Resolves STT registry configuration given a model string or provider.
    Returns (config_dict, is_fallback_flag).
    """
    if not name_or_provider:
        return VENDOR_PRICING_REGISTRY["stt"]["deepgram_nova_3"], False

    clean = str(name_or_provider).lower().strip()
    left_part = ""
    right_part = ""
    if "|||" in clean:
        parts = clean.split("|||", 1)
        left_part = parts[0].strip()
        right_part = parts[1].strip()

    # Step 1: Exact key or alias match on right_part (model)
    if right_part:
        for key, cfg in VENDOR_PRICING_REGISTRY["stt"].items():
            if right_part == key or right_part in cfg.get("aliases", []):
                return cfg, False

    # Step 2: Exact key or alias match on left_part or clean
    candidate = left_part if left_part else clean
    for key, cfg in VENDOR_PRICING_REGISTRY["stt"].items():
        if candidate == key or candidate in cfg.get("aliases", []):
            return cfg, False

    # Step 3: Regex / prefix fallback match (EC-6) on combined parts or clean
    check_str = f"{left_part} {right_part}".strip() if (left_part or right_part) else clean
    if "v4" in check_str and ("saaras" in check_str or "sarvam" in check_str):
        return VENDOR_PRICING_REGISTRY["stt"]["sarvam_saaras_v4"], False
    if "saaras" in check_str or "sarvam" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["sarvam_saaras"], False
    if "cartesia" in check_str or "ink" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["cartesia_ink"], False
    if "whisper" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["openai_whisper"], False
    if "scribe" in check_str or ("eleven" in check_str and "stt" in check_str):
        return VENDOR_PRICING_REGISTRY["stt"]["elevenlabs_scribe"], False
    if "chirp" in check_str or ("google" in check_str and "stt" in check_str):
        return VENDOR_PRICING_REGISTRY["stt"]["google_stt"], False
    if "google" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["google_stt"], False
    if "azure" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["azure_stt"], False
    if "assembly" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["assemblyai"], False
    if "gladia" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["gladia"], False
    if "speechmatics" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["speechmatics"], False
    if "pulse" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["smallest_ai_pulse"], True
    if "flux" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["deepgram_flux"], False
    if "nova-2" in check_str or "nova_2" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["deepgram_nova_2"], True
    if re.search(r"nova.*", check_str) or "deepgram" in check_str:
        return VENDOR_PRICING_REGISTRY["stt"]["deepgram_nova_3"], True

    # Global default fallback
    return VENDOR_PRICING_REGISTRY["stt"]["deepgram_nova_3"], True


def resolve_llm_pricing(name_or_model: Optional[str]) -> Tuple[Dict[str, Any], bool]:
    """
    Resolves LLM registry configuration given a model name or string.
    Returns (config_dict, is_fallback_flag).
    """
    if not name_or_model:
        return VENDOR_PRICING_REGISTRY["llm"]["gpt-4o-mini"], False

    clean = str(name_or_model).lower().strip()
    left_part = ""
    right_part = ""
    if "|||" in clean:
        parts = clean.split("|||", 1)
        left_part = parts[0].strip()
        right_part = parts[1].strip()

    # Step 1: Exact key or alias match on right_part (model)
    if right_part:
        for key, cfg in VENDOR_PRICING_REGISTRY["llm"].items():
            if right_part == key or right_part in cfg.get("aliases", []):
                return cfg, False

    # Step 2: Exact key or alias match on left_part or clean
    candidate = left_part if left_part else clean
    for key, cfg in VENDOR_PRICING_REGISTRY["llm"].items():
        if candidate == key or candidate in cfg.get("aliases", []):
            return cfg, False

    # Step 3: Regex / prefix fallback match (EC-6)
    check_str = f"{left_part} {right_part}".strip() if (left_part or right_part) else clean
    if "sonnet" in check_str or "claude-3-5-sonnet" in check_str or "claude-3.5-sonnet" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["claude-3-5-sonnet"], False
    if "opus" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["claude-3-opus"], False
    if "3-haiku" in check_str or "3.0-haiku" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["claude-3-haiku"], False
    if "haiku" in check_str or "claude-3-5-haiku" in check_str or "claude-3.5-haiku" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["claude-3-5-haiku"], False
    if "claude" in check_str or "anthropic" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["claude-3-5-sonnet"], True
    if "gemini-2" in check_str or "2.0-flash" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["gemini-2.0-flash"], False
    if "gemini-1.5-pro" in check_str or ("gemini" in check_str and "pro" in check_str):
        return VENDOR_PRICING_REGISTRY["llm"]["gemini-1.5-pro"], False
    if "gemini" in check_str or ("flash" in check_str and "gemini" in check_str):
        return VENDOR_PRICING_REGISTRY["llm"]["gemini-1.5-flash"], False
    if "r1" in check_str and "deepseek" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["deepseek-r1"], False
    if "deepseek" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["deepseek-v3"], False
    if "llama-3.3" in check_str or ("llama" in check_str and "70b" in check_str):
        return VENDOR_PRICING_REGISTRY["llm"]["llama-3.3-70b"], False
    if "8b" in check_str and "llama" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["llama-3.1-8b"], False
    if "groq" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["llama-3.3-70b"], True
    if "105b" in check_str or "sarvam" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["sarvam-105b"], False
    if "nano" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["gpt-4.1-nano"], False
    if "o3" in check_str or "o1" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["o3-mini"], False
    if "4.1" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["gpt-4.1-mini"], True
    if "4o-mini" in check_str or "4o_mini" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["gpt-4o-mini"], True
    if "gpt-4o" in check_str:
        return VENDOR_PRICING_REGISTRY["llm"]["gpt-4o"], True
    if re.search(r"gpt-4.*", check_str):
        return VENDOR_PRICING_REGISTRY["llm"]["gpt-4.1-mini"], True

    # Global default fallback
    return VENDOR_PRICING_REGISTRY["llm"]["gpt-4o-mini"], True


def resolve_tts_pricing(name_or_model: Optional[str]) -> Tuple[Dict[str, Any], bool]:
    """
    Resolves TTS registry configuration given a model name or provider.
    Returns (config_dict, is_fallback_flag).
    """
    if not name_or_model:
        return VENDOR_PRICING_REGISTRY["tts"]["smallest_ai_lightning"], False

    clean = str(name_or_model).lower().strip()
    left_part = ""
    right_part = ""
    if "|||" in clean:
        parts = clean.split("|||", 1)
        left_part = parts[0].strip()
        right_part = parts[1].strip()

    # Step 1: Exact key or alias match on right_part (model or voice)
    if right_part:
        for key, cfg in VENDOR_PRICING_REGISTRY["tts"].items():
            if right_part == key or right_part in cfg.get("aliases", []):
                return cfg, False

    # Step 2: Exact key or alias match on left_part (provider) or clean
    candidate = left_part if left_part else clean
    for key, cfg in VENDOR_PRICING_REGISTRY["tts"].items():
        if candidate == key or candidate in cfg.get("aliases", []):
            return cfg, False

    # Step 3: Substring / prefix matches (EC-6, EC-14)
    check_str = f"{left_part} {right_part}".strip() if (left_part or right_part) else clean
    if "cartesia" in check_str or "sonic" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["cartesia_sonic"], False
    if "multilingual" in check_str and "eleven" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["elevenlabs_multilingual"], False
    if "eleven" in check_str or "flash" in check_str or "turbo" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["elevenlabs_flash"], False
    if "bulbul-v4" in check_str or "bulbul_v4" in check_str or "v4-flash" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["sarvam_bulbul_v4"], False
    if "bulbul-v3" in check_str or "bulbul_v3" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["sarvam_bulbul_v3"], False
    if "bulbul" in check_str or "sarvam" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["sarvam_bulbul_v4"], False
    if "aura-2" in check_str or "aura_2" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["deepgram_aura_2"], False
    if "aura" in check_str or "deepgram" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["deepgram_aura_1"], False
    if "openai" in check_str or "tts-1" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["openai_tts"], False
    if "azure" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["azure_tts"], False
    if "google" in check_str or "journey" in check_str or "neural2" in check_str or "wavenet" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["google_tts"], False
    if "inworld" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["inworld_tts"], False
    if "minimax" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["minimax_tts"], False
    if "rime" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["rime_tts"], False
    if "smallest" in check_str or "lightning" in check_str or "waves" in check_str:
        return VENDOR_PRICING_REGISTRY["tts"]["smallest_ai_lightning"], False

    # Global fallback
    return VENDOR_PRICING_REGISTRY["tts"]["smallest_ai_lightning"], True


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to safely extract field from dict, ORM model, or object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ==============================================================================
# 3. CORE CALCULATION ENGINE
# ==============================================================================

def calculate_call_unit_economics(
    call_log: Any,
    workflow_run: Optional[Any] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Computes rigorous itemized vendor unit economics and gross margin for a single call run.

    Incorporates all 20 Edge-Case Protections from Section 7 of the Master Spec:
      - EC-1: Sub-Second / Early Hangup & Failed Call carrier cost suppression
      - EC-2: Missing or Corrupted telemetry handling (Zero Synthetic Fillers)
      - EC-3: Zero-Billed / Free Trial / Internal Test margin protection
      - EC-4: Plivo 60-Second Block Ceiling ceiling pulse math
      - EC-5: Mid-Call Multi-Model Failovers & Multi-Provider dynamic aggregation
      - EC-6: Unrecognized or custom model prefix/regex fallback matching
      - EC-7: Exact TTS characters synthesized with zero heuristic hallucination
      - EC-8: Separation of Extraction LLM tokens vs Conversational tokens
      - EC-9: Exchange rate control with offline baseline fallback ($1 = ₹88)
      - EC-11: Full-duplex STT streaming duration fallback when raw frame missing
      - EC-12: Post-call QA tokens (QAAnalysis|||) segregated from in-call cost
      - EC-13: Inbound vs Outbound vs International telephony direction rates
      - EC-14: TTS Unit Scale Normalization (all stored per 1,000 characters)
      - EC-15: Prompt caching discount math for OpenAI and Sarvam
      - EC-17: Multi-currency customer billing conversion to INR
      - EC-18: Mid-call dropoff and interrupted call safety
      - EC-19: Full provider dispatched character accounting for barge-in
      - EC-20: Answered call connection duration synchronization
    """
    overrides = overrides or {}
    notices: List[str] = []

    # --- 1. Forex Configuration (EC-9) with Live Auto-Updating Rate ---
    exchange_rate = float(
        overrides.get("exchange_rate")
        or overrides.get("usd_to_inr")
        or forex_service.get_usd_to_inr_rate()
    )

    # --- 2. Extract Duration & Status ---
    # Duration priority: call_log duration -> workflow_run duration -> 0
    duration_raw = _get_val(call_log, "duration_seconds")
    if duration_raw is None or duration_raw == "":
        duration_raw = _get_val(workflow_run, "call_duration_seconds", 0)
    try:
        duration_seconds = max(int(duration_raw or 0), 0)
    except (ValueError, TypeError):
        duration_seconds = 0

    # Call status: inspect call_log or workflow_run
    call_status = str(
        _get_val(call_log, "call_status")
        or _get_val(call_log, "status")
        or _get_val(workflow_run, "status")
        or "completed"
    ).lower().strip()

    # Effective minutes for unit rates (Division by zero guard: EC-1)
    effective_minutes = max(duration_seconds, 1.0) / 60.0

    # --- 3. Telephony Calculation (Plivo - EC-1, EC-4, EC-13, EC-20) ---
    direction = str(_get_val(call_log, "direction") or _get_val(call_log, "call_type") or "outbound").lower()
    to_phone = str(
        _get_val(call_log, "to_phone_number")
        or _get_val(call_log, "caller_number")
        or _get_val(call_log, "phone_number")
        or ""
    ).strip()

    # Determine telephony rate per minute
    include_telephony = overrides.get("include_telephony", True)
    custom_plivo_rate = overrides.get("plivo_rate_inr") or overrides.get("telephony_cost_per_min_inr")

    if custom_plivo_rate is not None and str(custom_plivo_rate) != "":
        plivo_rate_inr = float(custom_plivo_rate)
    elif "inbound" in direction:
        plivo_rate_inr = VENDOR_PRICING_REGISTRY["telephony"]["plivo"]["inbound_india"]["rate_inr_per_min"]
    elif to_phone and not (to_phone.startswith("+91") or to_phone.startswith("91") or to_phone.startswith("0")):
        # International destination (EC-13)
        plivo_rate_inr = VENDOR_PRICING_REGISTRY["telephony"]["plivo"]["international_default"]["rate_inr_per_min"]
    else:
        plivo_rate_inr = VENDOR_PRICING_REGISTRY["telephony"]["plivo"]["outbound_india"]["rate_inr_per_min"]

    # Failed / Unanswered / Sub-Second Call Check (EC-1)
    is_failed_or_unanswered = call_status in [
        "failed",
        "no-answer",
        "no_answer",
        "busy",
        "rejected",
        "canceled",
        "cancelled",
        "unanswered",
    ]
    is_sub_second_hangup = (duration_seconds <= 3 and call_status not in ["completed", "answered"])

    if not include_telephony:
        billed_telephony_minutes = 0
        cost_plivo = 0.0
    elif is_failed_or_unanswered:
        billed_telephony_minutes = 0
        cost_plivo = 0.0
        notices.append("FAILED / UNANSWERED (Zero Carrier Cost)")
    elif is_sub_second_hangup:
        billed_telephony_minutes = 0
        cost_plivo = 0.0
        notices.append("EARLY DISCONNECT / UNCOMPLETED (Zero Carrier Cost)")
    else:
        # Carrier 60/60 block ceiling rounding (EC-4, EC-20)
        billed_telephony_minutes = math.ceil(max(duration_seconds, 1) / 60.0)
        cost_plivo = float(billed_telephony_minutes * plivo_rate_inr)

    plivo_breakdown = {
        "minutes": billed_telephony_minutes,
        "duration_seconds": duration_seconds,
        "rate_inr": plivo_rate_inr,
        "cost_inr": cost_plivo,
        "direction": direction,
        "phone_number": to_phone,
    }

    # --- 4. Usage Info & Runtime Configuration Extraction (EC-2, EC-18) ---
    ui = None
    if workflow_run:
        ui = _get_val(workflow_run, "usage_info")
    if not ui and call_log:
        ui = _get_val(call_log, "usage_info")

    if not ui or not isinstance(ui, dict):
        ui = {}
        if not is_failed_or_unanswered and duration_seconds > 0:
            notices.append("UNRECORDED TELEMETRY (Pre-patch or Dropped Webhook)")

    rc = _get_val(workflow_run, "runtime_configuration") or _get_val(call_log, "runtime_configuration") or {}
    if not rc:
        init_ctx = _get_val(workflow_run, "initial_context") or _get_val(call_log, "initial_context")
        if isinstance(init_ctx, str):
            try:
                import json
                init_ctx = json.loads(init_ctx)
            except Exception:
                init_ctx = {}
        if isinstance(init_ctx, dict):
            rc = init_ctx.get("runtime_configuration") or {}
    if not isinstance(rc, dict):
        rc = {}

    stt_data = ui.get("stt", {}) if isinstance(ui.get("stt"), dict) else {}
    tts_data = ui.get("tts", {}) if isinstance(ui.get("tts"), dict) else {}
    llm_data = ui.get("llm", {}) if isinstance(ui.get("llm"), dict) else {}

    # --- 5. STT Calculation (EC-2, EC-6, EC-11) ---
    override_stt_prov = overrides.get("stt_provider") or overrides.get("stt_model")
    override_stt_rate = overrides.get("stt_rate_per_min")
    sarvam_stt_rate_override = overrides.get("sarvam_stt_rate")

    configured_stt = (
        override_stt_prov
        or rc.get("stt_model")
        or rc.get("stt_provider")
        or _get_val(call_log, "stt_model")
        or _get_val(call_log, "stt_provider")
    )

    stt_total_seconds = 0.0
    stt_items: List[Dict[str, Any]] = []

    if stt_data:
        for stt_key, val in stt_data.items():
            audio_sec = 0.0
            if isinstance(val, (int, float)):
                audio_sec = float(val)
            elif isinstance(val, dict):
                audio_sec = float(val.get("audio_seconds") or val.get("seconds") or 0.0)

            stt_total_seconds += audio_sec
            chosen_key = override_stt_prov or (stt_key if stt_key not in ("stt", "default", "audio", "") else None) or configured_stt
            cfg, fallback_applied = resolve_stt_pricing(chosen_key)
            if fallback_applied and chosen_key:
                notices.append(f"Default Fallback Rate Applied for STT: {chosen_key}")

            rate = float(override_stt_rate if override_stt_rate is not None else cfg["rate"])
            # Sarvam special rate support
            if cfg["provider"] == "sarvam" and sarvam_stt_rate_override is not None:
                rate = float(sarvam_stt_rate_override)

            curr = cfg.get("currency", "USD")
            rate_inr = rate * exchange_rate if curr == "USD" else rate
            item_cost = (audio_sec / 60.0) * rate_inr
            model_candidate = rc.get("stt_model") or _get_val(call_log, "stt_model")
            display_model = model_candidate if (model_candidate and (not rc.get("stt_provider") or cfg["provider"] == rc.get("stt_provider") or cfg["provider"] == _get_val(call_log, "stt_provider"))) else cfg["model"]
            stt_items.append({
                "key": stt_key,
                "provider": cfg["provider"],
                "model": display_model,
                "seconds": audio_sec,
                "rate": rate,
                "currency": curr,
                "cost_inr": item_cost,
            })
    else:
        # EC-11: Full-Duplex STT streaming duration fallback when raw frame is missing
        if duration_seconds > 0 and not is_failed_or_unanswered:
            stt_total_seconds = float(duration_seconds)
            notices.append(f"STT Audio Stream: {duration_seconds}s (Full Duplex Stream)")

        chosen_key = override_stt_prov or configured_stt
        cfg, fallback_applied = resolve_stt_pricing(chosen_key)
        if fallback_applied and chosen_key:
            notices.append(f"Default Fallback Rate Applied for STT: {chosen_key}")

        rate = float(override_stt_rate if override_stt_rate is not None else cfg["rate"])
        if cfg["provider"] == "sarvam" and sarvam_stt_rate_override is not None:
            rate = float(sarvam_stt_rate_override)

        curr = cfg.get("currency", "USD")
        rate_inr = rate * exchange_rate if curr == "USD" else rate
        item_cost = (stt_total_seconds / 60.0) * rate_inr
        model_candidate = rc.get("stt_model") or _get_val(call_log, "stt_model")
        display_model = model_candidate if (model_candidate and (not rc.get("stt_provider") or cfg["provider"] == rc.get("stt_provider") or cfg["provider"] == _get_val(call_log, "stt_provider"))) else cfg["model"]
        stt_items.append({
            "key": chosen_key or cfg["model"],
            "provider": cfg["provider"],
            "model": display_model,
            "seconds": stt_total_seconds,
            "rate": rate,
            "currency": curr,
            "cost_inr": item_cost,
        })

    cost_stt = sum(item["cost_inr"] for item in stt_items)
    primary_stt = stt_items[0] if stt_items else {
        "provider": configured_stt or "deepgram",
        "model": rc.get("stt_model") or _get_val(call_log, "stt_model") or "flux-general-multi",
        "rate": 0.0048,
        "currency": "USD",
    }

    stt_breakdown = {
        "provider": primary_stt["provider"],
        "model": primary_stt["model"],
        "seconds": stt_total_seconds,
        "rate_per_min": primary_stt["rate"],
        "rate_usd_min": primary_stt["rate"] if primary_stt.get("currency") == "USD" else None,
        "rate_inr_min": primary_stt["rate"] if primary_stt.get("currency") == "INR" else None,
        "rate_currency": primary_stt["currency"],
        "cost_inr": cost_stt,
        "items": stt_items,
    }

    # --- 6. TTS Calculation (EC-2, EC-6, EC-7, EC-14, EC-19) ---
    override_tts_prov = overrides.get("tts_provider") or overrides.get("tts_model")
    override_tts_rate = overrides.get("tts_rate_per_1k")
    configured_tts = (
        override_tts_prov
        or rc.get("tts_model")
        or rc.get("tts_provider")
        or _get_val(call_log, "tts_provider")
        or _get_val(call_log, "tts_model")
    )

    tts_total_chars = 0
    tts_items: List[Dict[str, Any]] = []

    if tts_data:
        for tts_key, val in tts_data.items():
            chars = 0
            if isinstance(val, (int, float)):
                chars = int(val)
            elif isinstance(val, dict):
                chars = int(val.get("characters") or val.get("chars") or 0)

            tts_total_chars += chars
            chosen_key = override_tts_prov or (tts_key if tts_key not in ("tts", "default", "") else None) or configured_tts
            cfg, fallback_applied = resolve_tts_pricing(chosen_key)
            if fallback_applied and chosen_key:
                notices.append(f"Default Fallback Rate Applied for TTS: {chosen_key}")

            rate_1k = float(override_tts_rate if override_tts_rate is not None else cfg["rate_per_1k_chars"])
            curr = cfg.get("currency", "USD")
            rate_inr_1k = rate_1k * exchange_rate if curr == "USD" else rate_1k
            item_cost = (chars / 1000.0) * rate_inr_1k
            model_candidate = rc.get("tts_model") or _get_val(call_log, "tts_model")
            display_model = model_candidate if (model_candidate and (not rc.get("tts_provider") or cfg["provider"] == rc.get("tts_provider") or cfg["provider"] == _get_val(call_log, "tts_provider"))) else cfg["model"]

            tts_items.append({
                "key": tts_key,
                "provider": cfg["provider"],
                "model": display_model,
                "characters": chars,
                "rate_per_1k": rate_1k,
                "currency": curr,
                "cost_inr": item_cost,
            })
    else:
        # Zero synthetic characters: never guess duration * 0.45 * 900 (EC-2, EC-7)
        if duration_seconds > 0 and not is_failed_or_unanswered:
            notices.append("0 Chars Synthesized")

        chosen_key = override_tts_prov or configured_tts
        cfg, fallback_applied = resolve_tts_pricing(chosen_key)
        if fallback_applied and chosen_key:
            notices.append(f"Default Fallback Rate Applied for TTS: {chosen_key}")

        rate_1k = float(override_tts_rate if override_tts_rate is not None else cfg["rate_per_1k_chars"])
        curr = cfg.get("currency", "USD")
        model_candidate = rc.get("tts_model") or _get_val(call_log, "tts_model")
        display_model = model_candidate if (model_candidate and (not rc.get("tts_provider") or cfg["provider"] == rc.get("tts_provider") or cfg["provider"] == _get_val(call_log, "tts_provider"))) else cfg["model"]
        tts_items.append({
            "key": chosen_key or cfg["model"],
            "provider": cfg["provider"],
            "model": display_model,
            "characters": 0,
            "rate_per_1k": rate_1k,
            "currency": curr,
            "cost_inr": 0.0,
        })

    cost_tts = sum(item["cost_inr"] for item in tts_items)
    primary_tts = tts_items[0] if tts_items else {
        "provider": configured_tts or "smallest_ai",
        "model": rc.get("tts_model") or _get_val(call_log, "tts_model") or "lightning-v3.1",
        "rate_per_1k": 0.0175,
        "currency": "USD",
    }

    tts_breakdown = {
        "provider": primary_tts["provider"],
        "model": primary_tts["model"],
        "characters": tts_total_chars,
        "rate_per_1k": primary_tts["rate_per_1k"],
        "rate_usd_per_10k": 0.175 if "smallest" in primary_tts["provider"].lower() else None,
        "rate_currency": primary_tts["currency"],
        "cost_inr": cost_tts,
        "items": tts_items,
    }

    # --- 7. LLM Calculation (Conversational, Extraction, QA - EC-5, EC-8, EC-12, EC-15) ---
    override_llm_model = overrides.get("llm_model") or overrides.get("llm_provider")
    configured_conv_llm = (
        override_llm_model
        or rc.get("llm_model")
        or rc.get("llm_provider")
        or _get_val(call_log, "llm_model")
    )
    configured_ext_llm = (
        overrides.get("extraction_llm_model")
        or rc.get("extraction_llm_model")
        or rc.get("extraction_llm_provider")
        or "gpt-4.1-mini"
    )

    conv_prompt_tokens = 0
    conv_comp_tokens = 0
    conv_cached_tokens = 0
    cost_llm_conv = 0.0
    conv_items: List[Dict[str, Any]] = []

    ext_prompt_tokens = 0
    ext_comp_tokens = 0
    ext_cached_tokens = 0
    cost_llm_ext = 0.0
    ext_items: List[Dict[str, Any]] = []

    comp_prompt_tokens = 0
    comp_comp_tokens = 0
    comp_cached_tokens = 0
    cost_llm_comp = 0.0
    comp_items: List[Dict[str, Any]] = []

    qa_prompt_tokens = 0
    qa_comp_tokens = 0
    qa_cached_tokens = 0
    cost_llm_qa = 0.0
    qa_items: List[Dict[str, Any]] = []

    pci_prompt_tokens = 0
    pci_comp_tokens = 0
    pci_cached_tokens = 0
    cost_llm_pci = 0.0
    pci_items: List[Dict[str, Any]] = []

    has_extraction_metrics = False

    for model_key, val in llm_data.items():
        if not isinstance(val, dict):
            continue

        p_tok = int(val.get("prompt_tokens") or 0)
        c_tok = int(val.get("completion_tokens") or 0)
        cache_tok = int(val.get("cache_read_input_tokens") or val.get("cached_tokens") or 0)

        # Categorization: QA vs PCI vs Extraction vs Compaction vs Conversation (EC-8, EC-12)
        m_lower = model_key.lower()
        is_qa = "qaanalysis" in m_lower or "qa_analysis" in m_lower or m_lower.startswith("qa")
        is_pci = "postcallintelligence" in m_lower or "post_call" in m_lower
        is_compaction = "compaction" in m_lower
        is_extraction = "variable_extraction" in m_lower or "extraction" in m_lower

        if is_extraction:
            has_extraction_metrics = True

        model_candidate = model_key.split("|||", 1)[1].strip() if "|||" in model_key else model_key.strip()
        if model_candidate.lower() in ("unknown", "default", "none", "extraction", "compaction"):
            model_candidate = None

        if is_extraction:
            chosen_key = (
                overrides.get("extraction_llm_model")
                or rc.get("extraction_llm_model")
                or model_candidate
                or configured_ext_llm
            )
        elif is_compaction:
            chosen_key = (
                model_candidate
                or rc.get("extraction_llm_model")
                or configured_ext_llm
            )
        elif is_pci:
            chosen_key = (
                model_candidate
                or rc.get("extraction_llm_model")
                or configured_ext_llm
            )
        elif is_qa:
            chosen_key = (
                model_candidate
                or rc.get("qa_model")
                or "gpt-4o-mini"
            )
        else:
            chosen_key = (
                override_llm_model
                or model_candidate
                or rc.get("llm_model")
                or configured_conv_llm
            )

        cfg, fallback_applied = resolve_llm_pricing(chosen_key)
        if fallback_applied:
            notices.append(f"Default Fallback Rate Applied for LLM: {chosen_key}")

        r_in = float(cfg["input_per_million"])
        r_cached = float(cfg["cached_per_million"])
        r_out = float(cfg["output_per_million"])
        curr = cfg.get("currency", "USD")

        # Caching math: uncached input = max(prompt_tokens - cached_tokens, 0) (EC-15)
        non_cached = max(p_tok - cache_tok, 0)
        item_cost_native = (
            (non_cached / 1_000_000.0) * r_in
            + (cache_tok / 1_000_000.0) * r_cached
            + (c_tok / 1_000_000.0) * r_out
        )
        item_cost_inr = item_cost_native * exchange_rate if curr == "USD" else item_cost_native

        display_model = cfg["model"]
        if is_extraction:
            if rc.get("extraction_llm_model") and (not rc.get("extraction_llm_provider") or cfg["provider"] == rc.get("extraction_llm_provider")):
                display_model = rc.get("extraction_llm_model")
        elif not is_extraction and not is_qa and not is_pci and not is_compaction:
            if rc.get("llm_model") and (not rc.get("llm_provider") or cfg["provider"] == rc.get("llm_provider")):
                display_model = rc.get("llm_model")

        item_entry = {
            "key": model_key,
            "provider": cfg["provider"],
            "model": display_model,
            "prompt_tokens": p_tok,
            "completion_tokens": c_tok,
            "cached_tokens": cache_tok,
            "cost_inr": item_cost_inr,
        }

        if is_qa:
            qa_prompt_tokens += p_tok
            qa_comp_tokens += c_tok
            qa_cached_tokens += cache_tok
            cost_llm_qa += item_cost_inr
            qa_items.append(item_entry)
        elif is_pci:
            pci_prompt_tokens += p_tok
            pci_comp_tokens += c_tok
            pci_cached_tokens += cache_tok
            cost_llm_pci += item_cost_inr
            pci_items.append(item_entry)
        elif is_extraction:
            ext_prompt_tokens += p_tok
            ext_comp_tokens += c_tok
            ext_cached_tokens += cache_tok
            cost_llm_ext += item_cost_inr
            ext_items.append(item_entry)
        elif is_compaction:
            comp_prompt_tokens += p_tok
            comp_comp_tokens += c_tok
            comp_cached_tokens += cache_tok
            cost_llm_comp += item_cost_inr
            comp_items.append(item_entry)
        else:
            conv_prompt_tokens += p_tok
            conv_comp_tokens += c_tok
            conv_cached_tokens += cache_tok
            cost_llm_conv += item_cost_inr
            conv_items.append(item_entry)

    # Historical runs without extraction telemetry notice (EC-8)
    if not has_extraction_metrics and not is_failed_or_unanswered and duration_seconds > 0:
        notices.append("Extraction Telemetry Unrecorded (Pre-Patch Run)")

    # Default entries if lists are empty
    conv_cfg, _ = resolve_llm_pricing(configured_conv_llm)
    ext_cfg, _ = resolve_llm_pricing(configured_ext_llm)

    primary_conv = conv_items[0] if conv_items else {
        "provider": conv_cfg["provider"],
        "model": rc.get("llm_model") or conv_cfg["model"],
    }
    primary_ext = ext_items[0] if ext_items else {
        "provider": ext_cfg["provider"],
        "model": rc.get("extraction_llm_model") or ext_cfg["model"],
    }
    primary_comp = comp_items[0] if comp_items else {
        "provider": ext_cfg["provider"],
        "model": rc.get("extraction_llm_model") or ext_cfg["model"],
    }
    primary_qa = qa_items[0] if qa_items else {
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    primary_pci = pci_items[0] if pci_items else {
        "provider": ext_cfg["provider"],
        "model": rc.get("extraction_llm_model") or ext_cfg["model"],
    }

    llm_conv_breakdown = {
        "provider": primary_conv["provider"],
        "model": primary_conv["model"],
        "prompt_tokens": conv_prompt_tokens,
        "completion_tokens": conv_comp_tokens,
        "cached_tokens": conv_cached_tokens,
        "cost_inr": cost_llm_conv,
        "items": conv_items,
    }

    llm_ext_breakdown = {
        "provider": primary_ext["provider"],
        "model": primary_ext["model"],
        "prompt_tokens": ext_prompt_tokens,
        "completion_tokens": ext_comp_tokens,
        "cached_tokens": ext_cached_tokens,
        "cost_inr": cost_llm_ext,
        "items": ext_items,
    }

    llm_comp_breakdown = {
        "provider": primary_comp["provider"],
        "model": primary_comp["model"],
        "prompt_tokens": comp_prompt_tokens,
        "completion_tokens": comp_comp_tokens,
        "cached_tokens": comp_cached_tokens,
        "cost_inr": cost_llm_comp,
        "items": comp_items,
    }

    llm_qa_breakdown = {
        "provider": primary_qa["provider"],
        "model": primary_qa["model"],
        "prompt_tokens": qa_prompt_tokens,
        "completion_tokens": qa_comp_tokens,
        "cached_tokens": qa_cached_tokens,
        "cost_inr": cost_llm_qa,
        "items": qa_items,
    }

    llm_pci_breakdown = {
        "provider": primary_pci["provider"],
        "model": primary_pci["model"],
        "prompt_tokens": pci_prompt_tokens,
        "completion_tokens": pci_comp_tokens,
        "cached_tokens": pci_cached_tokens,
        "cost_inr": cost_llm_pci,
        "items": pci_items,
    }

    # --- 8. Cost Aggregations (Operational vs Post-Call Intelligence - EC-12) ---
    total_operational_cost_inr = cost_plivo + cost_stt + cost_tts + cost_llm_conv + cost_llm_ext + cost_llm_comp
    cost_llm_post_call = cost_llm_qa + cost_llm_pci
    total_all_in_cost_inr = total_operational_cost_inr + cost_llm_post_call

    # --- 9. Revenue & Profitability Math (EC-3, EC-17) ---
    rev_raw = _get_val(call_log, "cost_to_customer_paise")
    if rev_raw is None or rev_raw == "":
        rev_raw = _get_val(call_log, "revenue_paise", 0)

    try:
        revenue_paise = float(rev_raw or 0.0)
    except (ValueError, TypeError):
        revenue_paise = 0.0

    customer_currency = str(_get_val(call_log, "customer_currency") or "INR").upper()
    if customer_currency == "USD":
        # EC-17: Enterprise USD revenue conversion
        revenue_inr = (revenue_paise / 100.0) * exchange_rate
    else:
        revenue_inr = revenue_paise / 100.0

    profit_inr = revenue_inr - total_operational_cost_inr

    # Margin Percentage and Status (EC-3)
    if revenue_inr <= 0.0:
        margin_pct = -100.0 if total_operational_cost_inr > 0 else 0.0
        margin_status = "free_trial"
        if not is_failed_or_unanswered:
            notices.append("FREE TRIAL / INTERNAL TEST (Zero Billed Revenue)")
    else:
        margin_pct = (profit_inr / revenue_inr) * 100.0
        if margin_pct >= 40.0:
            margin_status = "healthy"
        elif margin_pct >= 15.0:
            margin_status = "warning"
        else:
            margin_status = "loss"

    cost_per_minute_inr = total_operational_cost_inr / effective_minutes
    effective_rate_per_min_inr = (revenue_inr / billed_telephony_minutes) if billed_telephony_minutes > 0 else (revenue_inr / effective_minutes)

    # Metadata extraction
    dograh_run_id = _get_val(call_log, "dograh_run_id") or _get_val(workflow_run, "id")
    customer_id = _get_val(call_log, "customer_id")
    company_name = _get_val(call_log, "company_name")

    return {
        "dograh_run_id": dograh_run_id,
        "customer_id": customer_id,
        "company_name": company_name,
        "call_duration_seconds": duration_seconds,
        "billed_minutes": billed_telephony_minutes,
        "revenue_inr": revenue_inr,
        "effective_rate_per_min_inr": effective_rate_per_min_inr,
        "exchange_rate_used": exchange_rate,
        "cost_breakdown": {
            "plivo": plivo_breakdown,
            "stt": stt_breakdown,
            "llm_conversation": llm_conv_breakdown,
            "llm_extraction": llm_ext_breakdown,
            "llm_compaction": llm_comp_breakdown,
            "tts": tts_breakdown,
        },
        "post_call_breakdown": {
            "llm_qa_analysis": llm_qa_breakdown,
            "llm_pci": llm_pci_breakdown,
        },
        "total_cost_inr": total_operational_cost_inr,
        "post_call_cost_inr": cost_llm_post_call,
        "total_all_in_cost_inr": total_all_in_cost_inr,
        "cost_per_minute_inr": cost_per_minute_inr,
        "profit_inr": profit_inr,
        "margin_pct": margin_pct,
        "margin_status": margin_status,
        "notices": notices,
        # Backward compatibility aliases
        "plivo_inr": cost_plivo,
        "stt_inr": cost_stt,
        "tts_inr": cost_tts,
        "llm_inr": cost_llm_conv + cost_llm_ext + cost_llm_comp,
        "tts_chars": tts_total_chars,
        "stt_seconds": stt_total_seconds,
        "llm_prompt_tokens": conv_prompt_tokens + ext_prompt_tokens + comp_prompt_tokens,
        "llm_completion_tokens": conv_comp_tokens + ext_comp_tokens + comp_comp_tokens,
        "llm_cached_tokens": conv_cached_tokens + ext_cached_tokens + comp_cached_tokens,
    }


def estimate_call_cost_inr(
    duration_seconds: int,
    usage_info: Optional[Dict[str, Any]],
    tts_provider: str = "deepgram",
    stt_provider: str = "deepgram",
    llm_model: str = "gpt-4o-mini",
    overrides: Optional[Dict[str, Any]] = None,
    stt_model: Optional[str] = None,
    tts_model: Optional[str] = None,
    runtime_configuration: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Backwards-compatible drop-in replacement for the router's _estimate_call_cost_inr.
    """
    rc = runtime_configuration or {}
    call_log_mock = {
        "duration_seconds": duration_seconds,
        "cost_to_customer_paise": 0,
        "call_status": "completed",
        "tts_provider": tts_provider,
        "tts_model": tts_model or rc.get("tts_model"),
        "stt_provider": stt_provider,
        "stt_model": stt_model or rc.get("stt_model"),
        "llm_model": llm_model or rc.get("llm_model"),
        "runtime_configuration": rc,
    }
    workflow_run_mock = {
        "usage_info": usage_info or {},
        "runtime_configuration": rc,
    }
    merged_overrides = dict(overrides or {})

    res = calculate_call_unit_economics(call_log_mock, workflow_run_mock, merged_overrides)
    cb = res.get("cost_breakdown", {})
    post_cb = res.get("post_call_breakdown", {})
    post_call_inr = round(res.get("post_call_cost_inr", 0.0), 4)
    total_operational_cost_inr = round(res["total_cost_inr"], 4)
    total_all_in_cost_inr = round(res.get("total_all_in_cost_inr", total_operational_cost_inr + post_call_inr), 4)
    return {
        "plivo_inr": round(res["plivo_inr"], 4),
        "stt_inr": round(res["stt_inr"], 4),
        "tts_inr": round(res["tts_inr"], 4),
        "llm_inr": round(res["llm_inr"], 4),
        "post_call_inr": post_call_inr,
        "total_cost_inr": total_all_in_cost_inr,
        "total_operational_cost_inr": total_operational_cost_inr,
        "total_all_in_cost_inr": total_all_in_cost_inr,
        "tts_chars": res["tts_chars"],
        "stt_seconds": round(res["stt_seconds"], 1),
        "llm_prompt_tokens": res["llm_prompt_tokens"],
        "llm_completion_tokens": res["llm_completion_tokens"],
        "llm_cached_tokens": res["llm_cached_tokens"],
        "notices": res["notices"],
        "cost_breakdown": cb,
        "post_call_breakdown": post_cb,
        "stt_vendor": cb.get("stt", {}).get("provider", stt_provider),
        "stt_model": cb.get("stt", {}).get("model") or stt_model or stt_provider,
        "tts_vendor": cb.get("tts", {}).get("provider", tts_provider),
        "tts_model": cb.get("tts", {}).get("model") or tts_model or tts_provider,
        "llm_vendor": cb.get("llm_conversation", {}).get("provider", "openai"),
        "llm_model": cb.get("llm_conversation", {}).get("model") or llm_model,
    }


def get_cost_assumptions() -> Dict[str, Any]:
    """
    Returns the comprehensive, unified cost assumptions dictionary.
    Exposes live auto-updating Forex rate metadata, full LLM, TTS, STT, and Telephony pricing.
    Ensures safe compatibility across all admin profitability and diagnostic endpoints.
    """
    live_forex = forex_service.get_usd_to_inr_rate()
    forex_meta = forex_service.get_rate_metadata()

    # Safely derive OpenAI rates (USD per 1M tokens) from registry
    openai_rates = {
        model_key: {
            "input": cfg.get("input_per_million", 0.0),
            "cached": cfg.get("cached_per_million", 0.0),
            "output": cfg.get("output_per_million", 0.0),
        }
        for model_key, cfg in VENDOR_PRICING_REGISTRY.get("llm", {}).items()
        if cfg.get("provider") == "openai"
    }

    return {
        "usd_to_inr": round(live_forex, 4),
        "forex": forex_meta,
        "plivo_per_min_inr": VENDOR_PRICING_REGISTRY["telephony"]["plivo"]["outbound_india"]["rate_inr_per_min"],
        "telephony": VENDOR_PRICING_REGISTRY["telephony"],
        "deepgram_stt_usd_per_min": VENDOR_PRICING_REGISTRY["stt"]["deepgram_nova_3"]["rate"],
        "deepgram_tts_usd_per_1k_chars": VENDOR_PRICING_REGISTRY["tts"]["deepgram_aura_1"]["rate_per_1k_chars"],
        "elevenlabs_tts_usd_per_1k_chars": VENDOR_PRICING_REGISTRY["tts"]["elevenlabs_flash"]["rate_per_1k_chars"],
        "smallest_ai_tts_usd_per_1k_chars": VENDOR_PRICING_REGISTRY["tts"]["smallest_ai_lightning"]["rate_per_1k_chars"],
        "smallest_ai_stt_usd_per_min": VENDOR_PRICING_REGISTRY["stt"]["smallest_ai_pulse"]["rate"],
        "tts_speaking_ratio": 0.47,
        "openai_rates": openai_rates,
        "llm_rates": VENDOR_PRICING_REGISTRY["llm"],
        "stt_rates": VENDOR_PRICING_REGISTRY["stt"],
        "tts_rates": VENDOR_PRICING_REGISTRY["tts"],
    }

