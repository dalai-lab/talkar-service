"""
services/forex_service.py
=========================
Production-grade live USD to INR foreign exchange (Forex) rate service.

Features:
  - Multi-CDN & Multi-API Waterfall Failover:
      1. ExchangeRate-API Open v6 (open.er-api.com)
      2. European Central Bank via Frankfurter (api.frankfurter.dev)
      3. Fawaz Ahmed Currency API via jsDelivr CDN
      4. Cloudflare Pages Currency Mirror (latest.currency-api.pages.dev)
  - Zero-Latency Read Path:
      Non-blocking in-memory cache lookup (<0.001ms), safe for high-frequency
      billing loops and call audit computations.
  - Stale-While-Revalidate (SWR):
      When cache expires, immediately returns the active/stale rate and triggers
      an asynchronous background refresh without blocking callers.
  - Strict Sanity Bounds:
      Validates exchange rate within [70.0, 130.0] INR to guard against corrupt
      or inverted upstream API responses.
  - Offline / Network Outage Resilience:
      Degrades gracefully to the configured offline baseline (e.g. 88.0 or 94.0).
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("talkar.forex")

DEFAULT_BASELINE_USD_TO_INR: float = 88.0
MIN_SANITY_RATE: float = 70.0
MAX_SANITY_RATE: float = 130.0
CACHE_TTL_SECONDS: float = 10800.0  # 3 hours (Forex fixings update daily/hourly)


@dataclass
class ForexRateEntry:
    rate: float
    provider: str
    updated_at: float
    expires_at: float
    is_fallback: bool = False
    source_url: str = ""


class ForexService:
    """
    Thread-safe, non-blocking USD-to-INR live rate manager.
    """

    def __init__(self, baseline_rate: float = DEFAULT_BASELINE_USD_TO_INR):
        self.baseline_rate = float(baseline_rate)
        self._cache: Optional[ForexRateEntry] = None
        self._lock = asyncio.Lock()
        self._is_refreshing = False

        # Waterfall provider definitions: (Name, URL, parser_function)
        self._providers: List[Tuple[str, str, Callable[[Dict[str, Any]], float]]] = [
            (
                "open.er-api.com",
                "https://open.er-api.com/v6/latest/USD",
                lambda d: float(d["rates"]["INR"]),
            ),
            (
                "frankfurter",
                "https://api.frankfurter.dev/v1/latest?base=USD&symbols=INR",
                lambda d: float(d["rates"]["INR"]),
            ),
            (
                "fawazahmed0-jsdelivr",
                "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.min.json",
                lambda d: float(d["usd"]["inr"]),
            ),
            (
                "fawazahmed0-pages",
                "https://latest.currency-api.pages.dev/v1/currencies/usd.min.json",
                lambda d: float(d["usd"]["inr"]),
            ),
        ]

    def get_usd_to_inr_rate(self) -> float:
        """
        Ultra-fast synchronous lookup (<0.001 ms).
        Always returns an active rate immediately without network I/O.
        """
        now = time.time()

        # 1. Warm fresh cache
        if self._cache and now < self._cache.expires_at:
            return self._cache.rate

        # 2. Stale cache: return stale rate immediately & trigger background refresh
        if self._cache:
            self._trigger_background_refresh()
            return self._cache.rate

        # 3. Uninitialized cache: return baseline rate & trigger background refresh
        self._trigger_background_refresh()
        return self.baseline_rate

    def get_rate_metadata(self) -> Dict[str, Any]:
        """
        Returns full inspection metadata for admin diagnostics and dashboards.
        """
        rate = self.get_usd_to_inr_rate()
        now = time.time()
        return {
            "rate": rate,
            "provider": self._cache.provider if self._cache else "baseline_fallback",
            "source_url": self._cache.source_url if self._cache else "",
            "is_live": bool(self._cache and not self._cache.is_fallback),
            "is_fallback": self._cache.is_fallback if self._cache else True,
            "last_updated": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(self._cache.updated_at if self._cache else now),
            ),
            "expires_in_seconds": max(0, int((self._cache.expires_at - now) if self._cache else 0)),
            "baseline_rate": self.baseline_rate,
        }

    def _trigger_background_refresh(self) -> None:
        """Schedules non-blocking async refresh if an event loop is running."""
        if not self._is_refreshing:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.refresh_rate())
            except RuntimeError:
                pass  # Outside running event loop (e.g. sync scripts / tests)

    async def refresh_rate(self) -> float:
        """
        Queries the failover waterfall, applies sanity validation, and updates memory cache.
        """
        if self._lock.locked():
            return self._cache.rate if self._cache else self.baseline_rate

        async with self._lock:
            self._is_refreshing = True
            try:
                timeout = httpx.Timeout(connect=2.5, read=3.0, write=1.5, pool=4.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    for name, url, parser in self._providers:
                        try:
                            resp = await client.get(url, headers={"User-Agent": "Talkar-Forex-Poller/1.0"})
                            if resp.status_code == 200:
                                data = resp.json()
                                parsed_rate = parser(data)

                                # Sanity Bounds Guardrail
                                if not (MIN_SANITY_RATE <= parsed_rate <= MAX_SANITY_RATE):
                                    logger.warning(
                                        f"[FOREX] Provider '{name}' returned out-of-bounds rate {parsed_rate:.4f}. Skipping."
                                    )
                                    continue

                                now = time.time()
                                self._cache = ForexRateEntry(
                                    rate=round(parsed_rate, 4),
                                    provider=name,
                                    updated_at=now,
                                    expires_at=now + CACHE_TTL_SECONDS,
                                    is_fallback=False,
                                    source_url=url,
                                )
                                logger.info(
                                    f"[FOREX] Live USD->INR exchange rate updated: ₹{parsed_rate:.4f} via {name}."
                                )
                                return self._cache.rate
                        except Exception as exc:
                            logger.warning(f"[FOREX] Provider '{name}' failed ({exc}); trying next provider.")

                # If all external providers fail:
                if self._cache:
                    logger.error("[FOREX] All external forex APIs failed. Extending stale cache.")
                    self._cache.expires_at = time.time() + 900.0  # 15 min grace period
                    return self._cache.rate

                logger.critical(
                    f"[FOREX] All external forex APIs failed with empty cache. Falling back to baseline ₹{self.baseline_rate}."
                )
                now = time.time()
                self._cache = ForexRateEntry(
                    rate=self.baseline_rate,
                    provider="baseline_fallback",
                    updated_at=now,
                    expires_at=now + 600.0,
                    is_fallback=True,
                    source_url="",
                )
                return self.baseline_rate
            finally:
                self._is_refreshing = False


# Global singleton instance
forex_service = ForexService()

