"""
Each strategy function takes the same inputs (symbol, closes list, bars
DataFrame) and returns a Signal: which direction it thinks the market is
going, and how confident it is. The ensemble/allocator (ensemble.py) is what
decides how much real money actually follows each strategy's opinion.

Adding a new strategy = write one function here + register it in STRATEGIES.
"""
from dataclasses import dataclass

import utils


@dataclass
class Signal:
    strategy_name: str
    direction: int      # 1 = buy, -1 = sell/avoid, 0 = no opinion
    confidence: float   # 0.0 - 1.0, used to weight sizing within the strategy


def ma_crossover(symbol, closes, df):
    short_ma = utils.get_moving_average(closes, 50)
    long_ma = utils.get_moving_average(closes, 200)
    if short_ma is None or long_ma is None:
        return Signal("ma_crossover", 0, 0.0)
    spread = (short_ma - long_ma) / long_ma
    direction = 1 if short_ma > long_ma else -1
    confidence = min(abs(spread) * 10, 1.0)  # bigger spread = more conviction
    return Signal("ma_crossover", direction, confidence)


def rsi_reversion(symbol, closes, df):
    rsi = utils.get_rsi(closes, period=14)
    if rsi is None:
        return Signal("rsi_reversion", 0, 0.0)
    if rsi < 30:
        return Signal("rsi_reversion", 1, min((30 - rsi) / 30, 1.0))
    if rsi > 70:
        return Signal("rsi_reversion", -1, min((rsi - 70) / 30, 1.0))
    return Signal("rsi_reversion", 0, 0.0)


def macd_momentum(symbol, closes, df):
    macd = utils.get_macd(closes)
    if macd is None:
        return Signal("macd_momentum", 0, 0.0)
    hist = macd["macd_histogram"]
    direction = 1 if hist > 0 else (-1 if hist < 0 else 0)
    confidence = min(abs(hist) / (abs(macd["macd_line"]) + 1e-9), 1.0)
    return Signal("macd_momentum", direction, confidence)


def bollinger_reversion(symbol, closes, df):
    bands = utils.get_bollinger_bands(closes, window=20, num_std=2)
    if bands is None:
        return Signal("bollinger_reversion", 0, 0.0)
    price = closes[-1]
    band_width = bands["upper"] - bands["lower"]
    if band_width <= 0:
        return Signal("bollinger_reversion", 0, 0.0)
    if price <= bands["lower"]:
        return Signal("bollinger_reversion", 1, min((bands["lower"] - price) / band_width + 0.3, 1.0))
    if price >= bands["upper"]:
        return Signal("bollinger_reversion", -1, min((price - bands["upper"]) / band_width + 0.3, 1.0))
    return Signal("bollinger_reversion", 0, 0.0)


def zscore_mean_reversion(symbol, closes, df):
    z = utils.zscore(closes, window=20)
    if z is None:
        return Signal("zscore_mean_reversion", 0, 0.0)
    if z <= -1.5:
        return Signal("zscore_mean_reversion", 1, min(abs(z) / 3, 1.0))
    if z >= 1.5:
        return Signal("zscore_mean_reversion", -1, min(abs(z) / 3, 1.0))
    return Signal("zscore_mean_reversion", 0, 0.0)


def donchian_breakout(symbol, closes, df):
    channel = utils.get_donchian_channel(df, window=20)
    if channel is None:
        return Signal("donchian_breakout", 0, 0.0)
    price = closes[-1]
    if price >= channel["upper"]:
        return Signal("donchian_breakout", 1, 0.7)
    if price <= channel["lower"]:
        return Signal("donchian_breakout", -1, 0.7)
    return Signal("donchian_breakout", 0, 0.0)


def vwap_trend(symbol, closes, df):
    vwap = utils.get_vwap(df)
    if vwap is None:
        return Signal("vwap_trend", 0, 0.0)
    price = closes[-1]
    spread = (price - vwap) / vwap
    direction = 1 if price > vwap else -1
    confidence = min(abs(spread) * 15, 1.0)
    return Signal("vwap_trend", direction, confidence)


# Registry — add new strategies here and they're automatically included in
# the ensemble and get their own performance tracking.
STRATEGIES = {
    "ma_crossover": ma_crossover,
    "rsi_reversion": rsi_reversion,
    "macd_momentum": macd_momentum,
    "bollinger_reversion": bollinger_reversion,
    "zscore_mean_reversion": zscore_mean_reversion,
    "donchian_breakout": donchian_breakout,
    "vwap_trend": vwap_trend,
}


def run_all_strategies(symbol, closes, df):
    """Run every registered strategy against the same data and return their signals."""
    signals = []
    for name, fn in STRATEGIES.items():
        try:
            signals.append(fn(symbol, closes, df))
        except Exception:
            signals.append(Signal(name, 0, 0.0))
    return signals
