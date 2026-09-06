"""
Combines every strategy's opinion into one decision, weighted by how well
each strategy has actually performed for this user so far. This is a simple
performance-weighted ensemble (similar in spirit to a multi-armed bandit):
strategies with a better track record get more say over time, but a
strategy with no track record yet still gets a fair baseline weight so it
can prove itself rather than being frozen out forever.
"""
import math

from strategies import run_all_strategies, STRATEGIES

MIN_WEIGHT = 0.15   # floor so untested/underperforming strategies still get some allocation
BASELINE_TRADES = 5  # a strategy needs at least this many trades before its record counts fully


def strategy_weight(perf_row):
    """Turn a StrategyPerformance row into a weight between MIN_WEIGHT and ~1.0."""
    if perf_row is None or perf_row.trades_count == 0:
        return 0.5  # unproven strategies start at a neutral weight

    # Blend win rate and average P&L into one score, softened for small sample sizes.
    confidence_in_sample = min(perf_row.trades_count / BASELINE_TRADES, 1.0)
    raw_score = (perf_row.win_rate - 0.5) + math.tanh(perf_row.avg_pnl / 10)
    scaled = 0.5 + raw_score * confidence_in_sample
    return max(MIN_WEIGHT, min(scaled, 1.0))


def decide(symbol, closes, df, performance_by_strategy):
    """
    performance_by_strategy: dict of strategy_name -> StrategyPerformance row (or None)

    Returns: {
        "direction": 1 / -1 / 0,
        "score": float,               # combined conviction, 0-1
        "contributing_strategies": [...],  # which strategies agreed with the final call
    }
    """
    signals = run_all_strategies(symbol, closes, df)

    weighted_buy = 0.0
    weighted_sell = 0.0
    total_weight = 0.0
    contributing = []

    for sig in signals:
        w = strategy_weight(performance_by_strategy.get(sig.strategy_name))
        total_weight += w
        if sig.direction == 1:
            weighted_buy += w * sig.confidence
            if sig.confidence > 0:
                contributing.append(sig.strategy_name)
        elif sig.direction == -1:
            weighted_sell += w * sig.confidence
            if sig.confidence > 0:
                contributing.append(sig.strategy_name)

    if total_weight == 0:
        return {"direction": 0, "score": 0.0, "contributing_strategies": []}

    buy_score = weighted_buy / total_weight
    sell_score = weighted_sell / total_weight

    if buy_score > sell_score and buy_score > 0.25:
        return {"direction": 1, "score": buy_score, "contributing_strategies": contributing}
    if sell_score > buy_score and sell_score > 0.25:
        return {"direction": -1, "score": sell_score, "contributing_strategies": contributing}
    return {"direction": 0, "score": 0.0, "contributing_strategies": []}


MIN_ALLOCATION_FLOOR = 0.4  # every buy signal still gets at least 40% of an equal share


def allocate_capital(total_available, symbol_scores):
    """Split the pot across symbols the ensemble actually wants to buy,
    weighted by conviction score — a symbol with a 0.8 combined score gets
    more of the pot than one that barely cleared the 0.25 buy threshold.

    A floor (MIN_ALLOCATION_FLOOR) keeps this from going all-in on a single
    symbol just because it scored highest; every qualifying symbol still
    gets a meaningful position for diversification.

    symbol_scores: dict of symbol -> conviction score (0.0-1.0), already
    filtered to only the symbols with a buy decision.
    """
    if not symbol_scores:
        return {}

    n = len(symbol_scores)
    equal_share = total_available / n
    floor = equal_share * MIN_ALLOCATION_FLOOR

    # Remaining pot after guaranteeing every symbol its floor gets split
    # proportionally to conviction score.
    total_floor = floor * n
    remaining = max(total_available - total_floor, 0)
    total_score = sum(symbol_scores.values()) or 1.0

    return {
        symbol: floor + remaining * (score / total_score)
        for symbol, score in symbol_scores.items()
    }
