"""
================================================================================
SHARED TECHNICAL INDICATORS  v1.0
================================================================================
Single source of truth for all indicator functions used across the scanner
engine. Extracted from master_orchestrator.py to eliminate duplication.

Imported by:
  master_orchestrator.py, alpha_scanner.py, short_scanner.py, ignition_radar.py

Functions:
  compute_rsi      — RSI series
  compute_atr      — ATR series + scalar
  compute_macd     — MACD line, signal line, histogram
  compute_adx      — ADX, +DI, -DI
  compute_obv      — On-Balance Volume series
  compute_supertrend — SuperTrend direction series
  compute_cmf      — Chaikin Money Flow
  compute_bb       — Bollinger Bands (upper, mid, lower)
================================================================================
"""

import numpy as np
import pandas as pd


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index.
    Returns a pd.Series of RSI values (0-100). NaN where insufficient data.
    """
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range.
    Expects df with columns: high, low, close.
    Returns a pd.Series of ATR values.
    """
    highs  = df["high"]
    lows   = df["low"]
    closes = df["close"]
    tr = pd.concat(
        [
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows  - closes.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def compute_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD indicator.
    Returns (macd_line, signal_line, histogram).
    """
    ema_fast   = series.ewm(span=fast,   adjust=False).mean()
    ema_slow   = series.ewm(span=slow,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_adx(
    df: pd.DataFrame,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Average Directional Index.
    Expects df with columns: high, low, close.
    Returns (adx, plus_di, minus_di) as pd.Series.
    """
    highs  = df["high"]
    lows   = df["low"]
    closes = df["close"]

    up       = highs.diff()
    down     = -lows.diff()
    plus_dm  = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)

    tr = pd.concat(
        [
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows  - closes.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr_s    = tr.rolling(period).mean().replace(0, np.nan)
    plus_di  = 100 * (plus_dm.rolling(period).mean()  / atr_s)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr_s)
    dx       = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx      = dx.rolling(period).mean()

    return adx, plus_di, minus_di


def compute_obv(df: pd.DataFrame) -> pd.Series:
    """
    On-Balance Volume.
    Expects df with columns: close, volume.
    Returns cumulative OBV series.
    """
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def compute_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.Series:
    """
    SuperTrend indicator.
    Expects df with columns: high, low, close.
    Returns a boolean pd.Series: True = bullish (price above SuperTrend line).
    Uses numpy arrays for performance.
    """
    n = len(df)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)

    # True Range
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

    # ATR (simple rolling mean)
    atr = np.zeros(n)
    for i in range(period, n):
        atr[i] = tr[i - period + 1 : i + 1].mean()

    hl2         = (h + l) / 2.0
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    upper = upper_basic.copy()
    lower = lower_basic.copy()

    for i in range(1, n):
        upper[i] = (
            upper_basic[i]
            if (upper_basic[i] < upper[i - 1] or c[i - 1] > upper[i - 1])
            else upper[i - 1]
        )
        lower[i] = (
            lower_basic[i]
            if (lower_basic[i] > lower[i - 1] or c[i - 1] < lower[i - 1])
            else lower[i - 1]
        )

    # Determine SuperTrend line and trend direction
    st = np.zeros(n)
    in_uptrend = True
    for i in range(period, n):
        if atr[i] == 0:
            continue
        if c[i] > upper[i - 1]:
            in_uptrend = True
        elif c[i] < lower[i - 1]:
            in_uptrend = False
        st[i] = lower[i] if in_uptrend else upper[i]

    # Build boolean series: True = bullish
    bullish = np.zeros(n, dtype=bool)
    for i in range(period, n):
        if st[i] != 0 and not np.isnan(st[i]):
            bullish[i] = float(c[i]) > st[i]

    return pd.Series(bullish, index=df.index)


def compute_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Chaikin Money Flow.
    Expects df with columns: high, low, close, volume.
    Returns CMF series (range -1 to +1).
    """
    hl   = (df["high"] - df["low"]).replace(0, np.nan)
    mfm  = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl
    mfv  = mfm * df["volume"]
    vol_sum = df["volume"].rolling(period).sum().replace(0, np.nan)
    return mfv.rolling(period).sum() / vol_sum


def compute_bb(
    series: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Bollinger Bands.
    Returns (upper, mid, lower) as pd.Series.
    """
    mid   = series.rolling(period).mean()
    std   = series.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower


def compute_keltner(
    df: pd.DataFrame,
    period: int = 20,
    atr_period: int = 10,
    multiplier: float = 1.5,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Keltner Channels.
    Expects df with columns: high, low, close.
    Returns (upper, mid, lower) as pd.Series.
    mid = EMA(close, period); bands = mid ± multiplier × ATR(atr_period).
    When BB bands sit inside KC bands this is a true TTM squeeze — high-quality
    bb_squeeze signal confirming compression before expansion.
    """
    mid   = df["close"].ewm(span=period, adjust=False).mean()
    atr   = compute_atr(df, atr_period)
    upper = mid + multiplier * atr
    lower = mid - multiplier * atr
    return upper, mid, lower
