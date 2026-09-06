# Alpine Alpaca

Multi-user, multi-strategy autonomous trading bot for the
[Alpaca](https://alpaca.markets) API, installable as a PWA.

## ⚠️ Read this first

- This trades **real money** if you enable live mode. Test extensively on
  the Alpaca **paper** API before ever switching a user to live.
- I'm not a lawyer or financial advisor. If you host this for other people,
  get real legal advice on whether running algorithmic trades tied to other
  people's brokerage accounts requires any registration in your
  jurisdiction. This build deliberately keeps **each user's funds and API
  keys separate** (never pooled) to keep the model as simple as possible,
  but "simple" isn't the same as "definitely fine" — check.
- Past performance of any strategy here (or the ensemble) is not a
  guarantee of future results. Markets change; strategies that worked
  yesterday can stop working.

## What's new in this version

- **Accounts** — each user registers, logs in, and connects their own
  Alpaca key. Passwords are hashed; API keys are encrypted at rest
  (see `crypto_utils.py` for the current approach and its limits).
- **Seven independent strategies** running side by side per symbol:
  moving-average crossover, RSI mean-reversion, MACD momentum, Bollinger
  Band reversion, Z-score mean-reversion, Donchian channel breakout, and
  VWAP trend. See `strategies.py`.
- **Adaptive ensemble** (`ensemble.py`) — each strategy's real trade outcomes
  are tracked per user (`StrategyPerformance` in `models.py`). Strategies
  with a better track record get more weight in future decisions; new or
  underperforming strategies still get a baseline floor so they can prove
  themselves rather than being locked out permanently.
- **Dashboard** — a strategy leaderboard and trade log, not just a start/stop
  button.
- **PWA** — `static/manifest.json` + `static/service-worker.js` make it
  installable on a phone/desktop home screen.
- **Market tab** (`market_view.py`, `templates/market.html`) — a read-only
  view of what the ensemble currently thinks about each watchlist symbol,
  tabbed by symbol and by individual strategy. Never places an order.
- **Conviction-weighted capital allocation** (`ensemble.allocate_capital`) —
  symbols the ensemble feels more strongly about get more of the available
  capital, not an equal split regardless of signal strength. A floor keeps
  every qualifying symbol diversified rather than going all-in on one pick.
- **Local artwork** — the alpaca image and app icons are bundled under
  `static/images/` and `static/icons/` instead of hotlinked, so nothing
  breaks if an external image host goes down or is region-blocked.

## Running this headlessly / in CI

I can't run this myself — my environment has no internet access at all, so
nothing here has ever actually called Alpaca's servers. Two ways to get a
real headless run, both included:

**GitHub Actions (recommended for testing before you deploy):**
1. Push this repo to GitHub.
2. Repo Settings → Secrets and variables → Actions → add `ALPACA_API_KEY`
   and `ALPACA_API_SECRET` (use your **paper** keys — this workflow only
   backtests, it never places an order, but keep live keys out of CI as a
   habit).
3. Actions tab → "Backtest" → "Run workflow" → fill in symbols/dates → it
   runs on GitHub's servers and the results land in the run log and as a
   downloadable artifact.

A second workflow (`ci.yml`) runs automatically on every push — it's a pure
syntax/import sanity check, needs no real secrets, and catches broken code
before it ever reaches a deployment.

**Docker (for actually running the app, not just backtesting):**
```bash
docker build -t alpine-alpaca .
docker run -p 8080:8080 \
  -e SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
  -e MASTER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  alpine-alpaca
```
I haven't been able to test-build this image myself (no Docker available in
my environment) — please run `docker build .` once yourself to confirm
before deploying anywhere.

## Setup

```bash
pip install -r requirements.txt

# Generate two secrets — put them somewhere safe, not in git:
python -c "import secrets; print(secrets.token_hex(32))"        # -> SECRET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # -> MASTER_KEY

export SECRET_KEY=<paste here>
export MASTER_KEY=<paste here>

python app.py
```

Open http://127.0.0.1:8080, sign up, connect your Alpaca **paper** key first.

## Backtesting — answering "does this actually make money"

Before trusting any strategy (or the ensemble) with real money, run it
against historical data:

```bash
# Using your own Alpaca keys (paper keys work fine — this is read-only):
export ALPACA_API_KEY=your_key
export ALPACA_API_SECRET=your_secret
python backtest.py --source alpaca --symbols AAPL,MSFT,NVDA --start 2022-01-01 --end 2024-06-01

# Or from a local CSV (columns: t,o,h,l,c,v) if you have your own historical data:
python backtest.py --source csv --csv-path your_data.csv --symbols AAPL
```

This prints, per symbol: every individual strategy's return/win-rate/max-drawdown/Sharpe,
the combined ensemble's numbers, and — critically — a **buy-and-hold benchmark**
using the same estimated trading costs. If a strategy or the ensemble doesn't
beat buy-and-hold after costs, that's the honest signal to not trade it, not
a reason to keep tweaking parameters until it does (that way lies overfitting
to one specific historical period).

Read `backtest.py`'s module docstring for what this can't tell you — mainly:
a good backtest result doesn't guarantee the same edge continues going forward.

## Algorithm notes

- **RSI** uses Wilder's smoothing (the standard exponential-smoothing method
  used by nearly all real trading platforms), not a plain average over one
  window — the two can disagree noticeably on the same data.
- **The 200-day moving average needs 200+ days of history to mean anything.**
  The trading loop fetches 220 daily bars per symbol per cycle specifically
  so `ma_crossover` has enough data — if you shorten that fetch, that
  strategy will silently stop producing opinions (it already happened once
  during development; worth a regression test if this changes again).
- **VWAP and Donchian channel here operate on daily bars**, which is a
  simplification — VWAP is conventionally an intraday measure. Fine for a
  first version; revisit if you want VWAP to mean what a day-trader expects.

## Known limitations / next steps (roadmap)

- **Single-process only right now.** Running threads and stop-events live in
  an in-memory Python dict (`app.py`). That's fine for one server process,
  but once this is deployed with multiple gunicorn workers or across
  multiple machines, each worker has its own view of "who's trading" —
  move this to a real task queue (Celery + Redis, or similar) before that.
- **PWA icons are placeholders** (`static/icons/*.png`) — swap in real icons
  generated from your artwork before shipping.
- ~~No backtesting harness~~ **Done — see `backtest.py`.** Still worth
  extending: transaction cost assumptions are a rough estimate (`--spread-bps`),
  and it doesn't yet account for slippage on illiquid symbols or PDT-rule
  constraints on account size.
- **No notifications yet** — email/SMS/push alerts on trades and errors are
  a natural next addition.
- **Encryption is single-master-key** — fine for early testing, but before
  a real hosted multi-tenant launch, move to per-user derived keys or a
  managed secrets service (see comments in `crypto_utils.py`).

## Project layout

```
app.py              Flask app factory: auth, dashboard routes, per-user threads
models.py           User, ApiCredential, StrategyPerformance, TradeLog
crypto_utils.py      Encrypt/decrypt API credentials at rest
strategies.py         Seven independent trading strategies
ensemble.py           Combines strategy signals, weighted by track record + capital allocation
trading_bot.py        Runs the trade cycle for one user's account
market_view.py         Read-only strategy/symbol snapshot for the Market tab
backtest.py             Walk-forward backtest engine + buy-and-hold benchmark
Dockerfile              Headless, reproducible container build
.github/workflows/      backtest.yml (headless backtesting via GitHub) + ci.yml (syntax checks)
utils.py               Alpaca API wrapper + indicator math
templates/            base.html, login.html, register.html, dashboard.html, market.html
static/                styles.css, script.js, manifest.json, service-worker.js,
                        icons/ (PWA icons), images/ (bundled alpaca artwork)
```

## License

See `LICENSE`.
