"""
Builds a read-only snapshot of what the ensemble currently thinks about each
watchlist symbol — used by the dashboard's "Market" tab. This never places
an order; it's purely informational so a user can see the reasoning before
(or instead of) letting the bot trade automatically.
"""
from utils import setup_api, get_bars_df
from strategies import run_all_strategies
import ensemble
from trading_bot import get_stock_list


def build_snapshot(api_key, api_secret, base_url, performance_by_strategy):
    api = setup_api(api_key, api_secret, base_url)
    watchlist = get_stock_list(api)

    by_symbol = []
    by_strategy = {}

    for symbol in watchlist:
        try:
            df = get_bars_df(symbol, api, limit=220)
            closes = df["c"].tolist() if not df.empty else []
            if len(closes) < 20:
                continue

            current_price = closes[-1]
            signals = run_all_strategies(symbol, closes, df)
            decision = ensemble.decide(symbol, closes, df, performance_by_strategy)

            by_symbol.append({
                "symbol": symbol,
                "price": current_price,
                "direction": decision["direction"],
                "score": decision["score"],
                "signals": [
                    {"strategy": s.strategy_name, "direction": s.direction, "confidence": round(s.confidence, 2)}
                    for s in signals
                ],
            })

            for s in signals:
                by_strategy.setdefault(s.strategy_name, []).append({
                    "symbol": symbol,
                    "direction": s.direction,
                    "confidence": round(s.confidence, 2),
                })
        except Exception:
            continue

    # Sort symbols by strongest conviction first, buys before sells before neutral.
    by_symbol.sort(key=lambda x: (-x["direction"], -x["score"]))

    return {"by_symbol": by_symbol, "by_strategy": by_strategy}
