# FinAlly — AI Trading Workstation

An AI-powered trading workstation that streams live market data, lets you trade a simulated $10,000 portfolio, and includes an LLM chat assistant that can analyze your positions and execute trades for you. Think Bloomberg terminal with an AI copilot.

Built entirely by coding agents as the capstone project for an agentic AI coding course. Agents coordinate through the docs in [`planning/`](planning/); the full spec is [`planning/PLAN.md`](planning/PLAN.md).

## Features

- **Live prices** streamed over SSE, with green/red flash animations and sparklines
- **Simulated trading**: market orders, instant fills, no fees
- **Portfolio views**: positions table, P&L heatmap (treemap), and portfolio value chart
- **AI chat**: ask about your portfolio, and have the assistant place trades and edit your watchlist
- **Watchlist**: add or remove tickers by hand or through chat

## Architecture

Everything runs in a single Docker container on port 8000:

| Layer | Tech |
|---|---|
| Frontend | Next.js + TypeScript + Tailwind, built as a static export |
| Backend | FastAPI (Python, managed with `uv`), with SSE streaming |
| Database | SQLite, created and seeded automatically on startup |
| AI | LiteLLM → OpenRouter (Cerebras inference) with structured outputs |
| Market data | Built-in GBM simulator (default) or the Massive/Polygon.io API |

## Status

The market data subsystem is complete (see [`planning/MARKET_DATA_SUMMARY.md`](planning/MARKET_DATA_SUMMARY.md)). The rest of the platform (portfolio, chat, frontend, Docker) is in progress.

## Quick Start

```bash
# optional: create .env in the project root with any keys below
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
# open http://localhost:8000
```

The start/stop scripts in `scripts/` (macOS/Linux and Windows) wrap these commands once they are added.

## Environment Variables

All are optional. Without any keys the app runs on the simulator, and chat replies that the AI is not configured.

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Enables the AI chat assistant |
| `MASSIVE_API_KEY` | Use real market data instead of the simulator |
| `LLM_MOCK` | `true` for deterministic mock chat responses (testing) |
| `DB_PATH` | SQLite file location (default `db/finally.db`) |

## Development

Backend:

```bash
cd backend
uv sync --dev
uv run pytest
uv run ruff check .
```

To watch the market simulator in your terminal: `uv run market_data_demo.py`.

## Project Layout

```
backend/    FastAPI app (uv project): market data, API, DB, LLM
frontend/   Next.js app (static export)
planning/   Specs and shared docs for the agents
scripts/    Start/stop scripts for Docker
test/       Playwright end-to-end tests
db/         Runtime volume for the SQLite database
```

## License

See [LICENSE](LICENSE).
