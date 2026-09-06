import logging
import time

import pandas as pd

logging.basicConfig(
    filename="trading_bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def setup_api(api_key, api_secret, base_url="https://paper-api.alpaca.markets"):
    # Imported lazily so anything that only does CSV-based backtesting
    # (backtest.py --source csv) doesn't need alpaca-trade-api installed at all.
    from alpaca_trade_api.rest import REST
    return REST(api_key, api_secret, base_url)


def retry_api_call(func, *args, **kwargs):
    max_retries = 5
    last_err = None
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            logging.error(f"Error on attempt {attempt + 1}/{max_retries}: {e}")
            time.sleep(2 ** attempt)
    raise Exception(f"Max retries exceeded: {last_err}")


def get_bars_df(symbol, api, limit=60, timeframe=None):
    """Fetch recent bars as a DataFrame with columns: open, high, low, close, volume."""
    from alpaca_trade_api.rest import TimeFrame
    timeframe = timeframe or TimeFrame.Day
    bars = retry_api_call(api.get_bars, symbol, timeframe, limit=limit)
    data = [{"o": b.o, "h": b.h, "l": b.l, "c": b.c, "v": b.v, "t": b.t} for b in bars]
    if not data:
        return pd.DataFrame(columns=["o", "h", "l", "c", "v", "t"])
    return pd.DataFrame(data)


def get_moving_average(closes, window):
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def get_rsi(closes, period=14):
    """Wilder's RSI (the standard used by virtually every real trading
    platform), not a plain average over one window. A naive simple-average
    RSI overreacts to whatever happens to be in the window and disagrees
    with what any charting tool would show for the same symbol — using the
    same math traders actually see matters here.
    """
    if len(closes) < period * 3:
        # Wilder's smoothing needs a longer runway to converge; too short a
        # history gives a misleading number.
        return None

    series = pd.Series(closes)
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    last_gain = avg_gain.iloc[-1]
    last_loss = avg_loss.iloc[-1]

    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return 100 - (100 / (1 + rs))


def get_macd(closes):
    if len(closes) < 26:
        return None
    series = pd.Series(closes)
    short_ema = series.ewm(span=12, adjust=False).mean()
    long_ema = series.ewm(span=26, adjust=False).mean()
    macd_line = short_ema - long_ema
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd_line": macd_line.iloc[-1],
        "signal_line": signal_line.iloc[-1],
        "macd_histogram": histogram.iloc[-1],
    }


def get_bollinger_bands(closes, window=20, num_std=2):
    if len(closes) < window:
        return None
    series = pd.Series(closes[-window:])
    mid = series.mean()
    std = series.std()
    return {
        "upper": mid + num_std * std,
        "lower": mid - num_std * std,
        "mid": mid,
    }


def get_vwap(df):
    """Volume-weighted average price over the given bar window."""
    if df.empty:
        return None
    typical_price = (df["h"] + df["l"] + df["c"]) / 3
    total_vol = df["v"].sum()
    if total_vol <= 0:
        return None
    return (typical_price * df["v"]).sum() / total_vol


def get_donchian_channel(df, window=20):
    """Highest high / lowest low over `window` bars — used for breakout strategies."""
    if len(df) < window:
        return None
    recent = df.tail(window)
    return {"upper": recent["h"].max(), "lower": recent["l"].min()}


def zscore(closes, window=20):
    """How many standard deviations the latest close is from its recent mean.
    Used for mean-reversion signals."""
    if len(closes) < window:
        return None
    series = pd.Series(closes[-window:])
    std = series.std()
    if std == 0:
        return 0.0
    return (series.iloc[-1] - series.mean()) / std
