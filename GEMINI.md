# Project Instructions: polymarket-ai-bot

## Core Rules
- **Environment:** Always use `.venv/bin/python3` for executing scripts, tests, and tools.
- **Trading Mode:** `LIVE_TRADING` must remain `False`. All execution must be in Paper Trading or Read-only mode unless explicitly changed by the user.
- **Safety:** Do NOT stop active background processes or modify `.env` without explicit instruction.
- **Persistence:** The primary database is `data/trading_bot.db`.
- **Modularity:** Follow the established directory structure (`connectors/`, `models/`, `services/`, `executors/`, `database/`, `tools/`).

## Architectural Conventions
- **Risk Management:** Dynamic sizing based on Fractional Kelly (25%) with boundaries [2%, 10%] of total capital.
- **Validation:** Every trade must pass through the `MarketManipulationGuard` and `CLOBDepthVerifier`.
- **Data Sourcing:**
  - Polymarket: Gamma API (Metadata), CLOB API (Orderbook), Data API (Recent Trades).
  - Binance: Spot API for real-time price discovery and volatility.
