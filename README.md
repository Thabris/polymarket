# Polymarket Scanner

A strategy-scanner platform for Polymarket prediction markets. Three scanners
emit **signals** (never orders) into a paper-trading pipeline that tracks every
signal to market resolution — building the out-of-sample track record that
decides whether automated execution is ever worth turning on.

## Strategies

| Scanner | Cadence | What it looks for |
|---|---|---|
| **theta** | 15 min (REST) | Near-certain contracts (93–98.5¢) with clean resolution wording, net annualized yield above a hurdle after the market's own fee schedule. Excludes weather/entertainment (research: overconfident domains) and intraday junk. |
| **news_fade** | real-time (WebSocket) | ≥12pp price spikes within 30 min on liquid, non-crypto markets away from resolution. Emits fade *candidates* with a maker entry zone — the resolving-vs-noise judgment stays with you. |
| **calendar** | 60 min (REST) | Deadline-tranche families ("by Aug 31" / "by Sep 30"). Computes implied windows + conditional hazards for browsing; signals only on genuine fee-adjusted monotonicity violations. Handles survival ("continues through") semantics; flags resolution-text mismatches between tranches. |

Signal grades: **A/B** enter the paper book automatically; **C** (subjective
resolution wording, UMA disputes, tranche mismatches) is displayed but never
papered or toasted.

## Run

```powershell
pip install -r requirements.txt
python main.py            # migrations run automatically; Ctrl+C to stop
```

- Dashboard: http://127.0.0.1:8000/static/signals.html (Signals · Calendar · Paper Book · Rankings)
- API docs: http://127.0.0.1:8000/docs — status: `/api/status`, scanner runs: `/api/scanners/status`
- Windows toasts fire for: grade-A theta ≥15% annualized, ≥15pp fades on ≥$100k
  liquidity, and every calendar arb (max 6/hour; everything always reaches the dashboard).

`python main.py --api-only` runs just the API + market sync (UI development).

### Always-on
Task Scheduler → Create Basic Task → "At log on" → Program: `pythonw`,
arguments: `main.py`, start in: this directory.

## Configuration

Everything is env-overridable via `.env` (see `config/settings.py` for the
full list): scan thresholds, fee fallbacks, universe size, toast policy,
paper notional, risk caps. Fee truth comes from each market's own
`feeSchedule`; the category map is only a fallback.

## Paper book & real fills

Every A/B signal opens a hypothetical position: theta/calendar fill at the
executable ask + taker fee; fades fill only if price actually touches the
suggested maker zone within its validity window (fee-free maker fill).
Positions close on resolution (1.0/0.0), news-fade time stop (24h), and
"closed but ambiguous" markets are left open and flagged — never guessed.
Mark a signal you actually traded via **Mark traded** on the Signals page;
real fills are tracked separately from paper.

## Architecture

```
Gamma REST  ─→ UniverseManager ─→ markets/events tables (15-min sync)
CLOB WS     ─→ MarketStream ─→ EventBus ─→ BarRecorder → prices (1-min bars)
                                        └→ NewsFadeScanner
Theta/Calendar scanners (REST) ─→ SignalService ─→ signals table
                                        ├→ toasts (winotify) + browser WS
                                        └→ ExecutionRouter (ABC)
                                             └→ PaperRouter → paper_positions
                                             └→ [future LiveRouter — same seam]
```

Execution-readiness: scanners → `SignalService` → `ExecutionRouter` is the
only path to fills. A future `LiveRouter` (py-clob-client) implements the
same interface with the same `RiskLimits` — nothing else changes.

## Development

```powershell
python -m pytest -q                      # unit + fixture tests
python scripts/probe_gamma.py            # refresh API-contract fixtures
python scripts/probe_ws.py --seconds 60  # refresh WS capture
python scripts/emit_test_signal.py       # inject a synthetic signal (daemon must run)
python scripts/toast_test.py             # toast smoke test
```

Schema changes go through Alembic (`db/migrations`); `create_all` is
test-only. DB lives at `var/polymarket.db` (WAL); logs at `var/daemon.log`.
