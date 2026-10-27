# Market Data Backend — Detailed Design

**Scope:** everything under `backend/app/market/` — the unified data model and interface, the shared price cache, the GBM simulator, the Massive (Polygon.io) REST client, the source factory, the SSE stream, and how the rest of the backend (watchlist, trades, LLM chat) plugs into it.

**Audience:** the Backend / Market Data agents. Every code block in this document is a complete, drop-in module or test; together they were run against the installed `massive` package and pass `pytest` and `ruff`.

**Relationship to existing code:** a first version of this subsystem already exists (see `planning/MARKET_DATA_SUMMARY.md`). This document is the target design. It keeps the existing public API and adds a small set of fixes and additions, each marked **Δ** and listed in [§1](#1-changes-from-the-current-implementation). The most important one is a production bug in the Massive client ([§8.3](#83-response-mapping)).

---

## Table of Contents

1. [Changes from the current implementation](#1-changes-from-the-current-implementation)
2. [Architecture](#2-architecture)
3. [File layout](#3-file-layout)
4. [Data model — `models.py`](#4-data-model--modelspy)
5. [Price cache — `cache.py`](#5-price-cache--cachepy)
6. [Unified interface — `interface.py`](#6-unified-interface--interfacepy)
7. [Simulator — `seed_prices.py`, `simulator.py`](#7-simulator)
8. [Massive API client — `massive_client.py`](#8-massive-api-client--massive_clientpy)
9. [Factory and environment — `factory.py`](#9-factory-and-environment--factorypy)
10. [SSE stream — `stream.py`](#10-sse-stream--streampy)
11. [Ticker tracking and app integration — `tracking.py`, `main.py`](#11-ticker-tracking-and-app-integration)
12. [Package exports — `__init__.py`](#12-package-exports--__init__py)
13. [Testing](#13-testing)
14. [Edge cases and failure modes](#14-edge-cases-and-failure-modes)
15. [Configuration reference](#15-configuration-reference)
16. [Implementation checklist](#16-implementation-checklist)

---

## 1. Changes from the current implementation

| # | Δ | Module | Why |
|---|---|--------|-----|
| 1 | **Fix Massive snapshot parsing.** Read `last_trade.sip_timestamp` (nanoseconds), not `last_trade.timestamp`, and fall back through `min.close → day.close → prev_day.close` for the price. | `massive_client.py` | The installed `massive` `LastTrade` model has **no `timestamp` attribute**. Today every real snapshot raises `AttributeError`, gets caught, and is skipped, so with a real API key **no prices ever reach the cache**. The tests miss this because they build snapshots from `MagicMock`, which invents any attribute. |
| 2 | **Daily change.** `PriceUpdate` gains `reference_price` plus `day_change` and `day_change_percent`. | `models.py`, `cache.py` | PLAN §10 requires "daily change %" in the watchlist. The existing `change_percent` is tick-to-tick (~0.01%), which is not useful for that. For the simulator the reference is the session's first price; for Massive it is the previous close. |
| 3 | **`normalize_ticker()`** — one canonical form plus validation, used by every entry point. | `models.py` + both sources | Today Massive upper-cases tickers but the simulator does not, so `"aapl"` and `"AAPL"` could be tracked as two tickers. |
| 4 | **Cache `remove()` bumps `version`; `version` is read under the lock; `timestamp=0` is respected.** | `cache.py` | Without the bump, a removed ticker stays on screen until the next price write. That can be 15 s in Massive mode, or forever if it was the last ticker. |
| 5 | **Fresh `APIRouter` per `create_stream_router()` call; heartbeat comments; an empty cache sends `{}`.** | `stream.py` | Removes the module-level router footgun (duplicate routes in tests). Keeps idle Massive-mode connections alive through proxies, and lets the UI clear the last removed ticker. |
| 6 | **Seedable simulator RNG** (`seed=` parameter, private `numpy.Generator` / `random.Random`). | `simulator.py` | Reproducible unit tests without monkey-patching global RNGs. |
| 7 | **Massive robustness:** `retries=0` on `RESTClient`, ticker list copied before handing it to the worker thread, results for tickers removed mid-poll are dropped, failures are counted. | `massive_client.py` | urllib3's automatic retry on 429 burns the free tier's 5 req/min. The shared list was mutated across threads. |
| 8 | **`MASSIVE_POLL_INTERVAL`** optional environment variable. | `factory.py` | PLAN §6 says the poll interval is configurable (paid tiers can poll every 2–5 s). |
| 9 | **`sync_tracked_tickers()`** helper: tracked set = watchlist ∪ open positions. | new `tracking.py` | One idempotent rule for watchlist edits, trades and LLM actions. Held positions keep a live price after being un-watched. |

Unchanged: the `MarketDataSource` ABC methods, `PriceCache` method names, `seed_prices.py`, the GBM math, the correlation structure, the factory's selection rule, and the SSE URL and payload shape (fields are only added).

---

## 2. Architecture

```
                    ┌─────────────────────────────┐
  MASSIVE_API_KEY ─▶│ create_market_data_source() │
                    └──────────────┬──────────────┘
                     empty │               │ set
                           ▼               ▼
          ┌────────────────────────┐ ┌──────────────────────────┐
          │ SimulatorDataSource    │ │ MassiveDataSource        │
          │  GBMSimulator.step()   │ │  get_snapshot_all() in   │
          │  every 0.5 s           │ │  a thread, every 15 s    │
          └───────────┬────────────┘ └────────────┬─────────────┘
                      │  cache.update(...)        │
                      ▼                           ▼
                ┌──────────────────────────────────────┐
                │ PriceCache  {ticker: PriceUpdate}    │
                │ threading.Lock + version counter     │
                └───┬──────────────┬──────────────┬────┘
                    │              │              │
        GET /api/stream/prices   trades      portfolio valuation,
        (SSE, checks every 0.5s)  (fill      watchlist GET, LLM context
                                   price)
```

Core rules:

1. **Producers push, consumers pull.** A data source writes to the cache on its own schedule. Nothing downstream ever calls a data source to *get* a price. It reads `PriceCache`. This decouples the simulator (0.5 s) and Massive (15 s) cadences from the SSE cadence (0.5 s).
2. **`PriceUpdate` is the only type that leaves the market layer.** Routes serialize it with `to_dict()`.
3. **Exactly one source is active per process**, chosen at startup by the factory.
4. **The data source only knows the set of tickers to price.** Deciding *which* tickers (watchlist ∪ positions) is the app's job, via `sync_tracked_tickers()`.

---

## 3. File layout

```
backend/app/market/
├── __init__.py         # Public API re-exports
├── models.py           # PriceUpdate, normalize_ticker()                Δ
├── cache.py            # PriceCache                                     Δ
├── interface.py        # MarketDataSource ABC
├── seed_prices.py      # Seed prices, GBM params, correlation constants
├── simulator.py        # GBMSimulator + SimulatorDataSource            Δ
├── massive_client.py   # parse_snapshot() + MassiveDataSource          Δ
├── factory.py          # create_market_data_source()                   Δ
├── stream.py           # create_stream_router() + SSE generator        Δ
└── tracking.py         # sync_tracked_tickers()                        Δ new

backend/tests/market/
├── test_models.py  test_cache.py  test_simulator.py  test_simulator_source.py
├── test_massive.py  test_factory.py
└── test_stream.py  test_tracking.py                                    Δ new
```

Dependencies (already in `backend/pyproject.toml`): `fastapi`, `numpy`, `massive`. No new packages.

---

## 4. Data model — `models.py`

```python
"""Data models for market data."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["up", "down", "flat"]

# 1-10 chars, starts with a letter; allows class suffixes like BRK.B / BF-B
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def normalize_ticker(raw: str) -> str:
    """Canonical form for a ticker symbol: stripped and upper-cased.

    Raises ValueError for anything that is not a plausible US equity symbol.
    Every entry point (REST routes, LLM actions, data sources) calls this, so
    "aapl", " AAPL " and "AAPL" are always the same ticker.
    """
    ticker = raw.strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise ValueError(f"Invalid ticker symbol: {raw!r}")
    return ticker


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    # Price that "daily change" is measured from: the session's first price for
    # the simulator, the previous close for Massive.
    reference_price: float | None = None

    @property
    def change(self) -> float:
        """Absolute price change from the previous update (tick-to-tick)."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from the previous update (tick-to-tick)."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> Direction:
        """'up', 'down', or 'flat' relative to the previous update."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def day_change(self) -> float:
        """Absolute change from the reference price (0.0 if unknown)."""
        if not self.reference_price:
            return 0.0
        return round(self.price - self.reference_price, 4)

    @property
    def day_change_percent(self) -> float:
        """Percentage change from the reference price (0.0 if unknown)."""
        if not self.reference_price:
            return 0.0
        return round((self.price - self.reference_price) / self.reference_price * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "reference_price": self.reference_price,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }
```

### Field semantics

| Field / property | Meaning | Simulator | Massive |
|---|---|---|---|
| `price` | Latest price, rounded to cents | GBM output | `last_trade.price` (with fallbacks) |
| `previous_price` | Price at the previous cache write | previous tick (0.5 s ago) | previous poll (15 s ago) |
| `change`, `change_percent`, `direction` | Tick-to-tick move. **Drives the green/red flash.** | | |
| `timestamp` | Unix seconds of the price | wall clock | trade SIP time |
| `reference_price` | Base for daily change | first price this process saw (session open) | `prev_day.close` |
| `day_change`, `day_change_percent` | **Drives the watchlist "daily change %" column.** | | |

### Design decisions

- **`frozen=True, slots=True`**: updates are immutable value objects. They are safe to share between the event loop and the Massive worker thread without copying.
- **Derived values are properties**, not stored fields, so `direction` can never disagree with `price` and `previous_price`.
- **`normalize_ticker` lives here**, beside the type it keys, so every module can import it without cycles. It accepts class-share symbols (`BRK.B`, `BF-B`) and rejects anything injection-shaped. Routes turn its `ValueError` into HTTP 400.

---

## 5. Price cache — `cache.py`

```python
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker.

    Writers: SimulatorDataSource or MassiveDataSource (one at a time).
    Readers: SSE streaming endpoint, portfolio valuation, trade execution.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Monotonically increasing; bumped on every mutation

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        reference_price: float | None = None,
    ) -> PriceUpdate:
        """Record a new price for a ticker. Returns the created PriceUpdate.

        - First update for a ticker: previous_price == price (direction 'flat').
        - reference_price: if omitted, the previous update's reference is kept;
          on the very first update it defaults to the price itself. This gives
          the simulator a "session open" for free.
        """
        with self._lock:
            ts = time.time() if timestamp is None else timestamp
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price

            if reference_price is None:
                reference_price = prev.reference_price if prev else price

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
                reference_price=round(reference_price, 2),
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Get the latest price for a single ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(ticker)

    def get_all(self) -> dict[str, PriceUpdate]:
        """Snapshot of all current prices. Returns a shallow copy."""
        with self._lock:
            return dict(self._prices)

    def get_price(self, ticker: str) -> float | None:
        """Convenience: get just the price float, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the cache (e.g., when no longer tracked)."""
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1  # SSE clients must see the removal

    @property
    def version(self) -> int:
        """Current version counter. Used by SSE for change detection."""
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

### Why `threading.Lock` and not `asyncio.Lock`

The Massive client's blocking HTTP call runs in `asyncio.to_thread()`. The cache writes happen back on the event loop after the `await`, but a `threading.Lock` is correct no matter which thread a future writer runs on. The critical sections are a dict lookup and an assignment, so there is no measurable contention at tens of tickers and a handful of SSE clients.

### The version counter

`version` increments on every mutation, including `remove()` (Δ). SSE generators remember the last version they sent and skip the send when nothing changed. In Massive mode that turns 30 identical frames per poll into 1:

```python
last = -1
while True:
    if cache.version != last:
        last = cache.version
        yield format_prices_event(cache)
    await asyncio.sleep(0.5)
```

### Reference-price rules (Δ)

```python
cache = PriceCache()
cache.update("AAPL", 190.00)                         # first write → reference 190.00
u = cache.update("AAPL", 191.90)                     # reference carried forward
u.day_change_percent                                  # → 1.0
cache.update("AAPL", 125.07, reference_price=129.61)  # Massive: previous close supplied
```

---

## 6. Unified interface — `interface.py`

```python
"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push price updates into a shared PriceCache on their own
    schedule. Downstream code never calls the data source directly for prices —
    it reads from the cache.

    Tickers passed in are normalized by the implementation (normalize_ticker),
    so callers may pass "aapl" or " AAPL ".

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])
        # ... app runs ...
        await source.add_ticker("TSLA")
        await source.remove_ticker("GOOGL")
        # ... app shutting down ...
        await source.stop()
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing price updates for the given tickers.

        Starts a background task that periodically writes to the PriceCache.
        Must be called exactly once.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task and release resources.

        Safe to call multiple times. After stop(), the source will not write
        to the cache again.
        """

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. No-op if already present."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the active set and from the PriceCache.

        No-op if not present.
        """

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the current list of actively tracked tickers."""
```

Contract details both implementations must honour:

| Method | Must | Must not |
|---|---|---|
| `start(tickers)` | Normalize and de-duplicate tickers; put a first price in the cache **before returning** if one is obtainable; spawn exactly one background task | Raise on an empty list |
| `stop()` | Cancel and await the task; be idempotent | Leave a task writing to the cache |
| `add_ticker(t)` | Normalize; no-op if present | Block on network I/O |
| `remove_ticker(t)` | Normalize; drop from the active set **and** `cache.remove(t)` | Raise if absent |
| `get_tickers()` | Return a copy | Expose internal lists |

`add_ticker` must not do network I/O. A Massive "fetch this one ticker now" call would spend a free-tier request (5/min) on every watchlist click. The new ticker is included in the next scheduled poll instead.

---

## 7. Simulator

### 7.1 Model

Geometric Brownian Motion, one step per tick:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

| Symbol | Meaning | Value |
|---|---|---|
| `μ` | annualized drift | 0.03 – 0.08 per ticker |
| `σ` | annualized volatility | 0.17 (V) – 0.50 (TSLA) |
| `dt` | tick length as a fraction of a trading year | `0.5 / (252 · 6.5 · 3600)` ≈ 8.48e-8 |
| `Z` | correlated standard normal | `L @ z`, `L = cholesky(C)` |

Properties that follow:
- Prices stay positive, because `exp(·) > 0`.
- Per-tick σ ≈ `σ·√dt`: AAPL ≈ 0.0064 % (about 1¢ on $190), TSLA ≈ 0.015 %. Each tick is a small visible move, and over a simulated trading day these add up to a realistic intraday range.
- **Shock events:** each ticker has probability 0.001 per tick of an extra ±2–5 % jump. With 10 tickers at 2 ticks/s that is about one event every 50 s somewhere on the board.

### 7.2 Correlation

`C[i][j]` comes from sector membership:

| Pair | ρ |
|---|---|
| Both in tech (AAPL GOOGL MSFT AMZN META NVDA NFLX) | 0.6 |
| Both in finance (JPM V) | 0.5 |
| Anything with TSLA | 0.3 |
| Cross-sector or unknown ticker | 0.3 |

Every such matrix is positive definite: block equicorrelation with ρ ∈ [0.3, 0.6] and unit diagonal. So `np.linalg.cholesky` succeeds for any ticker set. The `LinAlgError` fallback exists only to survive a bad future edit to the constants. The matrix is rebuilt on every add or remove. That is O(n³), which is trivial for n < 50.

### 7.3 `seed_prices.py` (unchanged)

```python
"""Seed prices and per-ticker parameters for the market simulator."""

# Realistic starting prices for the default watchlist (as of project creation)
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM": 195.00,
    "V": 280.00,
    "NFLX": 600.00,
}

# Per-ticker GBM parameters
# sigma: annualized volatility (higher = more price movement)
# mu: annualized drift / expected return
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},  # High volatility
    "NVDA": {"sigma": 0.40, "mu": 0.08},  # High volatility, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},  # Low volatility (bank)
    "V": {"sigma": 0.17, "mu": 0.04},  # Low volatility (payments)
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}

# Default parameters for tickers not in the list above (dynamically added)
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Correlation groups for the simulator's Cholesky decomposition
# Tickers in the same group have higher intra-group correlation
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

# Correlation coefficients
INTRA_TECH_CORR = 0.6  # Tech stocks move together
INTRA_FINANCE_CORR = 0.5  # Finance stocks move together
CROSS_GROUP_CORR = 0.3  # Between sectors / unknown tickers
TSLA_CORR = 0.3  # TSLA does its own thing
```

Tickers not listed start at a uniform random price in [$50, $300] and use `DEFAULT_PARAMS`.

### 7.4 `simulator.py`

```python
"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import random

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .models import normalize_ticker
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)

logger = logging.getLogger(__name__)


class GBMSimulator:
    """Geometric Brownian Motion simulator for correlated stock prices.

    Math:
        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Where:
        S(t)   = current price
        mu     = annualized drift (expected return)
        sigma  = annualized volatility
        dt     = time step as fraction of a trading year
        Z      = correlated standard normal random variable
    """

    # 252 trading days * 6.5 hours/day * 3600 seconds/hour = 5,896,800 seconds
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # 500ms tick, ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        # Private RNGs: a fixed seed makes runs fully reproducible (tests, E2E)
        self._rng = np.random.default_rng(seed)
        self._py_rng = random.Random(seed)

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance all tickers by one time step. Returns {ticker: new_price}.

        This is the hot path — called every 500ms. Keep it fast.
        """
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]

            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Random event: ~0.1% chance per tick per ticker. With 10 tickers at
            # 2 ticks/sec, expect an event somewhere roughly every 50 seconds.
            if self._py_rng.random() < self._event_prob:
                shock = self._py_rng.uniform(0.02, 0.05) * self._py_rng.choice([-1, 1])
                self._prices[ticker] *= 1 + shock
                logger.debug("Random event on %s: %+.1f%%", ticker, shock * 100)

            result[ticker] = round(self._prices[ticker], 2)

        return result

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the simulation. Rebuilds the correlation matrix."""
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the simulation. Rebuilds the correlation matrix."""
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        """Current (unrounded) price for a ticker, or None if not tracked."""
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        """Return the list of currently tracked tickers."""
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add a ticker without rebuilding Cholesky (for batch initialization)."""
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker) or self._py_rng.uniform(50.0, 300.0)
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """Rebuild the Cholesky factor of the correlation matrix. O(n^3), n < 50."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return

        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho

        try:
            self._cholesky = np.linalg.cholesky(corr)
        except np.linalg.LinAlgError:
            # Cannot happen with the shipped constants, but a bad edit to
            # seed_prices.py must degrade to independent moves, not crash.
            logger.warning("Correlation matrix not positive definite; using independent draws")
            self._cholesky = None

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        """Correlation between two tickers based on sector grouping.

          - TSLA with anything:  0.3 (it does its own thing)
          - Both tech:           0.6
          - Both finance:        0.5
          - Cross-sector/unknown: 0.3
        """
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR

        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by the GBM simulator.

    Runs a background asyncio task that calls GBMSimulator.step() every
    `update_interval` seconds and writes results to the PriceCache.
    """

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed = seed
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
        self._sim = GBMSimulator(
            tickers=tickers,
            event_probability=self._event_prob,
            seed=self._seed,
        )
        # Seed the cache so SSE has data on its very first tick
        for ticker in tickers:
            self._seed_cache(ticker)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim and ticker not in self._sim.get_tickers():
            self._sim.add_ticker(ticker)
            self._seed_cache(ticker)  # Price is available immediately
            logger.info("Simulator: added ticker %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    def _seed_cache(self, ticker: str) -> None:
        price = self._sim.get_price(ticker) if self._sim else None
        if price is not None:
            self._cache.update(ticker=ticker, price=price)

    async def _run_loop(self) -> None:
        """Core loop: step the simulation, write to cache, sleep."""
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

Notes:
- **Two layers.** `GBMSimulator` is pure, synchronous and deterministic under a seed, so unit tests call `step()` directly. `SimulatorDataSource` adds the asyncio loop and cache writes.
- **Immediate seeding.** Both `start()` and `add_ticker()` write the seed price to the cache at once. The UI never shows an empty row, and a trade on a just-added ticker has a fill price.
- **Session open for free.** Because the first cache write is the seed price, `reference_price` = seed, and `day_change_percent` reads like a real intraday change.
- **Resilience.** An exception in one step is logged and the loop continues. One bad tick must never stop the feed.

---

## 8. Massive API client — `massive_client.py`

### 8.1 Endpoint

One call fetches every tracked ticker:

```
GET https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT
Authorization: Bearer <MASSIVE_API_KEY>
```

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=key, connect_timeout=5.0, read_timeout=10.0, retries=0)
snaps = client.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=["AAPL", "GOOGL"])
# -> list[massive.rest.models.TickerSnapshot]
```

The client is synchronous (urllib3), so it always runs via `asyncio.to_thread`. It raises `massive.exceptions.BadResponse` for any non-200 response (body text as the message) and `AuthError` for a missing key.

### 8.2 Rate limits and poll interval

| Plan | Limit | `MASSIVE_POLL_INTERVAL` |
|---|---|---|
| Free | 5 requests / min | 15 (default): 4 req/min, leaving one spare |
| Paid | effectively unlimited | 2 – 5 |

`retries=0` matters (Δ). `RESTClient` defaults to `retries=3` with urllib3 back-off on 413/429/5xx. On a 429 that sends three more requests within about 1 s and digs the rate-limit hole deeper. Our poll loop already retries on the next interval.

### 8.3 Response mapping

Raw JSON for one ticker, and the attribute names the `massive` models expose:

```json
{
  "ticker": "AAPL",
  "todaysChange": -4.54, "todaysChangePerc": -3.50,
  "updated": 1675190399000000000,
  "day":     {"o": 129.61, "h": 130.15, "l": 125.07, "c": 125.07, "v": 111237700, "vw": 127.35},
  "prevDay": {"o": 128.10, "h": 130.90, "l": 127.50, "c": 129.61, "v": 98000000,  "vw": 129.00},
  "lastTrade": {"p": 125.07, "s": 100, "x": 4, "t": 1675190399000000000, "i": "52983525029461"},
  "lastQuote": {"p": 125.06, "P": 125.08, "s": 5, "S": 10, "t": 1675190399500000000},
  "min":     {"c": 125.00, "t": 1675190340000, "v": 7500}
}
```

| We need | Model attribute | Unit / note |
|---|---|---|
| ticker | `snap.ticker` | |
| price | `snap.last_trade.price` | fallback → `snap.min.close` → `snap.day.close` → `snap.prev_day.close` |
| timestamp | `snap.last_trade.sip_timestamp` | **nanoseconds**; fallback `snap.updated` (ns) |
| reference price | `snap.prev_day.close` | previous session close |

> ⚠️ **Bug in the current code.** It reads `snap.last_trade.timestamp / 1000`. `massive.rest.models.LastTrade` has `sip_timestamp`, `participant_timestamp` and `trf_timestamp`, but **no `timestamp`**. Every real snapshot therefore raises `AttributeError` inside the per-snapshot `try` and is skipped with a warning, so with a real API key the cache stays empty. The tests pass only because `MagicMock()` creates any attribute on demand. Rule from here on: **Massive tests build snapshots with `TickerSnapshot.from_dict(...)`, never `MagicMock`** (see [§13.4](#134-massive-client)).

Why the fallbacks are needed:
- **Pre-market / just after midnight ET:** snapshot data is cleared at 12am ET. `day.*` is 0 and `last_trade` may be missing. `prev_day.close` is then the only price.
- **Thinly traded or new symbols:** `last_trade` can be absent while `min`/`day` bars exist.
- `0` means "no data" in these payloads, so `_positive()` treats `0` the same as `None`.
- Timestamp units differ by field (trades are ns, minute bars are ms). `_to_seconds()` detects the unit by magnitude instead of hard-coding it.

### 8.4 `massive_client.py`

```python
"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from massive import RESTClient
from massive.rest.models import SnapshotMarketType, TickerSnapshot

from .cache import PriceCache
from .interface import MarketDataSource
from .models import normalize_ticker

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedSnapshot:
    """The subset of a Massive TickerSnapshot that FinAlly uses."""

    ticker: str
    price: float
    timestamp: float | None  # Unix seconds; None -> cache uses "now"
    reference_price: float | None  # Previous close, for daily change


def _positive(value: float | None) -> float | None:
    """Massive reports 0 for fields with no data yet (e.g. day.* pre-market)."""
    return value if value is not None and value > 0 else None


def _to_seconds(ts: int | float | None) -> float | None:
    """Convert a Massive timestamp to Unix seconds.

    Snapshot trade timestamps are nanoseconds, minute bars are milliseconds.
    Detect the unit by magnitude so either is handled correctly.
    """
    if not ts:
        return None
    if ts > 1e17:  # nanoseconds
        return ts / 1e9
    if ts > 1e14:  # microseconds
        return ts / 1e6
    if ts > 1e11:  # milliseconds
        return ts / 1e3
    return float(ts)


def parse_snapshot(snap: TickerSnapshot) -> ParsedSnapshot | None:
    """Extract price, timestamp and previous close from one snapshot.

    Price fallback chain (first positive value wins):
        last_trade.price -> min.close -> day.close -> prev_day.close
    Returns None if the snapshot has no ticker or no usable price.
    """
    if not snap.ticker:
        return None

    trade, minute, day, prev_day = snap.last_trade, snap.min, snap.day, snap.prev_day
    price = (
        _positive(trade.price if trade else None)
        or _positive(minute.close if minute else None)
        or _positive(day.close if day else None)
        or _positive(prev_day.close if prev_day else None)
    )
    if price is None:
        return None

    timestamp = _to_seconds(trade.sip_timestamp if trade else None) or _to_seconds(snap.updated)
    return ParsedSnapshot(
        ticker=snap.ticker,
        price=price,
        timestamp=timestamp,
        reference_price=_positive(prev_day.close if prev_day else None),
    )


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all tracked
    tickers in a single API call, then writes results to the PriceCache.

    Rate limits:
      - Free tier: 5 req/min -> poll every 15s (default)
      - Paid tiers: effectively unlimited -> poll every 2-5s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None
        self._consecutive_failures = 0

    async def start(self, tickers: list[str]) -> None:
        # retries=0: our poll loop is the retry policy. urllib3's built-in
        # retries would re-send on 429 within a second and burn the free tier's
        # 5 req/min budget.
        self._client = RESTClient(
            api_key=self._api_key, connect_timeout=5.0, read_timeout=10.0, retries=0
        )
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))

        # Immediate first poll so the cache has data right away
        await self._poll_once()

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added ticker %s (price arrives on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internal ---

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch snapshots, update cache. Never raises."""
        if not self._tickers or not self._client:
            return

        # Copy: add/remove_ticker may mutate the list while the thread runs
        tickers = list(self._tickers)
        try:
            # RESTClient is synchronous (urllib3) — run it off the event loop
            snapshots = await asyncio.to_thread(self._fetch_snapshots, tickers)
        except Exception as e:
            # 401 bad key, 403 plan lacks endpoint, 429 rate limit, network.
            # Cache keeps last-known prices; next cycle retries.
            self._consecutive_failures += 1
            logger.error(
                "Massive poll failed (%d in a row): %s", self._consecutive_failures, e
            )
            return

        self._consecutive_failures = 0
        tracked = set(self._tickers)  # Re-read: drop tickers removed mid-poll
        updated: set[str] = set()
        for snap in snapshots:
            parsed = parse_snapshot(snap)
            if parsed is None:
                logger.warning("Skipping unusable snapshot for %s", snap.ticker or "???")
                continue
            if parsed.ticker not in tracked:
                continue
            self._cache.update(
                ticker=parsed.ticker,
                price=parsed.price,
                timestamp=parsed.timestamp,
                reference_price=parsed.reference_price,
            )
            updated.add(parsed.ticker)

        missing = set(tickers) - updated
        if missing:
            logger.debug("Massive returned no data for: %s", ", ".join(sorted(missing)))

    def _fetch_snapshots(self, tickers: list[str]) -> list[TickerSnapshot]:
        """Synchronous call to the Massive REST API. Runs in a worker thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
```

### 8.5 Error handling

| Situation | Behaviour |
|---|---|
| 401 bad key / 403 plan lacks endpoint | `BadResponse` → logged as ERROR with a consecutive-failure count; loop keeps running; cache keeps last prices (empty if it never succeeded) |
| 429 rate-limited | Same. Next attempt after `poll_interval` (no client-side burst retries) |
| Network timeout | Same (5 s connect / 10 s read keeps a hung call shorter than one interval) |
| Snapshot with no usable price | That ticker skipped with a WARNING; others processed |
| Unknown ticker requested | Massive omits it; logged at DEBUG; no cache entry, so the watchlist shows "—" |
| Ticker removed while a poll is in flight | Result dropped (`tracked` is re-read after the `await`), so it is not resurrected in the cache |

---

## 9. Factory and environment — `factory.py`

```python
"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

DEFAULT_MASSIVE_POLL_INTERVAL = 15.0  # Free tier: 5 req/min
MIN_MASSIVE_POLL_INTERVAL = 1.0


def _poll_interval_from_env() -> float:
    raw = os.environ.get("MASSIVE_POLL_INTERVAL", "").strip()
    if not raw:
        return DEFAULT_MASSIVE_POLL_INTERVAL
    try:
        return max(float(raw), MIN_MASSIVE_POLL_INTERVAL)
    except ValueError:
        logger.warning("Ignoring invalid MASSIVE_POLL_INTERVAL=%r", raw)
        return DEFAULT_MASSIVE_POLL_INTERVAL


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the appropriate market data source based on environment variables.

    - MASSIVE_API_KEY set and non-empty -> MassiveDataSource (real market data)
    - Otherwise                         -> SimulatorDataSource (GBM simulation)

    Returns an unstarted source. Caller must await source.start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        interval = _poll_interval_from_env()
        logger.info("Market data source: Massive API (real data), poll every %.1fs", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)

    logger.info("Market data source: GBM Simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

| Variable | Effect |
|---|---|
| `MASSIVE_API_KEY` unset / empty / whitespace | Simulator |
| `MASSIVE_API_KEY` set | Massive |
| `MASSIVE_POLL_INTERVAL` (Δ, optional) | Seconds between Massive polls; default 15, minimum 1, an invalid value logs a warning and uses 15 |

Add to `.env.example`:

```bash
# Optional: seconds between Massive polls (free tier: keep at 15; paid: 2-5)
MASSIVE_POLL_INTERVAL=15
```

---

## 10. SSE stream — `stream.py`

```python
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

PUSH_INTERVAL = 0.5  # seconds between cache checks
HEARTBEAT_INTERVAL = 15.0  # seconds; keeps idle connections open through proxies
RETRY_MS = 1000  # EventSource reconnect delay


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create the SSE router bound to a PriceCache.

    A fresh APIRouter per call, so calling this twice (e.g. in tests) never
    registers the route twice.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates (text/event-stream)."""
        return StreamingResponse(
            generate_price_events(price_cache, request.is_disconnected),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
            },
        )

    return router


def format_prices_event(price_cache: PriceCache) -> str:
    """Serialize the full cache as one SSE `data:` event."""
    data = {ticker: update.to_dict() for ticker, update in price_cache.get_all().items()}
    return f"data: {json.dumps(data)}\n\n"


async def generate_price_events(
    price_cache: PriceCache,
    is_disconnected,  # async () -> bool; Request.is_disconnected in production
    interval: float = PUSH_INTERVAL,
    heartbeat: float = HEARTBEAT_INTERVAL,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames: a retry directive, then a full price snapshot whenever
    the cache version changes, with a comment heartbeat when idle.
    """
    yield f"retry: {RETRY_MS}\n\n"

    last_version = -1
    last_sent = time.monotonic()
    while not await is_disconnected():
        version = price_cache.version
        if version != last_version:
            last_version = version
            yield format_prices_event(price_cache)
            last_sent = time.monotonic()
        elif time.monotonic() - last_sent >= heartbeat:
            yield ": ping\n\n"  # SSE comment; EventSource ignores it
            last_sent = time.monotonic()
        await asyncio.sleep(interval)
```

### Wire format

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":190.52,"previous_price":190.49,"timestamp":1730000000.51,"change":0.03,"change_percent":0.0157,"direction":"up","reference_price":190.0,"day_change":0.52,"day_change_percent":0.2737},"GOOGL":{...}}

: ping

```

- Every `data:` frame is a **full snapshot** of all tracked tickers, not a diff. A ticker missing from a frame has been removed. The client never needs reconciliation logic after a reconnect.
- `retry: 1000` sets the browser's reconnect delay to 1 s.
- `: ping` lines are SSE comments. `EventSource` ignores them, but they stop proxies and load balancers (App Runner, Render) from closing an idle stream in Massive mode, where data changes only every 15 s.
- The generator takes `is_disconnected` as a callable instead of the whole `Request`, so tests can drive it with a plain async function.

### Frontend consumer (reference for the Frontend agent)

```ts
type PriceUpdate = {
  ticker: string; price: number; previous_price: number; timestamp: number;
  change: number; change_percent: number; direction: "up" | "down" | "flat";
  reference_price: number | null; day_change: number; day_change_percent: number;
};

const es = new EventSource("/api/stream/prices");
es.onopen = () => setStatus("connected");
es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
es.onmessage = (e) => {
  const prices: Record<string, PriceUpdate> = JSON.parse(e.data);
  // flash on p.direction; sparkline append p.price; watchlist % = p.day_change_percent
};
```

---

## 11. Ticker tracking and app integration

### 11.1 Which tickers are priced

```
tracked = watchlist tickers ∪ tickers with an open position (quantity > 0)
```

Watched tickers need live prices for the grid. Held tickers need live prices for portfolio value, the heatmap and P&L, even after the user un-watches them. Rather than sprinkle `add_ticker`/`remove_ticker` calls with special cases through every route, the app recomputes the wanted set and lets one helper apply the diff:

```python
"""Keep the data source's ticker set in line with what the app needs priced."""

from __future__ import annotations

from collections.abc import Iterable

from .interface import MarketDataSource
from .models import normalize_ticker


async def sync_tracked_tickers(source: MarketDataSource, wanted: Iterable[str]) -> None:
    """Make `source` track exactly `wanted` (watchlist ∪ open positions).

    Idempotent: call it after any watchlist change or trade. Adds go first so
    a ticker moving from "watched" to "held" never loses its price.
    """
    target = {normalize_ticker(t) for t in wanted}
    current = set(source.get_tickers())
    for ticker in sorted(target - current):
        await source.add_ticker(ticker)
    for ticker in sorted(current - target):
        await source.remove_ticker(ticker)
```

### 11.2 Lifespan wiring (`backend/app/main.py`)

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.market import (
    MarketDataSource,
    PriceCache,
    create_market_data_source,
    create_stream_router,
)
from app.db import get_tracked_tickers  # watchlist ∪ open positions (DB layer)


@asynccontextmanager
async def lifespan(app: FastAPI):
    price_cache = PriceCache()
    source = create_market_data_source(price_cache)
    await source.start(get_tracked_tickers())  # DB is lazily initialized + seeded first

    app.state.price_cache = price_cache
    app.state.market_source = source
    app.include_router(create_stream_router(price_cache))
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)


# Dependencies for route modules
def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    return request.app.state.market_source
```

Register the SSE router **before** mounting the static frontend at `/`, so `/api/*` is not shadowed. When the stream router is included inside `lifespan`, mount static files inside `lifespan` too, after it.

### 11.3 Route integration patterns

```python
from fastapi import APIRouter, Depends, HTTPException

from app.market import MarketDataSource, PriceCache, normalize_ticker, sync_tracked_tickers

router = APIRouter(prefix="/api")


def parse_ticker(raw: str) -> str:
    try:
        return normalize_ticker(raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/watchlist")
async def list_watchlist(cache: PriceCache = Depends(get_price_cache)):
    return [
        {"ticker": t, **(u.to_dict() if (u := cache.get(t)) else {"price": None})}
        for t in db.get_watchlist()
    ]


@router.post("/watchlist")
async def add_watchlist(body: WatchlistAdd,
                        source: MarketDataSource = Depends(get_market_source),
                        cache: PriceCache = Depends(get_price_cache)):
    ticker = parse_ticker(body.ticker)
    db.add_watchlist(ticker)  # unique (user_id, ticker): no-op if present
    await sync_tracked_tickers(source, db.get_tracked_tickers())
    update = cache.get(ticker)  # simulator: set already; Massive: None until next poll
    return {"ticker": ticker, "price": update.price if update else None}


@router.delete("/watchlist/{ticker}")
async def remove_watchlist(ticker: str, source: MarketDataSource = Depends(get_market_source)):
    ticker = parse_ticker(ticker)
    db.remove_watchlist(ticker)
    await sync_tracked_tickers(source, db.get_tracked_tickers())  # still priced if held
    return {"ticker": ticker, "removed": True}


@router.post("/portfolio/trade")
async def trade(body: TradeRequest,
                source: MarketDataSource = Depends(get_market_source),
                cache: PriceCache = Depends(get_price_cache)):
    ticker = parse_ticker(body.ticker)
    price = cache.get_price(ticker)
    if price is None:
        # Not tracked yet (trade bar on an un-watched ticker) — start tracking.
        await source.add_ticker(ticker)
        price = cache.get_price(ticker)  # simulator: available now
    if price is None:
        raise HTTPException(400, f"No price yet for {ticker}; try again in a few seconds")
    result = db.execute_trade(ticker, body.side, body.quantity, price)  # validates cash/shares
    await sync_tracked_tickers(source, db.get_tracked_tickers())  # e.g. full sell of un-watched
    return result
```

The LLM chat handler calls the same service functions as these routes for `trades` and `watchlist_changes`, so the tracking rule holds there too. A `ValueError` from `normalize_ticker` on an LLM-proposed ticker becomes an entry in that action's error list, which the LLM reports back to the user.

---

## 12. Package exports — `__init__.py`

```python
"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate               - Immutable price snapshot dataclass
    PriceCache                - Thread-safe in-memory price store
    MarketDataSource          - Abstract interface for data providers
    normalize_ticker          - Canonical ticker form; raises ValueError if invalid
    create_market_data_source - Factory that selects simulator or Massive
    create_stream_router      - FastAPI router factory for the SSE endpoint
    sync_tracked_tickers      - Diff-and-apply the set of tickers to price
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate, normalize_ticker
from .stream import create_stream_router
from .tracking import sync_tracked_tickers

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "normalize_ticker",
    "create_market_data_source",
    "create_stream_router",
    "sync_tracked_tickers",
]
```

Downstream code imports only from `app.market`, never from submodules. The exceptions are tests and `parse_snapshot`.

---

## 13. Testing

Run with `cd backend && uv run --extra dev pytest tests/market -v`. `asyncio_mode = "auto"` is already set in `pyproject.toml`, so async tests need no decorator.

### 13.1 Models and cache — `test_models.py`, `test_cache.py` (additions)

```python
import pytest

from app.market import PriceCache, normalize_ticker


def test_normalize_ticker():
    assert normalize_ticker(" aapl ") == "AAPL"
    assert normalize_ticker("brk.b") == "BRK.B"
    for bad in ["", "1ABC", "AAPL; DROP", "TOOLONGTICKER"]:
        with pytest.raises(ValueError):
            normalize_ticker(bad)


def test_reference_price_defaults_to_first_price():
    cache = PriceCache()
    cache.update("AAPL", 100.0)
    u = cache.update("AAPL", 103.0)
    assert u.reference_price == 100.0
    assert u.day_change == 3.0
    assert u.day_change_percent == 3.0


def test_explicit_reference_price_wins():
    cache = PriceCache()
    assert cache.update("AAPL", 99.0, reference_price=100.0).day_change_percent == -1.0


def test_remove_bumps_version_only_when_present():
    cache = PriceCache()
    cache.update("AAPL", 1.0)
    v = cache.version
    cache.remove("AAPL")
    assert cache.version == v + 1
    cache.remove("AAPL")
    assert cache.version == v + 1


def test_zero_timestamp_is_respected():
    assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0
```

### 13.2 Simulator math — `test_simulator.py` (additions)

```python
import numpy as np

from app.market.seed_prices import SEED_PRICES
from app.market.simulator import GBMSimulator


def test_seeded_simulator_is_deterministic():
    a = GBMSimulator(list(SEED_PRICES), seed=42)
    b = GBMSimulator(list(SEED_PRICES), seed=42)
    for _ in range(100):
        assert a.step() == b.step()


def test_full_default_set_cholesky():
    sim = GBMSimulator(list(SEED_PRICES))
    assert sim._cholesky is not None and sim._cholesky.shape == (10, 10)


def test_tech_stocks_are_correlated():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], event_probability=0.0, seed=1)
    rets = {t: [] for t in ["AAPL", "MSFT", "JPM"]}
    prev = {t: sim.get_price(t) for t in rets}
    for _ in range(5000):
        sim.step()
        for t in rets:
            p = sim.get_price(t)
            rets[t].append(np.log(p / prev[t]))
            prev[t] = p
    c = np.corrcoef([rets["AAPL"], rets["MSFT"], rets["JPM"]])
    assert 0.5 < c[0, 1] < 0.7  # tech-tech ≈ 0.6
    assert 0.2 < c[0, 2] < 0.4  # cross-sector ≈ 0.3
```

### 13.3 Simulator source — `test_simulator_source.py` (additions)

```python
import asyncio

from app.market import PriceCache
from app.market.simulator import SimulatorDataSource


async def test_simulator_source_normalizes_and_seeds():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=7)
    await src.start(["aapl", "AAPL", "googl"])
    assert src.get_tickers() == ["AAPL", "GOOGL"]
    assert cache.get_price("AAPL") == 190.0  # seeded before first tick
    await src.add_ticker("pypl")
    assert "PYPL" in cache  # available immediately
    await asyncio.sleep(0.05)
    assert cache.get("AAPL").reference_price == 190.0  # session open
    await src.remove_ticker("pypl")
    assert "PYPL" not in cache
    await src.stop()
    await src.stop()  # idempotent
```

### 13.4 Massive client — `test_massive.py` (replace the MagicMock snapshot helper)

```python
from unittest.mock import MagicMock, patch

from massive.rest.models import TickerSnapshot

from app.market import PriceCache
from app.market.massive_client import MassiveDataSource, parse_snapshot

# Real wire-format payload, parsed by the real model class.
SNAP = {
    "ticker": "AAPL",
    "todaysChangePerc": -3.5,
    "updated": 1675190399000000000,
    "day": {"o": 129.61, "h": 130.15, "l": 125.07, "c": 125.07, "v": 111237700},
    "prevDay": {"c": 129.61},
    "lastTrade": {"p": 125.07, "s": 100, "x": 4, "t": 1675190399000000000},
    "min": {"c": 125.0, "t": 1675190340000},
}


def test_parse_snapshot_real_model():
    p = parse_snapshot(TickerSnapshot.from_dict(SNAP))
    assert (p.ticker, p.price, p.timestamp, p.reference_price) == (
        "AAPL", 125.07, 1675190399.0, 129.61,
    )


def test_parse_snapshot_fallbacks():
    premarket = {"ticker": "AAPL", "day": {"c": 0}, "prevDay": {"c": 129.61}}
    p = parse_snapshot(TickerSnapshot.from_dict(premarket))
    assert p.price == 129.61 and p.timestamp is None
    assert parse_snapshot(TickerSnapshot.from_dict({"ticker": "X"})) is None


async def test_poll_once_updates_cache_and_ignores_untracked():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60)
    src._client = MagicMock()
    src._tickers = ["AAPL"]
    snaps = [TickerSnapshot.from_dict(SNAP), TickerSnapshot.from_dict({**SNAP, "ticker": "ZZZ"})]
    with patch.object(src, "_fetch_snapshots", return_value=snaps):
        await src._poll_once()
    u = cache.get("AAPL")
    assert u.price == 125.07
    assert round(u.day_change_percent, 2) == -3.5  # matches todaysChangePerc
    assert "ZZZ" not in cache


async def test_poll_failure_keeps_cache():
    cache = PriceCache()
    cache.update("AAPL", 1.0)
    src = MassiveDataSource("k", cache)
    src._client = MagicMock()
    src._tickers = ["AAPL"]
    with patch.object(src, "_fetch_snapshots", side_effect=RuntimeError("429")):
        await src._poll_once()  # must not raise
    assert cache.get_price("AAPL") == 1.0
    assert src._consecutive_failures == 1


async def test_start_does_immediate_poll():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60)
    with patch.object(
        MassiveDataSource, "_fetch_snapshots", return_value=[TickerSnapshot.from_dict(SNAP)]
    ):
        await src.start(["aapl"])
    assert cache.get_price("AAPL") == 125.07
    await src.stop()
```

### 13.5 Factory — `test_factory.py` (additions)

```python
from app.market import PriceCache, create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


def test_factory(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "  ")
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "5")
    s = create_market_data_source(PriceCache())
    assert isinstance(s, MassiveDataSource) and s._interval == 5.0
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "nope")
    assert create_market_data_source(PriceCache())._interval == 15.0
```

### 13.6 SSE — `test_stream.py` (new)

```python
import json

from fastapi import FastAPI

from app.market import PriceCache, create_stream_router
from app.market.stream import generate_price_events


async def test_sse_generator():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    calls = {"n": 0}

    async def disconnected():
        calls["n"] += 1
        return calls["n"] > 3

    frames = [f async for f in generate_price_events(cache, disconnected, interval=0, heartbeat=0)]
    assert frames[0] == "retry: 1000\n\n"
    assert json.loads(frames[1].removeprefix("data: "))["AAPL"]["price"] == 190.0
    assert frames[2] == ": ping\n\n"  # nothing changed -> heartbeat


def test_router_registered_once():
    cache = PriceCache()
    r1, r2 = create_stream_router(cache), create_stream_router(cache)
    assert r1 is not r2
    app = FastAPI()
    app.include_router(r1)
    assert [r.path for r in app.routes].count("/api/stream/prices") == 1
```

### 13.7 Tracking — `test_tracking.py` (new)

```python
from app.market import PriceCache, sync_tracked_tickers
from app.market.simulator import SimulatorDataSource


async def test_sync_tracked_tickers():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=10)
    await src.start(["AAPL", "GOOGL"])
    await sync_tracked_tickers(src, ["googl", "TSLA"])
    assert sorted(src.get_tickers()) == ["GOOGL", "TSLA"]
    assert "AAPL" not in cache
    await src.stop()
```

### 13.8 Manual check against the live API

There is no CI for this, since it needs a key. Run it before merging Massive changes:

```bash
cd backend
MASSIVE_API_KEY=... uv run python -c "
from massive import RESTClient
from massive.rest.models import SnapshotMarketType
from app.market.massive_client import parse_snapshot
c = RESTClient(retries=0)
for s in c.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=['AAPL','MSFT']):
    print(parse_snapshot(s))
"
```

Expect one `ParsedSnapshot` per ticker with a positive price and a timestamp near now (or near the last close).

---

## 14. Edge cases and failure modes

| Case | Behaviour |
|---|---|
| Empty tracked set at startup | Both sources start fine and produce nothing. SSE sends `retry` then `data: {}` |
| Last ticker removed | `remove()` bumps the version, so SSE sends `{}` and the UI clears the row |
| Duplicate / lower-case tickers | Normalized and de-duplicated in `start`/`add_ticker`/`remove_ticker`/`sync_tracked_tickers` |
| Invalid symbol (`"AAPL; DROP"`) | `normalize_ticker` raises `ValueError`, which becomes HTTP 400 or an LLM action error |
| Trade on an untracked ticker | Route calls `add_ticker`. Simulator fills immediately; Massive returns 400 "no price yet" until the next poll |
| Un-watch a held ticker | Stays tracked via `sync_tracked_tickers` (positions are in the wanted set) |
| Massive key invalid | ERROR logs every interval. SSE stays connected with no data, so the header dot is green while the grid shows "—". This is acceptable; the logs say why |
| Market closed | Massive returns the last trade (may be after-hours). Prices stop moving; SSE sends heartbeats only |
| Simulator left running for days | GBM stays positive. Drift is tiny (μ·dt per tick), so prices wander but do not explode |
| Client disconnects | `is_disconnected()` returns true and the generator ends. If the task is cancelled instead, `CancelledError` propagates and Starlette handles it |
| Many SSE clients | Each client is one coroutine that wakes every 0.5 s. Serialization is O(tickers). Fine for a single-user app |

---

## 15. Configuration reference

| Parameter | Where | Default |
|---|---|---|
| `MASSIVE_API_KEY` | env | empty, which selects the simulator |
| `MASSIVE_POLL_INTERVAL` | env | `15` s (min 1) |
| `update_interval` | `SimulatorDataSource(...)` | `0.5` s |
| `event_probability` | `SimulatorDataSource(...)` / `GBMSimulator(...)` | `0.001` per ticker per tick |
| `seed` | `SimulatorDataSource(...)` / `GBMSimulator(...)` | `None` (random) |
| `dt` | `GBMSimulator(...)` | `0.5 / 5_896_800` |
| `connect_timeout` / `read_timeout` / `retries` | `RESTClient` in `MassiveDataSource.start` | `5` s / `10` s / `0` |
| `PUSH_INTERVAL` | `stream.py` | `0.5` s |
| `HEARTBEAT_INTERVAL` | `stream.py` | `15` s |
| `RETRY_MS` | `stream.py` | `1000` ms |

---

## 16. Implementation checklist

Moving from the current code to this design, in order (each step leaves the suite green):

1. `models.py`: add `normalize_ticker`, `reference_price`, `day_change`, `day_change_percent`; extend `to_dict`.
2. `cache.py`: `reference_price` parameter, `timestamp is None` check, version bump in `remove`, locked `version`.
3. `simulator.py`: `seed` + private RNGs, normalization in the data source, `LinAlgError` fallback.
4. `massive_client.py`: `ParsedSnapshot` / `parse_snapshot` / `_to_seconds`; `retries=0`; ticker copy and mid-poll removal guard; failure counter. **Replace** the `MagicMock` snapshot helper in `tests/market/test_massive.py` with `TickerSnapshot.from_dict` fixtures. The four existing tests that set `last_trade.timestamp` encode the bug and must be rewritten, not patched.
5. `factory.py`: `MASSIVE_POLL_INTERVAL`; add it to `.env.example`.
6. `stream.py`: router inside the factory, `generate_price_events(cache, is_disconnected, ...)`, heartbeat, `{}` on empty.
7. Add `tracking.py` and update `__init__.py`.
8. Add `tests/market/test_stream.py`, `test_tracking.py` and the additions above. Then run `uv run --extra dev pytest tests/market` and `uv run --extra dev ruff check app tests`.
9. Update `backend/CLAUDE.md` (new exports, `day_change_percent` for the watchlist) and `planning/MARKET_DATA_SUMMARY.md`.
10. Run the §13.8 live check once with a real key.
