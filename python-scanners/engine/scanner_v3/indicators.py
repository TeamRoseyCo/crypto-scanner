"""
================================================================================
SHARED TECHNICAL INDICATORS  v2.0
================================================================================
Single source of truth for all indicator functions used across the scanner
engine. Extended from v1.0 (which lived in master_orchestrator's tree) to
include the indicators previously inlined in enhanced_scan.py.

v2.0 additions:
  compute_dema, compute_tema  — double / triple EMA
  compute_hull_ma             — Hull moving average
  compute_aroon               — Aroon up / down
  compute_ichimoku            — full Ichimoku cloud
  compute_stoch_rsi           — Stochastic RSI (%K, %D)
  compute_mfi                 — Money Flow Index
  compute_cci                 — Commodity Channel Index
  compute_psar                — Parabolic SAR (with bullish flag)
  compute_slope               — Linear regression slope over a window
  find_pivots                 — Swing-high / swing-low detection (left/right bars)

Existing functions from v1.0 are unchanged so the legacy scanners
(spot_scanner, ignition_radar, prepump_radar) keep working.

Imported by:
  spot_scanner.py, ignition_radar.py, prepump_radar.py  (legacy)
  signals.py, data.py                                    (v3 system)
================================================================================
"""

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# v1.0 INDICATORS  — unchanged, do not modify
# ─────────────────────────────────────────────────────────────────────────────

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index. period=14 default kept for v1.0 compatibility.
    For the v3 signal layer we explicitly pass period=7.

    Edge cases handled:
      - All bars rising (loss=0): RSI = 100 (mathematical limit)
      - All bars flat (gain=0, loss=0): RSI = 50 (neutral)
    """
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()

    # Compute RS, then handle the boundary cases explicitly so we don't
    # leak NaN into downstream signals.
    rs   = gain / loss.replace(0, np.nan)
    rsi  = 100 - (100 / (1 + rs))

    # When loss==0 and gain>0 → RSI = 100
    rsi  = rsi.where(~((loss == 0) & (gain >  0)), 100.0)
    # When both are 0 (flat) → RSI = 50 (neutral)
    rsi  = rsi.where(~((loss == 0) & (gain == 0)),  50.0)
    return rsi


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range. Expects df with high, low, close."""
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
    """MACD. Returns (macd_line, signal_line, histogram)."""
    ema_fast    = series.ewm(span=fast, adjust=False).mean()
    ema_slow    = series.ewm(span=slow, adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram   = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_adx(
    df: pd.DataFrame,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """ADX. Returns (adx, plus_di, minus_di)."""
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
    """On-Balance Volume cumulative series."""
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def compute_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.Series:
    """
    SuperTrend bullish flag (True = price above ST line).
    NOTE: kept as-is from v1.0 for compatibility. The loop-based
    implementation is not the bottleneck — vectorize later if needed.
    """
    n = len(df)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)

    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

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

    bullish = np.zeros(n, dtype=bool)
    for i in range(period, n):
        if st[i] != 0 and not np.isnan(st[i]):
            bullish[i] = float(c[i]) > st[i]

    return pd.Series(bullish, index=df.index)


def compute_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Chaikin Money Flow."""
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
    """Bollinger Bands. Returns (upper, mid, lower)."""
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
    mid = EMA(close, period); bands = mid ± multiplier × ATR(atr_period).
    When BB bands sit inside KC bands → TTM squeeze (high-quality compression).
    """
    mid   = df["close"].ewm(span=period, adjust=False).mean()
    atr   = compute_atr(df, atr_period)
    upper = mid + multiplier * atr
    lower = mid - multiplier * atr
    return upper, mid, lower


# ─────────────────────────────────────────────────────────────────────────────
# v2.0 ADDITIONS  — moved from enhanced_scan.py inline implementations and
#                   from places they were inlined across other scanners
# ─────────────────────────────────────────────────────────────────────────────

def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average. Convenience wrapper for consistency."""
    return series.ewm(span=period, adjust=False).mean()


def compute_dema(series: pd.Series, period: int) -> pd.Series:
    """Double EMA: 2*EMA(x) − EMA(EMA(x)). Less lag than plain EMA."""
    e1 = compute_ema(series, period)
    e2 = compute_ema(e1, period)
    return 2 * e1 - e2


def compute_tema(series: pd.Series, period: int) -> pd.Series:
    """Triple EMA: 3*EMA − 3*EMA(EMA) + EMA(EMA(EMA))."""
    e1 = compute_ema(series, period)
    e2 = compute_ema(e1, period)
    e3 = compute_ema(e2, period)
    return 3 * e1 - 3 * e2 + e3


def compute_wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average — weights linearly with bar age."""
    weights = np.arange(1, period + 1, dtype=float)
    weights /= weights.sum()
    return series.rolling(period).apply(lambda x: np.dot(x, weights), raw=True)


def compute_hull_ma(series: pd.Series, period: int = 20) -> pd.Series:
    """
    Hull Moving Average: WMA(2*WMA(p/2) - WMA(p), sqrt(p)).
    Smooth + responsive — better than EMA for trend direction.
    """
    half  = max(int(period / 2), 1)
    sqrtp = max(int(np.sqrt(period)), 1)
    wma_half = compute_wma(series, half)
    wma_full = compute_wma(series, period)
    raw      = 2 * wma_half - wma_full
    return compute_wma(raw, sqrtp)


def compute_aroon(
    df: pd.DataFrame,
    period: int = 25,
) -> tuple[pd.Series, pd.Series]:
    """
    Aroon Up / Aroon Down. Returns (aroon_up, aroon_down) as 0–100 series.
    Up    = 100 × (period - bars_since_highest_high) / period
    Down  = 100 × (period - bars_since_lowest_low)   / period
    """
    highs = df["high"]
    lows  = df["low"]
    aroon_up = highs.rolling(period + 1).apply(
        lambda x: (period - (period - np.argmax(x))) / period * 100, raw=True
    )
    aroon_down = lows.rolling(period + 1).apply(
        lambda x: (period - (period - np.argmin(x))) / period * 100, raw=True
    )
    return aroon_up, aroon_down


def compute_ichimoku(
    df: pd.DataFrame,
    tenkan: int = 9,
    kijun:  int = 26,
    senkou: int = 52,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Ichimoku Cloud.
    Returns (tenkan_sen, kijun_sen, senkou_a, senkou_b).
    senkou_a/b are NOT shifted forward — caller can shift if displaying.
    """
    highs = df["high"]
    lows  = df["low"]

    def _mid(period: int) -> pd.Series:
        return (highs.rolling(period).max() + lows.rolling(period).min()) / 2.0

    tenkan_sen = _mid(tenkan)
    kijun_sen  = _mid(kijun)
    senkou_a   = (tenkan_sen + kijun_sen) / 2.0
    senkou_b   = _mid(senkou)
    return tenkan_sen, kijun_sen, senkou_a, senkou_b


def compute_stoch_rsi(
    series: pd.Series,
    rsi_period:   int = 14,
    stoch_period: int = 14,
    k_smooth:     int = 3,
    d_smooth:     int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Stochastic RSI. Returns (%K, %D)."""
    rsi = compute_rsi(series, rsi_period)
    rsi_min = rsi.rolling(stoch_period).min()
    rsi_max = rsi.rolling(stoch_period).max()
    raw_k   = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan) * 100
    k_line  = raw_k.rolling(k_smooth).mean()
    d_line  = k_line.rolling(d_smooth).mean()
    return k_line, d_line


def compute_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Money Flow Index — RSI weighted by price × volume."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    mf      = typical * df["volume"]
    delta   = typical.diff()
    pos_mf  = mf.where(delta > 0, 0.0).rolling(period).sum()
    neg_mf  = mf.where(delta < 0, 0.0).rolling(period).sum()
    mfr     = pos_mf / neg_mf.replace(0, np.nan)
    return 100 - (100 / (1 + mfr))


def compute_cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    sma     = typical.rolling(period).mean()
    # Mean absolute deviation around the SMA
    mad     = typical.rolling(period).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    return (typical - sma) / (0.015 * mad.replace(0, np.nan))


def compute_psar(
    df: pd.DataFrame,
    af_start: float = 0.02,
    af_step:  float = 0.02,
    af_max:   float = 0.20,
) -> tuple[pd.Series, pd.Series]:
    """
    Parabolic SAR.
    Returns (psar_value, bullish_flag). bullish_flag = True when in long mode.
    """
    n = len(df)
    if n == 0:
        return pd.Series(dtype=float), pd.Series(dtype=bool)

    h = df["high"].values
    l = df["low"].values

    psar    = np.zeros(n)
    bullish = np.zeros(n, dtype=bool)

    # Initialize: assume first bar is bullish
    long_mode = True
    af        = af_start
    ep        = h[0]
    psar[0]   = l[0]
    bullish[0] = True

    for i in range(1, n):
        prior = psar[i - 1]
        if long_mode:
            psar[i] = prior + af * (ep - prior)
            psar[i] = min(psar[i], l[i - 1], l[max(i - 2, 0)])
            if l[i] < psar[i]:
                # flip to short
                long_mode = False
                psar[i]   = ep
                ep        = l[i]
                af        = af_start
            else:
                if h[i] > ep:
                    ep = h[i]
                    af = min(af + af_step, af_max)
        else:
            psar[i] = prior + af * (ep - prior)
            psar[i] = max(psar[i], h[i - 1], h[max(i - 2, 0)])
            if h[i] > psar[i]:
                long_mode = True
                psar[i]   = ep
                ep        = h[i]
                af        = af_start
            else:
                if l[i] < ep:
                    ep = l[i]
                    af = min(af + af_step, af_max)
        bullish[i] = long_mode

    return (
        pd.Series(psar,    index=df.index),
        pd.Series(bullish, index=df.index),
    )


def compute_slope(series: pd.Series, window: int = 5) -> float:
    """
    Linear regression slope of the last `window` points.
    Returns scalar (slope per bar). NaN if insufficient data.
    Used for OBV slope, ATR slope, RSI slope, etc.
    """
    s = series.dropna()
    if len(s) < window:
        return np.nan
    y = s.iloc[-window:].values.astype(float)
    x = np.arange(window, dtype=float)
    return float(np.polyfit(x, y, 1)[0])


def compute_slope_series(series: pd.Series, window: int = 5) -> pd.Series:
    """
    Rolling linear regression slope as a series.
    Slow but correct — use compute_slope(series, window) for one-shot.
    """
    def _fit(arr: np.ndarray) -> float:
        x = np.arange(len(arr), dtype=float)
        return float(np.polyfit(x, arr, 1)[0])

    return series.rolling(window).apply(_fit, raw=True)


def find_pivots(
    series: pd.Series,
    left:  int = 3,
    right: int = 3,
) -> tuple[list[int], list[int]]:
    """
    Identify swing-high and swing-low pivot indices.
    A bar is a pivot HIGH if it is strictly greater than `left` bars before
    and `right` bars after; pivot LOW is the symmetric definition.
    The `right` bars after means pivots can only be confirmed `right` bars later.

    Returns (high_indices, low_indices) — positions in the input series.
    """
    vals = series.values
    n    = len(vals)
    highs: list[int] = []
    lows:  list[int] = []
    for i in range(left, n - right):
        if np.isnan(vals[i]):
            continue
        window_left  = vals[i - left:i]
        window_right = vals[i + 1:i + 1 + right]
        if np.isnan(window_left).any() or np.isnan(window_right).any():
            continue
        v = vals[i]
        if v > window_left.max() and v > window_right.max():
            highs.append(i)
        if v < window_left.min() and v < window_right.min():
            lows.append(i)
    return highs, lows
