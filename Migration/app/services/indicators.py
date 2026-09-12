"""
Technical indicators, hand-rolled over pandas/numpy.

Deliberately not using pandas-ta: it does `from numpy import NaN as npNaN`,
which numpy removed the alias for in 1.24+ (this repo runs numpy 2.5.1), and
the package hasn't had a real release since ~2021 -- there's no working pin.
pandas and numpy are already proven working here (see market_data.py's
daily_baseline), so these are zero new dependencies.

Every function takes a pandas Series (close prices) or the OHLCV DataFrame
shape MarketDataService.get_candles() returns (columns: time, o, h, l, c, v).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int = 20) -> pd.Series:
    return series.rolling(window).mean()


def ema(series: pd.Series, window: int = 20) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return {"macd": macd_line, "signal": signal_line, "histogram": macd_line - signal_line}


def bollinger_bands(series: pd.Series, window: int = 20, num_std: float = 2.0) -> dict[str, pd.Series]:
    mid = sma(series, window)
    std = series.rolling(window).std()
    return {"upper": mid + num_std * std, "middle": mid, "lower": mid - num_std * std}


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    high, low, close = df["h"], df["l"], df["c"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    typical_price = (df["h"] + df["l"] + df["c"]) / 3
    cum_vol = df["v"].cumsum().replace(0, np.nan)
    return (typical_price * df["v"]).cumsum() / cum_vol


def _last(series: pd.Series) -> float | None:
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return round(float(series.iloc[-1]), 4)


def compute_indicators(df: pd.DataFrame) -> dict:
    """Latest value of each indicator, keyed to match the naming
    trade_signals.technical_data already anticipates (see schema.sql comment)."""
    close = df["c"]
    macd_vals = macd(close)
    bb_vals = bollinger_bands(close)
    return {
        "sma_20": _last(sma(close, 20)),
        "sma_50": _last(sma(close, 50)),
        "ema_20": _last(ema(close, 20)),
        "rsi": _last(rsi(close)),
        "macd": _last(macd_vals["macd"]),
        "macd_signal": _last(macd_vals["signal"]),
        "macd_histogram": _last(macd_vals["histogram"]),
        "bb_upper": _last(bb_vals["upper"]),
        "bb_middle": _last(bb_vals["middle"]),
        "bb_lower": _last(bb_vals["lower"]),
        "atr": _last(atr(df)),
        "vwap": _last(vwap(df)),
    }
