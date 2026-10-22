# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area (also accumulated from the SSE stream since page load — there is no historical price endpoint)
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db` (path configurable via `DB_PATH`), volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

The only background task is the market data source (simulator loop or Massive poller), started and stopped in the FastAPI lifespan hook. Portfolio snapshots are recorded on demand, not by a separate loop (see Section 7).

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── app/
│       ├── market/           # Market data subsystem (complete)
│       └── db/               # Schema definitions, seed data, init logic (Python package)
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── docker-compose.yml        # Optional convenience wrapper
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/app/db/`** contains schema SQL definitions and seed logic, as part of the `app` Python package. It deliberately does **not** live at `backend/db/`: in the container, the backend is copied to `/app` and the data volume is mounted at `/app/db`, so a `backend/db/` folder would be hidden by the volume.
- **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts via Docker volume.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Optional: OpenRouter API key for LLM chat functionality.
# Without it the app runs normally; only chat returns a friendly "AI unavailable" message.
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false

# Optional: SQLite file location. Default: db/finally.db relative to the project root.
# In the container this is set to /app/db/finally.db.
DB_PATH=
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests); no API key needed
- If `OPENROUTER_API_KEY` is absent and `LLM_MOCK` is not `true` → the app starts normally; `POST /api/chat` returns a normal chat response whose message explains that the AI assistant is not configured
- **Loading**: in Docker, variables come from `--env-file .env` (the container never reads a `.env` file itself). For local development, the backend calls `python-dotenv`'s `load_dotenv()` at startup, which loads the project-root `.env` if present and is a no-op otherwise.

---

## 6. Market Data

> The market data subsystem is built — see `planning/MARKET_DATA_SUMMARY.md`. Items marked **(change required)** below are small additions to that completed code.

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.)
- Runs as an in-process background task — no external dependencies
- **Fixed ticker universe (change required)**: the simulator only supports a fixed set of 50 well-known US tickers, each with a seed price, GBM parameters, and a correlation group in `seed_prices.py`. This replaces today's behavior of giving unknown tickers a random $50–$300 seed price, so typos like `APPL` are rejected rather than becoming tradable stocks. The universe:
  - Default watchlist (10): AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX
  - Tech (12): AMD, INTC, ORCL, CRM, ADBE, CSCO, AVGO, QCOM, IBM, UBER, SHOP, PLTR
  - Finance (8): BAC, WFC, GS, MS, C, MA, PYPL, AXP
  - Consumer (10): WMT, COST, HD, MCD, NKE, SBUX, KO, PEP, DIS, PG
  - Healthcare (5): JNJ, UNH, PFE, LLY, MRK
  - Energy & industrials (5): XOM, CVX, BA, CAT, GE

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the union of all tracked tickers on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator
- Tickers are not validated up front. A ticker for which Massive returns no data simply never gets a price in the cache; the UI shows "—" for it and trades on it are rejected (see Section 8).

### Ticker Validation

Every ticker entering the system (watchlist add, manual trade, LLM action) is uppercased and trimmed, then validated by the active market data source:
- **Simulator**: must be in the fixed universe
- **Massive**: must match `^[A-Z][A-Z.]{0,9}$`

The market data source exposes this as a method (e.g. `is_supported(ticker) -> bool`) **(change required)**, so validation logic lives in one place.

### Tracked Tickers

The market data source tracks the **union of watchlist tickers and tickers with an open position**. Removing a ticker from the watchlist only stops tracking it if the user holds no shares of it; likewise, selling a full position only stops tracking the ticker if it is not on the watchlist. This keeps portfolio valuation live for every holding.

On startup, the backend initializes the database first, then starts the market data source with this union.

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds the latest price, previous price, session open price, and timestamp for each ticker
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

### Daily Change (change required)

`previous_price` is the previous tick (~500ms ago), so it cannot drive a "daily change" figure. Each `PriceUpdate` therefore also carries a `session_open` price:
- **Simulator**: the ticker's first price after the backend started tracking it (its seed price)
- **Massive**: the previous day's close from the snapshot response (`prevDay.c`); falls back to the first polled price if absent

`PriceUpdate.to_dict()` adds `session_open` and `daily_change_percent = (price - session_open) / session_open * 100`. The watchlist's "daily change %" column uses `daily_change_percent`.

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- The first message is `retry: 1000` (browser reconnects after 1s if the connection drops)
- Roughly every 500ms, if any price changed, the server sends **one event containing all tracked tickers**, keyed by ticker:

```
data: {"AAPL": {"ticker": "AAPL", "price": 190.52, "previous_price": 190.48, "session_open": 190.00,
                "timestamp": 1760352000.123, "change": 0.04, "change_percent": 0.021,
                "daily_change_percent": 0.2737, "direction": "up"},
       "GOOGL": {...}, ...}
```

- Field meanings: `change`/`change_percent`/`direction` are versus the previous tick (drive the flash animation); `daily_change_percent` is versus `session_open` (drives the watchlist column); `timestamp` is Unix seconds.
- Events use the default (unnamed) event type, so the frontend uses `EventSource.onmessage`.
- Client handles reconnection automatically (EventSource has built-in retry)

---

## 7. Database

### SQLite with Startup Initialization

The backend initializes the database in the FastAPI lifespan (startup) hook, before the market data source starts — because the market data source needs the watchlist tickers at startup. If the SQLite file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- No per-request "is the database initialized?" checks
- Fresh Docker volumes start with a clean, seeded database automatically

The file location comes from `DB_PATH` (see Section 5). The backend creates the parent directory if it is missing.

### Schema

All tables except `users_profile` include a `user_id` column defaulting to `"default"`; `users_profile` is keyed by its `id` column, which holds the same user id. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**users_profile** — User state (cash balance)
- `id` TEXT PRIMARY KEY (default: `"default"`)
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `added_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`

**positions** — Current holdings (one row per ticker per user)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `quantity` INTEGER (whole shares only)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)
- UNIQUE constraint on `(user_id, ticker)`
- A position sold down to zero is **deleted** — zero-quantity rows never exist, so they never appear in the positions table or heatmap

**trades** — Trade history (append-only log)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` INTEGER (whole shares only)
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart)
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)

Snapshots are recorded without a dedicated background loop:
- once at startup (after the market data source has produced its first prices), so the P&L chart has a starting point on first launch
- immediately after each successful trade
- on `GET /api/portfolio/history`, if the most recent snapshot is more than 30 seconds old

The frontend polls `GET /api/portfolio/history` every 30 seconds, which produces a ~30s cadence while the app is open. No snapshots are recorded while no browser is open, which is acceptable for this app.

**chat_messages** — Conversation history with LLM
- `id` TEXT PRIMARY KEY (UUID)
- `user_id` TEXT (default: `"default"`)
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — trades executed, watchlist changes made; null for user messages)
- `created_at` TEXT (ISO timestamp)

### Default Seed Data

- One user profile: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates (format in Section 6) |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized P&L |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots over time (for P&L chart) |

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices |
| POST | `/api/watchlist` | Add a ticker: `{ticker}` |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message, receive complete JSON response (message + executed actions) |
| GET | `/api/chat/history` | Stored conversation, oldest first, so the chat panel survives a page refresh |

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment): `{"status": "ok"}` |

### Conventions

- All request and response bodies are JSON. Timestamps in REST responses are ISO 8601 strings; SSE timestamps are Unix seconds (as already implemented).
- Tickers in requests are case-insensitive; the backend uppercases and trims them, and all responses use uppercase.
- Money values are returned unrounded as floats; the frontend formats them for display.
- **Errors** use FastAPI's default shape: `{"detail": "Human-readable message"}`, with HTTP `400` for business-rule failures (insufficient cash, insufficient shares, no price available, unsupported ticker, invalid quantity), `404` for removing a ticker that is not on the watchlist, and `422` for malformed request bodies (FastAPI validation).

### Response Shapes

**`GET /api/portfolio`**
```json
{
  "cash_balance": 8095.0,
  "positions_value": 1905.2,
  "total_value": 10000.2,
  "unrealized_pnl": 0.2,
  "positions": [
    {
      "ticker": "AAPL",
      "quantity": 10,
      "avg_cost": 190.5,
      "current_price": 190.52,
      "market_value": 1905.2,
      "unrealized_pnl": 0.2,
      "unrealized_pnl_percent": 0.0105
    }
  ]
}
```
If a position's ticker has no cached price yet, `current_price` falls back to `avg_cost`.

**`POST /api/portfolio/trade`** — request `{"ticker": "AAPL", "quantity": 10, "side": "buy"}`, response:
```json
{
  "trade": {"id": "uuid", "ticker": "AAPL", "side": "buy", "quantity": 10, "price": 190.5, "executed_at": "2026-10-13T14:00:00Z"},
  "cash_balance": 8095.0
}
```
The frontend refetches `/api/portfolio` after a trade rather than relying on the trade response for portfolio state.

**`GET /api/portfolio/history`** — the most recent 500 snapshots, oldest first:
```json
{"snapshots": [{"total_value": 10000.0, "recorded_at": "2026-10-13T14:00:00Z"}]}
```

**`GET /api/watchlist`** — includes a price snapshot so the grid is populated before the first SSE event arrives; price fields are `null` if no price is cached yet:
```json
{"tickers": [{"ticker": "AAPL", "price": 190.52, "daily_change_percent": 0.27, "added_at": "2026-10-13T14:00:00Z"}]}
```

**`POST /api/watchlist`** — request `{"ticker": "PYPL"}`; returns `201` with the new entry (same shape as an item above). Adding a ticker already on the watchlist returns `200` with the existing entry (idempotent). Unsupported tickers return `400`.

**`DELETE /api/watchlist/{ticker}`** — returns `204`. Removing a ticker not on the watchlist returns `404`.

**`POST /api/chat`** — request `{"message": "Buy 10 AAPL"}`, response:
```json
{
  "message": "Done — bought 10 AAPL at $190.50.",
  "actions": {
    "trades": [
      {"ticker": "AAPL", "side": "buy", "quantity": 10, "status": "executed", "price": 190.5, "error": null}
    ],
    "watchlist_changes": [
      {"ticker": "PYPL", "action": "add", "status": "executed", "error": null}
    ]
  }
}
```
`status` is `"executed"` or `"failed"`; on failure `price` is `null` and `error` holds the same message the REST endpoint would return. `actions` has empty arrays when the assistant took no action. This `actions` object is also what is stored in `chat_messages.actions`.

**`GET /api/chat/history`** — the most recent 100 messages, oldest first:
```json
{"messages": [{"id": "uuid", "role": "assistant", "content": "...", "actions": {...}, "created_at": "2026-10-13T14:00:00Z"}]}
```
`actions` is `null` for user messages.

### Trade Execution Rules

Manual trades (`POST /api/portfolio/trade`) and LLM trades call **the same trade-execution service function** — validation and portfolio math are never duplicated. That function:

1. Normalizes the ticker and validates it with the market data source (Section 6)
2. Requires `quantity` to be a **positive whole number of shares** (no fractions, no dollar amounts); anything else is rejected with `400`
3. Reads the fill price from the price cache; if the ticker has **no cached price**, the trade is rejected (`"No price available for XYZ"`). A trade never adds its ticker to the watchlist.
4. Buy: requires `cash_balance >= quantity × price`; updates the weighted-average `avg_cost`
5. Sell: requires held quantity ≥ requested quantity; `avg_cost` is unchanged; the position is deleted if the remainder is zero
6. Applies the cash update, position update, and trade-log insert in **one SQLite transaction**
7. Records a portfolio snapshot, and updates the tracked-ticker set (a new position starts tracking its ticker; a closed position stops tracking it unless watched)

---

## 9. LLM Integration

When writing code to make calls to LLMs, use cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results.

There is an OPENROUTER_API_KEY in the .env file in the project root.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads the **last 20 messages** of conversation history from the `chat_messages` table
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM **once** via LiteLLM → OpenRouter, requesting structured output, using the cerebras-inference skill
5. Parses the complete structured JSON response
6. Auto-executes any watchlist changes, then any trades (see Auto-Execution)
7. Stores the user message and the assistant message (with its `actions`) in `chat_messages`
8. Returns the complete JSON response to the frontend (shape in Section 8; no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

If the LLM call fails or returns JSON that doesn't match the schema, the endpoint still returns `200` with a message like "Sorry, I couldn't process that — please try again." and empty actions. No actions are executed in that case.

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute. Each trade goes through the same trade-execution function as manual trades (Section 8)
- `watchlist_changes` (optional): Array of watchlist modifications; `action` is `"add"` or `"remove"`

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

Execution rules:
- Watchlist changes run first, then trades, each list in the order the LLM returned it
- Each action executes **independently**: if trade #2 fails, trades #1 and #3 still execute. Each individual trade is atomic (Section 8)
- Every action's result (`executed` or `failed` with an error) is reported in the response's `actions`
- There is no second LLM call. If any action fails, the backend appends a short system-generated note to the LLM's message, one line per failure, e.g. `⚠ Sell 50 TSLA failed: insufficient shares (you hold 10)`. This combined text is what is stored and returned as `message`.

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Always respond with valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

The mock parses the user's message (case-insensitive) with these rules, and the resulting actions go through the normal auto-execution path:

| User message matches | Mock returns |
|---|---|
| `buy <N> <TICKER>` | `message: "Buying <N> <TICKER>."`, one buy trade |
| `sell <N> <TICKER>` | `message: "Selling <N> <TICKER>."`, one sell trade |
| `watch <TICKER>` | `message: "Adding <TICKER> to your watchlist."`, one watchlist add |
| `unwatch <TICKER>` | `message: "Removing <TICKER> from your watchlist."`, one watchlist remove |
| anything else | `message: "This is a mock response from FinAlly."`, no actions |

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), daily change % (`daily_change_percent` from SSE), and a sparkline mini-chart (accumulated from SSE since page load). Tickers with no price yet show "—".
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time (accumulated from SSE since page load). Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, using data from `GET /api/portfolio/history` (polled every 30 seconds)
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill. Errors (e.g. insufficient cash) are shown inline next to the trade bar.
- **AI chat panel** — docked sidebar that can be collapsed/expanded (no drag-to-resize, no persisted layout). Loads prior conversation from `GET /api/chat/history` on page load. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Trade executions and watchlist changes (including failures) shown inline as confirmations, from the response's `actions`.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance

### Data Flow

- **Live total value** is computed on the frontend: `cash_balance + Σ(position.quantity × latest SSE price)`, using positions and cash from `GET /api/portfolio`. Positions table P&L and the heatmap use the same live prices.
- The frontend refetches `/api/portfolio` and `/api/watchlist` after every manual trade, watchlist change, and chat response that contains actions.
- On page load: fetch `/api/portfolio`, `/api/watchlist`, `/api/portfolio/history`, `/api/chat/history`, and open the SSE connection.

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- **Charts**: Lightweight Charts (canvas-based) for the main price chart and the P&L chart. Sparklines may use Lightweight Charts or a small inline SVG. The heatmap uses Recharts' `Treemap` (SVG is fine for a handful of positions). No other charting libraries.
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme
- Static export: `next.config` sets `output: 'export'`, and `images.unoptimized: true` if `next/image` is used

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build (produces static export)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/ to /app
  - uv sync (install Python dependencies from lockfile)
  - Copy frontend build output into /app/static
  - ENV DB_PATH=/app/db/finally.db
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and all API routes on port 8000. The static mount (`StaticFiles(directory="static", html=True)` at `/`) must be registered **after** all `/api` routers, so it doesn't shadow them; `html=True` makes `/` serve `index.html`.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container. The backend writes `finally.db` to this path. Nothing else in the image lives under `/app/db`, so the volume never hides application files.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, Massive API response parsing works, both implementations conform to the abstract interface, ticker validation, `session_open`/`daily_change_percent`
- Portfolio: trade execution logic, P&L calculations, edge cases (selling more than owned, buying with insufficient cash, selling at a loss, trading a ticker with no price, selling a full position deletes the row, tracked tickers stay live after removing a held ticker from the watchlist)
- LLM: structured output parsing handles all valid schemas, graceful handling of malformed responses, trade validation within chat flow, partial failure of multiple actions, mock-mode keyword rules
- API routes: correct status codes, response shapes, error handling (Section 8)

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations
- Portfolio display calculations
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: A separate `docker-compose.test.yml` in `test/` that spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**Environment**: Tests run with `LLM_MOCK=true` by default for speed and determinism.

**Determinism**: Simulator prices are random and tick every ~500ms, so tests must not assert against prices read from the screen. Assert against the fill `price` in the `POST /api/portfolio/trade` (or `/api/chat`) response captured via Playwright's `page.waitForResponse`, or assert relationships (cash decreased, position quantity is 10) rather than exact values.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist
- Buy shares: cash decreases, position appears, portfolio updates
- Sell shares: cash increases, position updates or disappears
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points
- AI chat (mocked): send a message, receive a response, trade execution appears inline
- SSE resilience: disconnect and verify reconnection
