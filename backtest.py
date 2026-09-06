"""
Walk-forward backtest for every strategy in strategies.py, plus the
ensemble, against historical daily bars. This is the thing that turns "will
this make money" from a guess into a number.

IMPORTANT — what this can and can't tell you:
  - It tells you how this specific strategy set would have performed on
    the specific symbols/date range you test, AFTER an estimated cost of
    trading (spread + slippage). That's real signal.
  - It does NOT prove the strategy will keep working — markets change, and
    a strategy discovered by testing many variants against the same
    historical data can look good purely by chance (overfitting). Test on
    a range of symbols and time periods, not just whichever one looks best.
  - It always prints a buy-and-hold benchmark next to the strategy results.
    If buy-and-hold beats every strategy after costs, that's the honest
    answer: this strategy set is a dead end for that symbol/period, and
    trading it adds cost and risk without benefit over just holding.

Usage:
    # Using your own Alpaca keys (paper is fine, backtesting is read-only):
    python backtest.py --source alpaca --symbols AAPL,MSFT --start 2022-01-01 --end 2024-06-01

    # Using a local CSV (columns: t,o,h,l,c,v) — no API keys required:
    python backtest.py --source csv --csv-path my_data.csv --symbols AAPL
"""
import argparse
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import ensemble
from strategies import STRATEGIES, run_all_strategies
from utils import setup_api, get_bars_df

SPREAD_BPS_DEFAULT = 5  # 0.05% per side — a rough stand-in for spread + slippage on liquid large-caps.
                         # Thinner/less liquid symbols should use a higher number here.


@dataclass
class SimplePerformance:
    """Same interface ensemble.strategy_weight() expects from a DB row
    (trades_count, win_rate, avg_pnl), but in-memory only — the backtest
    doesn't touch the database or need a Flask app context.
    """
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    trades_count: int = 0

    def record_trade(self, pnl):
        self.trades_count += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

    @property
    def win_rate(self):
        return self.wins / self.trades_count if self.trades_count else 0.0

    @property
    def avg_pnl(self):
        return self.total_pnl / self.trades_count if self.trades_count else 0.0


def load_bars_alpaca(symbol, api_key, api_secret, start, end, base_url="https://paper-api.alpaca.markets"):
    from alpaca_trade_api.rest import TimeFrame
    api = setup_api(api_key, api_secret, base_url)
    bars = api.get_bars(symbol, TimeFrame.Day, start=start, end=end)
    data = [{"t": b.t, "o": b.o, "h": b.h, "l": b.l, "c": b.c, "v": b.v} for b in bars]
    return pd.DataFrame(data)


def load_bars_csv(path):
    """Expects columns: t,o,h,l,c,v (t = date, rest = OHLCV)."""
    df = pd.read_csv(path)
    required = {"t", "o", "h", "l", "c", "v"}
    if not required.issubset(df.columns):
        raise ValueError(f"CSV must have columns {required}, got {set(df.columns)}")
    return df


def _apply_spread(price, side, spread_bps):
    """Model the cost of crossing the bid/ask spread: buys fill a little
    worse than the quoted price, sells too."""
    adj = price * (spread_bps / 10000)
    return price + adj if side == "buy" else price - adj


def run_single_strategy_backtest(symbol, df, strategy_name, initial_capital=10000,
                                  profit_target=0.10, stop_loss=0.05, spread_bps=SPREAD_BPS_DEFAULT,
                                  min_history=220):
    strategy_fn = STRATEGIES[strategy_name]
    closes_all = df["c"].tolist()

    capital = initial_capital
    position = None  # dict: entry_price, notional, entry_index
    trades = []
    equity_curve = []

    for i in range(min_history, len(df)):
        window_df = df.iloc[: i + 1]
        closes = closes_all[: i + 1]
        price = closes[-1]

        if position is None:
            sig = strategy_fn(symbol, closes, window_df)
            if sig.direction == 1 and sig.confidence > 0.25:
                fill_price = _apply_spread(price, "buy", spread_bps)
                qty = capital / fill_price
                position = {"entry_price": fill_price, "qty": qty, "entry_index": i}
        else:
            hit_target = price >= position["entry_price"] * (1 + profit_target)
            hit_stop = price <= position["entry_price"] * (1 - stop_loss)
            if hit_target or hit_stop:
                fill_price = _apply_spread(price, "sell", spread_bps)
                pnl = (fill_price - position["entry_price"]) * position["qty"]
                capital += pnl
                trades.append({"entry_index": position["entry_index"], "exit_index": i, "pnl": pnl,
                                "reason": "target" if hit_target else "stop"})
                position = None

        mark_price = closes[-1]
        unrealized = (mark_price - position["entry_price"]) * position["qty"] if position else 0
        equity_curve.append(capital + unrealized)

    # Close any still-open position at the last available price for scoring purposes.
    if position is not None:
        fill_price = _apply_spread(closes_all[-1], "sell", spread_bps)
        pnl = (fill_price - position["entry_price"]) * position["qty"]
        capital += pnl
        trades.append({"entry_index": position["entry_index"], "exit_index": len(df) - 1, "pnl": pnl, "reason": "end_of_period"})

    return {"trades": trades, "equity_curve": equity_curve, "final_capital": capital}


def run_ensemble_backtest(symbol, df, initial_capital=10000, profit_target=0.10, stop_loss=0.05,
                           spread_bps=SPREAD_BPS_DEFAULT, min_history=220):
    closes_all = df["c"].tolist()
    performance = {name: SimplePerformance() for name in STRATEGIES}

    capital = initial_capital
    position = None
    trades = []
    equity_curve = []

    for i in range(min_history, len(df)):
        window_df = df.iloc[: i + 1]
        closes = closes_all[: i + 1]
        price = closes[-1]

        if position is None:
            decision = ensemble.decide(symbol, closes, window_df, performance)
            if decision["direction"] == 1 and decision["score"] > 0.25:
                fill_price = _apply_spread(price, "buy", spread_bps)
                qty = capital / fill_price
                position = {"entry_price": fill_price, "qty": qty, "entry_index": i,
                            "strategies": decision["contributing_strategies"]}
        else:
            hit_target = price >= position["entry_price"] * (1 + profit_target)
            hit_stop = price <= position["entry_price"] * (1 - stop_loss)
            if hit_target or hit_stop:
                fill_price = _apply_spread(price, "sell", spread_bps)
                pnl = (fill_price - position["entry_price"]) * position["qty"]
                capital += pnl
                trades.append({"entry_index": position["entry_index"], "exit_index": i, "pnl": pnl,
                                "reason": "target" if hit_target else "stop"})
                for name in position["strategies"]:
                    performance[name].record_trade(pnl)
                position = None

        mark_price = closes[-1]
        unrealized = (mark_price - position["entry_price"]) * position["qty"] if position else 0
        equity_curve.append(capital + unrealized)

    if position is not None:
        fill_price = _apply_spread(closes_all[-1], "sell", spread_bps)
        pnl = (fill_price - position["entry_price"]) * position["qty"]
        capital += pnl
        trades.append({"entry_index": position["entry_index"], "exit_index": len(df) - 1, "pnl": pnl, "reason": "end_of_period"})
        for name in position["strategies"]:
            performance[name].record_trade(pnl)

    return {"trades": trades, "equity_curve": equity_curve, "final_capital": capital, "strategy_performance": performance}


def buy_and_hold_benchmark(df, initial_capital=10000, min_history=220, spread_bps=SPREAD_BPS_DEFAULT):
    closes = df["c"].tolist()[min_history:]
    if not closes:
        return {"final_capital": initial_capital, "equity_curve": []}
    entry = _apply_spread(closes[0], "buy", spread_bps)
    qty = initial_capital / entry
    equity_curve = [qty * c for c in closes]
    exit_price = _apply_spread(closes[-1], "sell", spread_bps)
    final_capital = qty * exit_price
    return {"final_capital": final_capital, "equity_curve": equity_curve}


def compute_metrics(result, initial_capital):
    trades = result.get("trades", [])
    equity_curve = result.get("equity_curve", [])
    final_capital = result["final_capital"]

    total_return_pct = (final_capital - initial_capital) / initial_capital * 100
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    avg_pnl = sum(t["pnl"] for t in trades) / len(trades) if trades else 0.0

    max_drawdown_pct = 0.0
    if equity_curve:
        peak = equity_curve[0]
        for v in equity_curve:
            peak = max(peak, v)
            drawdown = (peak - v) / peak if peak > 0 else 0
            max_drawdown_pct = max(max_drawdown_pct, drawdown * 100)

    sharpe = None
    if len(equity_curve) > 2:
        eq = pd.Series(equity_curve)
        daily_returns = eq.pct_change().dropna()
        if daily_returns.std() > 0:
            sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)

    return {
        "total_return_pct": round(total_return_pct, 2),
        "num_trades": len(trades),
        "win_rate_pct": round(win_rate * 100, 1),
        "avg_pnl": round(avg_pnl, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else "n/a (too few trades)",
        "final_capital": round(final_capital, 2),
    }


def run_full_report(symbol, df, initial_capital=10000, spread_bps=SPREAD_BPS_DEFAULT):
    print(f"\n{'=' * 70}\n{symbol} — {len(df)} bars\n{'=' * 70}")

    bh = buy_and_hold_benchmark(df, initial_capital, spread_bps=spread_bps)
    bh_metrics = compute_metrics(bh, initial_capital)
    print(f"{'Buy & hold (benchmark)':<28} return {bh_metrics['total_return_pct']:>7}%  "
          f"max DD {bh_metrics['max_drawdown_pct']:>6}%  final ${bh_metrics['final_capital']:>10}")
    print("-" * 70)

    rows = []
    for name in STRATEGIES:
        result = run_single_strategy_backtest(symbol, df, name, initial_capital, spread_bps=spread_bps)
        m = compute_metrics(result, initial_capital)
        rows.append((name, m))
        print(f"{name:<28} return {m['total_return_pct']:>7}%  trades {m['num_trades']:>4}  "
              f"win% {m['win_rate_pct']:>5}  avg P&L ${m['avg_pnl']:>7}  max DD {m['max_drawdown_pct']:>6}%  "
              f"sharpe {m['sharpe']}")

    ens_result = run_ensemble_backtest(symbol, df, initial_capital, spread_bps=spread_bps)
    m = compute_metrics(ens_result, initial_capital)
    print("-" * 70)
    print(f"{'ENSEMBLE (all combined)':<28} return {m['total_return_pct']:>7}%  trades {m['num_trades']:>4}  "
          f"win% {m['win_rate_pct']:>5}  avg P&L ${m['avg_pnl']:>7}  max DD {m['max_drawdown_pct']:>6}%  "
          f"sharpe {m['sharpe']}")

    beat_benchmark = m["total_return_pct"] > bh_metrics["total_return_pct"]
    verdict = "beat buy-and-hold" if beat_benchmark else "did NOT beat buy-and-hold"
    print(f"\nVerdict for {symbol}: ensemble {verdict} after estimated costs "
          f"({spread_bps} bps/side spread).")

    return {"symbol": symbol, "buy_and_hold": bh_metrics, "strategies": dict(rows), "ensemble": m}


def main():
    parser = argparse.ArgumentParser(description="Backtest Alpine Alpaca strategies against historical data.")
    parser.add_argument("--source", choices=["alpaca", "csv"], default="alpaca")
    parser.add_argument("--symbols", required=True, help="Comma-separated, e.g. AAPL,MSFT")
    parser.add_argument("--start", help="YYYY-MM-DD (required for --source alpaca)")
    parser.add_argument("--end", help="YYYY-MM-DD (required for --source alpaca)")
    parser.add_argument("--csv-path", help="Path to CSV with columns t,o,h,l,c,v (required for --source csv)")
    parser.add_argument("--capital", type=float, default=10000)
    parser.add_argument("--spread-bps", type=float, default=SPREAD_BPS_DEFAULT)
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    for symbol in symbols:
        if args.source == "alpaca":
            api_key = os.environ.get("ALPACA_API_KEY")
            api_secret = os.environ.get("ALPACA_API_SECRET")
            if not api_key or not api_secret:
                raise RuntimeError("Set ALPACA_API_KEY / ALPACA_API_SECRET env vars for --source alpaca.")
            if not args.start or not args.end:
                raise RuntimeError("--start and --end are required for --source alpaca.")
            df = load_bars_alpaca(symbol, api_key, api_secret, args.start, args.end)
        else:
            if not args.csv_path:
                raise RuntimeError("--csv-path is required for --source csv.")
            df = load_bars_csv(args.csv_path)

        if len(df) < 221:
            print(f"Skipping {symbol}: only {len(df)} bars, need 220+ for the longest lookback (200-day MA).")
            continue

        run_full_report(symbol, df, initial_capital=args.capital, spread_bps=args.spread_bps)


if __name__ == "__main__":
    main()
