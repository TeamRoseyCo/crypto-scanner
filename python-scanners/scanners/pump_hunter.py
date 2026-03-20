"""
================================================================================
PUMP HUNTER v1.0 - Pre-Pump Detection System
================================================================================
Designed to catch tokens in the "loading zone" before explosive moves.

Core Philosophy:
- Pumps don't happen randomly - there are ALWAYS early warning signs
- Smart money accumulates BEFORE retail notices
- Volume leads price - always
- Compression precedes expansion - always

Detection Layers:
1. VOLUME VELOCITY    - Acceleration in volume (not just increase)
2. STEALTH ACCUMULATION - OBV rising while price flat (smart money loading)
3. COMPRESSION SQUEEZE - Bollinger squeeze about to release
4. MOMENTUM IGNITION  - Early RSI thrust from oversold
5. WHALE FOOTPRINT    - Abnormal large candles in accumulation
6. BREAKOUT PROXIMITY - Price coiling near resistance
7. RELATIVE EXPLOSION - RS vs BTC accelerating

Each layer adds conviction. 5+ layers = HIGH PROBABILITY setup.
================================================================================
"""

import os
import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime
from pathlib import Path

# Resolve output directory relative to this script's location
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT_PH = _SCRIPT_DIR.parent.parent
_OUTPUT_DIR = _SCRIPT_DIR / "../../outputs/scanner-results"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

CONFIG = {
    # Scan parameters
    'MIN_MARKET_CAP_RANK': 50,
    'MAX_MARKET_CAP_RANK': 500,
    'MIN_24H_VOLUME_USD': 300_000,
    'MIN_PRICE': 0.00001,

    # Exclusions (stablecoins, wrapped tokens, LSTs)
    'EXCLUDE_SYMBOLS': [
        'USDT', 'USDC', 'DAI', 'BUSD', 'TUSD', 'USDD', 'FDUSD', 'PYUSD',
        'USDE', 'SUSDE', 'BFUSD', 'RLUSD', 'USDG', 'USD0', 'GHO', 'USDAI',
        'WBTC', 'WETH', 'STETH', 'RETH', 'CBETH', 'PAXG', 'XAUT', 'TBTC',
        'WBNB', 'JITOSOL', 'MSOL', 'BNSOL', 'STABLE', 'EURC',
    ],

    # Detection thresholds
    'VOLUME_VELOCITY_THRESHOLD': 1.5,      # Volume acceleration multiplier
    'STEALTH_ACCUM_THRESHOLD': 0.02,       # OBV slope while price flat
    'SQUEEZE_BB_WIDTH': 0.04,              # BB width for squeeze (<4%)
    'RSI_IGNITION_ZONE': (35, 60),         # Expanded: most pumps start RSI 40-60, not 25-40
    'WHALE_CANDLE_MULT': 2.5,              # Candle size vs average
    'BREAKOUT_PROXIMITY': 0.95,            # % of recent high
    'RS_ACCELERATION_MIN': 0.02,           # RS vs BTC acceleration

    # New pre-trend signal parameters
    'DIV_WINDOW': 20,                      # Bars to scan for RSI bullish divergence
    'DIV_PRICE_GAP': 0.98,                 # Price low-2 must be <= this × price low-1
    'DIV_RSI_GAP': 3.0,                    # RSI gap to confirm divergence
    'HL_WINDOW': 20,                       # Bars to scan for higher-lows structure
    'SELL_VOL_REDUCTION': 0.80,            # Red-candle vol reduction to confirm exhaustion

    # Macro guard
    'REQUIRE_BTC_HEALTHY': True,           # Skip scan if BTC in bear regime
    'BTC_MIN_7D_CHANGE': -7.0,             # BTC 7d threshold for "healthy" market
    'MIN_ABS_24H_PCT': 0.3,               # Skip flatliners before any OHLCV call

    # Scoring
    'MIN_PUMP_SCORE': 5,                   # Minimum layers (raised: now 11 total signals)
}

_CG_API_KEY  = os.environ.get("CG_API_KEY", "")
_CG_DEMO_KEY = os.environ.get("CG_DEMO_KEY", "")
COINGECKO_API = (
    "https://pro-api.coingecko.com/api/v3" if _CG_API_KEY
    else "https://api.coingecko.com/api/v3"
)
if _CG_API_KEY:
    _CG_HEADERS = {"x-cg-pro-api-key": _CG_API_KEY}
elif _CG_DEMO_KEY:
    _CG_HEADERS = {"x-cg-demo-api-key": _CG_DEMO_KEY}
else:
    _CG_HEADERS = {}
_API_DELAY = 1.2 if _CG_API_KEY else 2.0 if _CG_DEMO_KEY else 6.5

CACHE_DIR = _PROJECT_ROOT_PH / "cache" / "shared_ohlcv"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv(coin_id, days=30):
    """Fetch OHLCV data with caching."""
    cache_file = CACHE_DIR / f"{coin_id}_{days}d.csv"

    # Use cache if fresh (< 1 hour old)
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if age < 3600:  # 1 hour
            try:
                return pd.read_csv(cache_file, index_col=0, parse_dates=True)
            except:
                pass

    # Fetch OHLC
    url = f"{COINGECKO_API}/coins/{coin_id}/ohlc"
    params = {'vs_currency': 'usd', 'days': days}

    try:
        r = requests.get(url, headers=_CG_HEADERS, params=params, timeout=15)
        if r.status_code == 429:
            wait = 30 if _CG_API_KEY else 60
            time.sleep(wait)
            r = requests.get(url, headers=_CG_HEADERS, params=params, timeout=15)
        if r.status_code != 200:
            return None

        data = r.json()
        if not isinstance(data, list) or len(data) < 20:
            return None

        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)

        # Fetch volume separately
        time.sleep(0.2 if _CG_API_KEY else 0.5)
        vol_url = f"{COINGECKO_API}/coins/{coin_id}/market_chart"
        vol_r = requests.get(vol_url, headers=_CG_HEADERS, params={'vs_currency': 'usd', 'days': days}, timeout=15)

        if vol_r.status_code == 200:
            vol_data = vol_r.json()
            volumes = vol_data.get('total_volumes', [])
            if volumes:
                vol_df = pd.DataFrame(volumes, columns=['ts', 'volume'])
                vol_df['ts'] = pd.to_datetime(vol_df['ts'], unit='ms')
                vol_df.set_index('ts', inplace=True)
                # Resample to match OHLC
                vol_daily = vol_df.resample('4h').mean()
                df = df.join(vol_daily, how='left')
                df['volume'] = df['volume'].ffill()

        if 'volume' not in df.columns:
            df['volume'] = np.nan

        df.to_csv(cache_file)
        return df

    except Exception as e:
        return None


def fetch_btc_data(days=30):
    """Fetch BTC data for relative strength."""
    return fetch_ohlcv('bitcoin', days)


def fetch_market_coins():
    """Fetch top coins by market cap."""
    url = f"{COINGECKO_API}/coins/markets"
    coins = []

    for page in range(1, 5):
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': 250,
            'page': page,
            'sparkline': False
        }
        try:
            r = requests.get(url, headers=_CG_HEADERS, params=params, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if not data:
                    break
                coins.extend(data)
                time.sleep(1)
        except:
            break

    return coins


def pre_filter(coins: list, btc_close, min_layers: int = 3) -> set:
    """
    Pipeline Stage 1a: screen all market coins, populate shared OHLCV cache.
    Returns set of coin_ids with >= min_layers pump signals detected.
    Lower threshold (3 vs 5) casts a wider net for the pipeline.
    """
    candidates = set()
    total = len(coins)
    scanned = 0
    for coin in coins:
        symbol     = coin['symbol'].upper()
        coin_id    = coin['id']
        rank       = coin.get('market_cap_rank', 999)
        price      = coin.get('current_price', 0)
        volume_24h = coin.get('total_volume', 0)
        change_24h = coin.get('price_change_percentage_24h') or 0.0

        if symbol in CONFIG['EXCLUDE_SYMBOLS']:                     continue
        if not (CONFIG['MIN_MARKET_CAP_RANK'] <= rank <= CONFIG['MAX_MARKET_CAP_RANK']): continue
        if volume_24h < CONFIG['MIN_24H_VOLUME_USD']:               continue
        if price < CONFIG['MIN_PRICE']:                             continue
        if abs(change_24h) < CONFIG['MIN_ABS_24H_PCT']:             continue

        scanned += 1
        print(f"  [{scanned}] {symbol} (#{rank})...", end=" ", flush=True)

        ohlcv = fetch_ohlcv(coin_id, 30)
        if ohlcv is None or len(ohlcv) < 20:
            print("no data")
            time.sleep(_API_DELAY)
            continue

        close  = ohlcv['close']
        high   = ohlcv['high']
        low    = ohlcv['low']
        opens  = ohlcv['open'] if 'open' in ohlcv.columns else close
        volume = ohlcv.get('volume', pd.Series([np.nan]*len(close), index=close.index))
        if isinstance(volume, pd.DataFrame):
            volume = volume.iloc[:, 0]

        detections = {
            'volume_velocity':    detect_volume_velocity(volume),
            'stealth_accumulation': detect_stealth_accumulation(close, volume),
            'squeeze':            detect_squeeze(close),
            'rsi_ignition':       detect_rsi_ignition(close),
            'whale_candles':      detect_whale_candles(opens, high, low, close, volume),
            'breakout_proximity': detect_breakout_proximity(close),
            'rsi_divergence':     detect_rsi_divergence(close, window=CONFIG['DIV_WINDOW'],
                                      price_gap=CONFIG['DIV_PRICE_GAP'], rsi_gap=CONFIG['DIV_RSI_GAP']),
            'macd_turning':       detect_macd_turning(close),
            'higher_lows':        detect_higher_lows(low, window=CONFIG['HL_WINDOW']),
            'declining_sell_vol': detect_declining_sell_volume(ohlcv, window=10,
                                      reduction=CONFIG['SELL_VOL_REDUCTION']),
            'rs_explosion':       detect_rs_explosion(close, btc_close) if btc_close is not None
                                  else (False, 0.0),
        }
        active = sum(1 for d, _ in detections.values() if d)
        if active >= min_layers:
            candidates.add(coin_id)
            print(f"CANDIDATE ({active} signals)")
        else:
            print(f"skip ({active} signals)")

        time.sleep(_API_DELAY)

    return candidates


# ═══════════════════════════════════════════════════════════════════════════════
# DETECTION ALGORITHMS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_volume_velocity(volume, window=5):
    """
    LAYER 1: Volume Velocity (Acceleration)

    Not just "volume is high" but "volume is ACCELERATING"
    This catches the early phase of accumulation before it's obvious.

    Returns: (is_accelerating, velocity_score)
    """
    if volume.isna().all() or len(volume) < window * 2:
        return False, 0.0

    vol = volume.dropna()
    if len(vol) < window * 2:
        return False, 0.0

    # Calculate volume momentum (first derivative)
    vol_ma_short = vol.rolling(window).mean()
    vol_ma_long = vol.rolling(window * 2).mean()

    # Velocity = rate of change in volume
    vol_velocity = (vol_ma_short.iloc[-1] / vol_ma_long.iloc[-1]) if vol_ma_long.iloc[-1] > 0 else 0

    # Acceleration = change in velocity
    vol_velocity_prev = (vol_ma_short.iloc[-3] / vol_ma_long.iloc[-3]) if vol_ma_long.iloc[-3] > 0 else 0
    vol_acceleration = vol_velocity - vol_velocity_prev

    is_accelerating = vol_velocity > CONFIG['VOLUME_VELOCITY_THRESHOLD'] and vol_acceleration > 0

    return is_accelerating, round(vol_velocity, 2)


def detect_stealth_accumulation(close, volume, window=10):
    """
    LAYER 2: Stealth Accumulation

    OBV (On-Balance Volume) rising while price is flat = smart money loading
    This is THE classic "they know something we don't" signal.

    Returns: (is_accumulating, divergence_strength)
    """
    if volume.isna().all() or len(close) < window:
        return False, 0.0

    # Calculate OBV
    obv = pd.Series(index=close.index, dtype=float)
    obv.iloc[0] = 0

    for i in range(1, len(close)):
        if close.iloc[i] > close.iloc[i-1]:
            obv.iloc[i] = obv.iloc[i-1] + volume.iloc[i]
        elif close.iloc[i] < close.iloc[i-1]:
            obv.iloc[i] = obv.iloc[i-1] - volume.iloc[i]
        else:
            obv.iloc[i] = obv.iloc[i-1]

    # Price slope (should be flat or slightly down)
    price_change = (close.iloc[-1] / close.iloc[-window]) - 1

    # OBV slope (should be rising)
    obv_change = (obv.iloc[-1] - obv.iloc[-window]) / (abs(obv.iloc[-window]) + 1)

    # Stealth accumulation: OBV rising, price flat
    is_stealth = obv_change > CONFIG['STEALTH_ACCUM_THRESHOLD'] and price_change < 0.05
    divergence = obv_change - price_change

    return is_stealth, round(divergence, 3)


def detect_squeeze(close, window=20):
    """
    LAYER 3: Bollinger Band Squeeze

    Tight consolidation (low BB width) = energy building
    When it releases, move is explosive. This catches the COIL.

    Returns: (is_squeezed, bb_width)
    """
    if len(close) < window:
        return False, 0.0

    sma = close.rolling(window).mean()
    std = close.rolling(window).std()

    bb_width = (std.iloc[-1] / sma.iloc[-1]) if sma.iloc[-1] > 0 else 0

    # Historical BB width for context
    bb_width_avg = (std / sma).rolling(window).mean().iloc[-1]

    # Squeeze = current width below threshold AND below average
    is_squeezed = bb_width < CONFIG['SQUEEZE_BB_WIDTH'] and bb_width < bb_width_avg * 0.8

    return is_squeezed, round(bb_width * 100, 2)


def detect_rsi_ignition(close, window=14):
    """
    LAYER 4: RSI Ignition

    RSI leaving oversold zone with momentum = early reversal
    Catching the TURN, not chasing the move.

    Returns: (is_igniting, rsi_value)
    """
    if len(close) < window + 5:
        return False, 0.0

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    rsi_now = rsi.iloc[-1]
    rsi_prev = rsi.iloc[-3]

    if np.isnan(rsi_now) or np.isnan(rsi_prev):
        return False, 0.0

    # Ignition = RSI was below lower bound, now rising into zone
    low, high = CONFIG['RSI_IGNITION_ZONE']
    is_igniting = rsi_prev < low and rsi_now >= low and rsi_now < high

    # Also detect RSI momentum (rising from oversold)
    rsi_momentum = rsi_now > rsi_prev and rsi_now < 50

    return (is_igniting or rsi_momentum), round(rsi_now, 1)


def detect_whale_candles(open_, high, low, close, volume, window=20):
    """
    LAYER 5: Whale Footprint (direction-aware)

    Large BULLISH candles (close > open, closing in upper 30% of range) with high
    volume = whale accumulation, not distribution. Large red candles are ignored
    because they signal selling pressure, not buying.

    Returns: (has_whale_activity, whale_score)
    """
    if len(close) < window:
        return False, 0.0

    candle_range = high - low
    avg_range = candle_range.rolling(window).mean()

    # Candle position: 1.0 = closed at top of range, 0.0 = closed at bottom
    candle_pos = (close - low) / candle_range.replace(0, np.nan)

    # Bullish: closed above open AND in the upper 30% of the candle range
    is_bullish = (close > open_) & (candle_pos >= 0.70)

    # Large bullish candles in the last 5 bars
    large_bull = (
        (candle_range.iloc[-5:] > avg_range.iloc[-5:] * CONFIG['WHALE_CANDLE_MULT'])
        & is_bullish.iloc[-5:]
    ).sum()

    # High-volume bullish candles in the last 5 bars
    if not volume.isna().all():
        vol_avg = volume.rolling(window).mean()
        high_vol_bull = (
            (volume.iloc[-5:] > vol_avg.iloc[-5:] * 1.5)
            & is_bullish.iloc[-5:]
        ).sum()
        whale_score = int(large_bull) + int(high_vol_bull)
    else:
        whale_score = int(large_bull)

    return whale_score >= 2, whale_score


def detect_breakout_proximity(close, window=14):
    """
    LAYER 6: Breakout Proximity

    Price coiling near recent high = ready to break
    The closer to resistance with compression, the higher probability.

    Returns: (near_breakout, proximity_pct)
    """
    if len(close) < window:
        return False, 0.0

    recent_high = close.rolling(window).max().iloc[-1]
    current = close.iloc[-1]

    proximity = current / recent_high if recent_high > 0 else 0

    near_breakout = proximity >= CONFIG['BREAKOUT_PROXIMITY']

    return near_breakout, round(proximity * 100, 1)


def detect_rs_explosion(token_close, btc_close, window=7):
    """
    LAYER 7: Relative Strength Explosion

    Token starting to outperform BTC with acceleration = alpha emerging
    Smart money rotating in.

    Returns: (rs_exploding, rs_acceleration)
    """
    if len(token_close) < window * 2 or len(btc_close) < window * 2:
        return False, 0.0

    # Current RS
    token_ret = (token_close.iloc[-1] / token_close.iloc[-window]) - 1
    btc_ret = (btc_close.iloc[-1] / btc_close.iloc[-window]) - 1
    rs_now = token_ret - btc_ret

    # Previous RS
    token_ret_prev = (token_close.iloc[-window] / token_close.iloc[-window*2]) - 1
    btc_ret_prev = (btc_close.iloc[-window] / btc_close.iloc[-window*2]) - 1
    rs_prev = token_ret_prev - btc_ret_prev

    # Acceleration = change in RS
    rs_acceleration = rs_now - rs_prev

    is_exploding = rs_acceleration > CONFIG['RS_ACCELERATION_MIN'] and rs_now > 0

    return is_exploding, round(rs_acceleration * 100, 2)


# ─── Pre-trend detection helpers ─────────────────────────────────────────────

def _rsi_series(closes, window=14):
    """Compute full RSI series."""
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def detect_rsi_divergence(close, window=20, rsi_window=14, price_gap=0.98, rsi_gap=3.0):
    """
    LAYER 8: RSI Bullish Divergence

    Price makes a lower low while RSI makes a higher low = selling exhaustion.
    Fires BEFORE the reversal candle, not after. Classic "hidden strength" signal.

    Returns: (has_divergence, rsi_delta)
    """
    if len(close) < window + rsi_window:
        return False, 0.0

    rsi = _rsi_series(close, rsi_window)
    recent_close = close.iloc[-window:]
    recent_rsi = rsi.iloc[-window:]

    if recent_rsi.isna().any():
        return False, 0.0

    # Find local lows in price
    lows_idx = [
        i for i in range(1, len(recent_close) - 1)
        if recent_close.iloc[i] < recent_close.iloc[i - 1]
        and recent_close.iloc[i] < recent_close.iloc[i + 1]
    ]

    if len(lows_idx) < 2:
        return False, 0.0

    i1, i2 = lows_idx[-2], lows_idx[-1]
    p1, p2 = float(recent_close.iloc[i1]), float(recent_close.iloc[i2])
    rsi1, rsi2 = float(recent_rsi.iloc[i1]), float(recent_rsi.iloc[i2])

    if np.isnan(rsi1) or np.isnan(rsi2):
        return False, 0.0

    price_lower = p2 <= p1 * price_gap   # price made a lower low
    rsi_higher = rsi2 >= rsi1 + rsi_gap   # RSI made a higher low

    return bool(price_lower and rsi_higher), round(rsi2 - rsi1, 1)


def detect_macd_turning(close, fast=8, slow=17, signal=9):
    """
    LAYER 9: MACD Histogram Turning

    Histogram still negative but rising for 2+ consecutive bars = momentum shifting
    before the golden cross. Fires BEFORE the crossover, not after.

    Returns: (is_turning, histogram_delta)
    """
    if len(close) < slow + signal:
        return False, 0.0

    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_f - ema_s
    hist = macd_line - macd_line.ewm(span=signal, adjust=False).mean()

    if hist.isna().iloc[-3:].any():
        return False, 0.0

    h0, h1, h2 = float(hist.iloc[-1]), float(hist.iloc[-2]), float(hist.iloc[-3])

    # Still below zero but improving for two consecutive bars
    is_turning = (h0 < 0) and (h0 > h1) and (h1 > h2)

    return bool(is_turning), round(h0 - h2, 6)


def detect_higher_lows(low, window=20):
    """
    LAYER 10: Higher Lows Structure

    Ascending swing lows = demand floor rising, buyers stepping in at higher prices.
    Classic accumulation footprint before a breakout.

    Returns: (has_higher_lows, pct_rise_of_lows)
    """
    if len(low) < window:
        return False, 0.0

    recent = low.iloc[-window:]

    swing_lows = [
        float(recent.iloc[i])
        for i in range(1, len(recent) - 1)
        if recent.iloc[i] < recent.iloc[i - 1] and recent.iloc[i] < recent.iloc[i + 1]
    ]

    if len(swing_lows) < 2:
        return False, 0.0

    ascending = all(swing_lows[i] > swing_lows[i - 1] for i in range(1, len(swing_lows)))

    if ascending:
        pct_rise = round((swing_lows[-1] / swing_lows[0] - 1) * 100, 2)
        return True, pct_rise

    return False, 0.0


def detect_declining_sell_volume(ohlcv_df, window=10, reduction=0.80):
    """
    LAYER 11: Declining Sell Volume

    Volume on red candles shrinking over recent bars = sellers exhausted, supply
    drying up. Classic bottom-formation and accumulation pattern.

    Returns: (is_declining, late_vs_early_vol_ratio)
    """
    if len(ohlcv_df) < window or 'volume' not in ohlcv_df.columns:
        return False, 0.0

    if ohlcv_df['volume'].isna().all():
        return False, 0.0

    recent = ohlcv_df.iloc[-window:]
    red_candles = recent[recent['close'] < recent['open']]

    if len(red_candles) < 3:
        return False, 0.0

    red_vols = red_candles['volume'].dropna().values

    if len(red_vols) < 3:
        return False, 0.0

    mid = len(red_vols) // 2
    early_avg = red_vols[:mid].mean()
    late_avg = red_vols[mid:].mean()

    if early_avg <= 0:
        return False, 0.0

    ratio = late_avg / early_avg
    return bool(ratio <= reduction), round(float(ratio), 2)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_pump_score(detections):
    """
    Calculate composite pump probability score.
    Each detection layer adds conviction. 11 signals total.
    """
    weights = {
        'stealth_accumulation': 2.0,   # OBV rising while price flat — smart money
        'rsi_divergence':       2.0,   # Price lower-low + RSI higher-low — fires before reversal
        'macd_turning':         2.0,   # Hist rising below zero — fires before crossover
        'rs_explosion':         1.5,   # Token outperforming BTC with acceleration
        'volume_velocity':      1.5,   # Volume accelerating (not just elevated)
        'whale_candles':        1.5,   # Bullish large candles — institutional buying
        'squeeze':              1.5,   # BB width compressed — coiled spring
        'higher_lows':          1.5,   # Ascending swing lows — demand floor rising
        'declining_sell_vol':   1.5,   # Red candle volume shrinking — sellers exhausted
        'rsi_ignition':         1.0,   # RSI entering zone from below — timing
        'breakout_proximity':   1.0,   # Near recent high — proximity to breakout
    }

    score = sum(weights.get(layer, 1.0) for layer, (detected, _) in detections.items() if detected)
    max_score = sum(weights.values())  # 18.0
    normalized = (score / max_score) * 100

    return round(normalized, 1), round(score, 1)


def classify_setup(pump_score, detections):
    """Classify the setup quality (11-signal scale)."""
    active_layers = sum(1 for d, _ in detections.values() if d)

    if active_layers >= 7:
        return "PRIME"
    elif active_layers >= 5:
        return "HIGH"
    elif active_layers >= 4:
        return "MEDIUM"
    else:
        return "LOW"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

def scan_for_pumps():
    """Main scanning function."""

    print("\n" + "=" * 80)
    print("  PUMP HUNTER v2.0 - Pre-Pump Detection System")
    print("  Scanning for tokens in the loading zone...")
    print("=" * 80 + "\n")

    # Fetch BTC data for relative strength and regime filter
    print("Fetching BTC data...")
    btc_data = fetch_btc_data(30)
    btc_close = btc_data['close'] if btc_data is not None else None

    # BTC health check — abort in extreme bear regimes (saves 45+ min of wasted scanning)
    if CONFIG['REQUIRE_BTC_HEALTHY'] and btc_data is not None:
        # 42 × 4h bars ≈ 7 calendar days
        btc_7d_chg = ((btc_data['close'].iloc[-1] / btc_data['close'].iloc[-42]) - 1) * 100
        if btc_7d_chg < CONFIG['BTC_MIN_7D_CHANGE']:
            print(f"\n⚠️  BTC 7d change = {btc_7d_chg:.1f}% — bear regime detected.")
            print("   Scan aborted. Re-run when conditions improve.\n")
            return []
        print(f"BTC 7d change: {btc_7d_chg:+.1f}% ✓")

    # Fetch market coins
    print("Fetching market data...")
    coins = fetch_market_coins()
    print(f"Found {len(coins)} coins\n")

    results = []

    for i, coin in enumerate(coins):
        symbol = coin['symbol'].upper()
        coin_id = coin['id']
        rank = coin.get('market_cap_rank', 999)
        price = coin.get('current_price', 0)
        volume_24h = coin.get('total_volume', 0)

        # Apply filters
        if symbol in CONFIG['EXCLUDE_SYMBOLS']:
            continue
        if not (CONFIG['MIN_MARKET_CAP_RANK'] <= rank <= CONFIG['MAX_MARKET_CAP_RANK']):
            continue
        if volume_24h < CONFIG['MIN_24H_VOLUME_USD']:
            continue
        if price < CONFIG['MIN_PRICE']:
            continue

        # Skip flatliners — no meaningful 24h move saves ~6.5s API call per coin
        change_24h = coin.get('price_change_percentage_24h') or 0.0
        if abs(change_24h) < CONFIG['MIN_ABS_24H_PCT']:
            continue

        print(f"[{i+1}] Analyzing {symbol} (#{rank})...", end=" ")

        # Fetch OHLCV
        ohlcv = fetch_ohlcv(coin_id, 30)
        if ohlcv is None or len(ohlcv) < 20:
            print("insufficient data")
            continue

        close = ohlcv['close']
        high = ohlcv['high']
        low = ohlcv['low']
        opens = ohlcv['open'] if 'open' in ohlcv.columns else close
        volume = ohlcv.get('volume', pd.Series([np.nan] * len(close), index=close.index))
        if isinstance(volume, pd.DataFrame):
            volume = volume.iloc[:, 0]

        # ATR → stop / TP levels for trade plan
        atr_series = (high - low).rolling(14).mean()
        atr_pct = float(atr_series.iloc[-1] / close.iloc[-1]) if close.iloc[-1] > 0 else 0.03
        stop_pct = max(0.05, min(0.15, 1.5 * atr_pct))
        stop_price = price * (1 - stop_pct)
        tp1 = price * (1 + stop_pct * 2)   # R:R 1:2
        tp2 = price * (1 + stop_pct * 3)   # R:R 1:3
        tp3 = price * (1 + stop_pct * 5)   # R:R 1:5

        # Run all detection layers
        detections = {}

        detections['volume_velocity']    = detect_volume_velocity(volume)
        detections['stealth_accumulation'] = detect_stealth_accumulation(close, volume)
        detections['squeeze']            = detect_squeeze(close)
        detections['rsi_ignition']       = detect_rsi_ignition(close)
        detections['whale_candles']      = detect_whale_candles(opens, high, low, close, volume)
        detections['breakout_proximity'] = detect_breakout_proximity(close)
        detections['rsi_divergence']     = detect_rsi_divergence(
            close, window=CONFIG['DIV_WINDOW'],
            price_gap=CONFIG['DIV_PRICE_GAP'], rsi_gap=CONFIG['DIV_RSI_GAP']
        )
        detections['macd_turning']       = detect_macd_turning(close)
        detections['higher_lows']        = detect_higher_lows(low, window=CONFIG['HL_WINDOW'])
        detections['declining_sell_vol'] = detect_declining_sell_volume(
            ohlcv, window=10, reduction=CONFIG['SELL_VOL_REDUCTION']
        )
        if btc_close is not None:
            detections['rs_explosion']   = detect_rs_explosion(close, btc_close)
        else:
            detections['rs_explosion']   = (False, 0.0)

        # Calculate score
        pump_score, raw_score = calculate_pump_score(detections)
        setup_class = classify_setup(pump_score, detections)

        active_layers = sum(1 for d, _ in detections.values() if d)

        if active_layers >= CONFIG['MIN_PUMP_SCORE']:
            print(f"DETECTED! Score: {pump_score} ({setup_class})")

            results.append({
                'SYMBOL': symbol,
                'RANK': rank,
                'PRICE': price,
                'PUMP_SCORE': pump_score,
                'SETUP': setup_class,
                'LAYERS': active_layers,
                'STOP_PCT': round(stop_pct * 100, 1),
                'STOP_PRICE': round(stop_price, 8),
                'TP1': round(tp1, 8),
                'TP2': round(tp2, 8),
                'TP3': round(tp3, 8),
                'ATR_PCT': round(atr_pct * 100, 2),
                'VOL_VELOCITY': detections['volume_velocity'][1],
                'STEALTH_ACCUM': detections['stealth_accumulation'][1],
                'SQUEEZE': detections['squeeze'][1],
                'RSI': detections['rsi_ignition'][1],
                'WHALE_SCORE': detections['whale_candles'][1],
                'BREAKOUT_PROX': detections['breakout_proximity'][1],
                'RS_ACCEL': detections['rs_explosion'][1],
                'RSI_DIV_DELTA': detections['rsi_divergence'][1],
                'MACD_DELTA': detections['macd_turning'][1],
                'HL_PCT': detections['higher_lows'][1],
                'SELL_VOL_RATIO': detections['declining_sell_vol'][1],
                'SIGNALS': ' | '.join(k.upper() for k, (d, _) in detections.items() if d),
            })
        else:
            print(f"skip ({active_layers} layers)")

        time.sleep(_API_DELAY)  # Rate limiting

    return results


def print_results(results):
    """Print formatted results."""
    if not results:
        print("\n" + "=" * 80)
        print("  No pump setups detected at this time.")
        print("  This is normal - quality setups are rare.")
        print("=" * 80)
        return

    # Sort by pump score
    df = pd.DataFrame(results).sort_values('PUMP_SCORE', ascending=False)

    print("\n" + "=" * 80)
    print("  PUMP CANDIDATES DETECTED")
    print("=" * 80)

    for _, row in df.iterrows():
        symbol = row['SYMBOL']
        score = row['PUMP_SCORE']
        setup = row['SETUP']
        layers = int(row['LAYERS'])
        signals = row['SIGNALS']
        p = row['PRICE']

        icon = "🔥" if setup == "PRIME" else "⚡" if setup == "HIGH" else "💡"

        print(f"\n{icon}  {symbol}  (#{int(row['RANK'])})")
        print(f"     Pump Score  : {score:.1f} / 100  |  Setup: {setup}  |  Layers: {layers}/11")
        print(f"     Signals     : {signals}")
        print(f"")
        print(f"     ┌─ TRADE PLAN {'─' * 48}")
        print(f"     │  Entry price  : ${p:.8g}  ← buy at market")
        print(f"     │  STOP LOSS    : ${row['STOP_PRICE']:.8g}  (-{row['STOP_PCT']:.1f}%)  ← EXIT HARD")
        print(f"     │")
        print(f"     │  TP1  ${row['TP1']:.8g}  (R:R 1:2)")
        print(f"     │  TP2  ${row['TP2']:.8g}  (R:R 1:3)")
        print(f"     │  TP3  ${row['TP3']:.8g}  (R:R 1:5)")
        print(f"     └{'─' * 57}")
        print(f"")
        print(f"     ── Indicators ─────────────────────────────────────────────")
        print(f"     RSI={row['RSI']}  ATR={row['ATR_PCT']}%  BB Squeeze={row['SQUEEZE']}%  RS Accel={row['RS_ACCEL']}%")
        print(f"     Vol Velocity={row['VOL_VELOCITY']}x  Stealth Accum={row['STEALTH_ACCUM']}  Whale={row['WHALE_SCORE']}")
        print(f"     Breakout Prox={row['BREAKOUT_PROX']}%  RSI Div Δ={row['RSI_DIV_DELTA']}  MACD Δ={row['MACD_DELTA']}  Sell Vol Ratio={row['SELL_VOL_RATIO']}")

    print("\n" + "=" * 80)
    print(f"  Total: {len(df)} pump candidates detected")
    print("  Rules: Honor every stop. After TP1 → move stop to breakeven.")
    print("=" * 80)

    # Save to CSV
    output_file = str(_OUTPUT_DIR / "pump_hunter_results.csv")
    df.to_csv(output_file, index=False)
    print(f"\nResults saved to {output_file}")


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════════════════════════════════════════╗
    ║                     PUMP HUNTER v2.0                                      ║
    ║                 Pre-Pump Detection System                                 ║
    ╠═══════════════════════════════════════════════════════════════════════════╣
    ║  Detection Layers (11 signals):                                           ║
    ║   1. Volume Velocity      - Volume accelerating (not just elevated)       ║
    ║   2. Stealth Accumulation - OBV rising while price flat (smart money)     ║
    ║   3. Bollinger Squeeze    - Compression before expansion                  ║
    ║   4. RSI Ignition         - RSI entering zone from below                  ║
    ║   5. Whale Footprint      - Bullish large candles (direction-aware)       ║
    ║   6. Breakout Proximity   - Coiling near recent high                      ║
    ║   7. RS Explosion         - Alpha vs BTC accelerating                     ║
    ║   8. RSI Divergence       - Price lower-low + RSI higher-low (pre-turn)   ║
    ║   9. MACD Turning         - Histogram rising below zero (pre-crossover)   ║
    ║  10. Higher Lows          - Ascending swing lows (demand rising)          ║
    ║  11. Declining Sell Vol   - Red candle volume shrinking (sellers done)    ║
    ╠═══════════════════════════════════════════════════════════════════════════╣
    ║  Scoring: 4+ = MEDIUM | 5-6 = HIGH | 7+ = PRIME                          ║
    ╚═══════════════════════════════════════════════════════════════════════════╝
    """)

    results = scan_for_pumps()
    print_results(results)
