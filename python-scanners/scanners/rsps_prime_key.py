import os
import requests
import pandas as pd
import numpy as np
import time
from pathlib import Path

# Resolve output directory relative to this script's location
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT_PK = _SCRIPT_DIR.parent.parent
_OUTPUT_DIR = _SCRIPT_DIR / "../../outputs/scanner-results"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- 🎯 PRIME KEY CONFIG (ENHANCED) ---
CONFIG = {
    # 🔍 Market filters (tightened for quality)
    'MAX_TOKENS_TO_FETCH': 1000,
    'MIN_24H_VOLUME_USD': 500_000,  # Higher liquidity floor
    'MIN_MARKET_CAP_RANK': 50,      # Skip too-large (less volatile)
    'MAX_MARKET_CAP_RANK': 500,     # Skip too-small (illiquid)
    'MIN_PRICE_CHANGE_7D': -30.0,
    'MAX_PRICE_CHANGE_7D': 80.0,
    'MIN_PRICE': 0.0001,

    # 📊 Indicators
    'RSI_WINDOW': 14,              # 14 periods standard (was 9 — too noisy on 4h data)
    'ATR_WINDOW': 10,
    'MACD_FAST': 8,
    'MACD_SLOW': 17,
    'MACD_SIGNAL': 9,
    'DMI_WINDOW': 10,

    # 🔮 New pre-trend signal parameters
    'DIV_WINDOW': 20,              # Bars to scan for RSI bullish divergence
    'DIV_PRICE_GAP': 0.98,         # Price low-2 must be <= this × price low-1
    'DIV_RSI_GAP': 3.0,            # RSI at 2nd low must be N pts above 1st low
    'HL_WINDOW': 20,               # Bars to scan for higher-lows structure
    'SELL_VOL_REDUCTION': 0.80,    # Recent red-candle vol <= this × earlier red-candle vol

    # 🎯 Thresholds (optimized)
    'MIN_ATR_PCT': 0.025,   # 2.5% min volatility
    'RSI_LOW': 30,          # Not oversold extremes
    'RSI_HIGH': 65,         # Catch before overbought
    'MIN_ADX': 22,          # Stronger trend confirmation

    # 🔥 Explosive filters
    'REQUIRE_ATR_UPTREND': True,
    'ATR_TREND_WINDOW': 3,
    'MIN_ATR_TREND_SLOPE': 0.008,  # Steeper expansion required
    'REQUIRE_VOLUME_SURGE': True,
    'VOLUME_SURGE_MULTIPLIER': 1.5,
    'REQUIRE_RECENT_CANDLE_STRENGTH': True,
    'MIN_CANDLE_STRENGTH': 65,
    'REQUIRE_RSI_REVERSAL': True,
    'RSI_REVERSAL_THRESHOLD': 30,
    'REQUIRE_PRICE_ABOVE_SMA': True,

    # 🚀 NEW: Alpha Filters
    'REQUIRE_RS_VS_BTC': True,
    'MIN_RS_VS_BTC': 0.03,          # 3% outperformance vs BTC
    'RS_WINDOW': 7,

    'REQUIRE_ACCUMULATION': True,
    'MIN_ACCUMULATION_SLOPE': 0.0,  # Positive AD slope

    'REQUIRE_BREAKOUT_QUALITY': False,  # Optional - can be strict
    'BB_WIDTH_TIGHT_THRESHOLD': 0.06,   # <6% = consolidation
    'BB_EXPANSION_MULT': 1.25,

    'MAX_VOLATILITY_RATIO': 0.12,       # Reject if avg daily range >12%

    'REQUIRE_INTRADAY_MOMENTUM': True,
    'INTRADAY_LOOKBACK': 3,

    'MAX_DISTANCE_FROM_HIGH': 0.92,     # Must be <92% of 7d high (avoid FOMO)
    'MIN_DISTANCE_FROM_LOW': 1.05,      # Must be >5% above 7d low

    # 🛡️ Macro guards
    'REQUIRE_BTC_HEALTHY': True,
    'MIN_BTC_7D_CHANGE': -7.0,

    # 📊 Scoring weights (pre-trend signals weighted highest)
    'WEIGHTS': {
        'rsi_divergence':     3.5,   # Leading: price lower-low, RSI higher-low
        'rs_vs_btc':          2.5,   # Alpha: token outperforming BTC
        'macd_turning':       2.5,   # Leading: histogram rising pre-crossover
        'accumulation':       2.0,   # Smart money A/D slope
        'atr_trend':          2.0,   # Volatility expanding
        'higher_lows':        2.0,   # Base-building market structure
        'declining_sell_vol': 1.5,   # Sellers exhausting
        'volume_surge':       1.5,   # Volume velocity
        'breakout_quality':   1.5,   # BB squeeze then expansion
        'intraday_momentum':  1.5,   # Recent candles green
        'rsi_reversal':       1.0,   # RSI leaving oversold
        'macd_cross':         1.0,   # MACD line above signal
        'dmi_bullish':        1.0,   # ADX + DI+ > DI-
        'candle_strength':    1.0,   # Last candle in upper range
        'price_above_sma':    1.0,   # Price above SMA20
    },
}

_CG_API_KEY      = os.environ.get("CG_API_KEY", "")
_CG_DEMO_KEY     = os.environ.get("CG_DEMO_KEY", "")
COINGECKO_API_URL = (
    "https://pro-api.coingecko.com/api/v3" if _CG_API_KEY
    else "https://api.coingecko.com/api/v3"
)
if _CG_API_KEY:
    _CG_HEADERS = {"x-cg-pro-api-key": _CG_API_KEY}
elif _CG_DEMO_KEY:
    _CG_HEADERS = {"x-cg-demo-api-key": _CG_DEMO_KEY}
else:
    _CG_HEADERS = {}
_API_DELAY = 1.2 if _CG_API_KEY else 2.0 if _CG_DEMO_KEY else 6.3

CACHE_DIR = _PROJECT_ROOT_PK / "cache" / "shared_ohlcv"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# --- FETCH BTC 7D CHANGE ---
def get_btc_7d_change():
    try:
        r = requests.get(f"{COINGECKO_API_URL}/coins/bitcoin", headers=_CG_HEADERS, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data['market_data']['price_change_percentage_7d_in_currency']['usd']
    except:
        pass
    return None

# --- OHLC + VOLUME FETCH (same as before) ---
def fetch_ohlc(coin_id, days=60):
    cache_file = CACHE_DIR / f"{coin_id}_{days}d.csv"

    # ── Cache hit: only reuse if younger than 4 hours (was infinite — caused stale data) ──
    if cache_file.exists():
        age_h = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_h < 4.0:
            try:
                df = pd.read_csv(cache_file, index_col=0)
                if len(df) >= 20:
                    return df
            except:
                pass  # corrupt cache — re-fetch

    allowed_days = [1, 7, 14, 30, 90, 180, 365]
    chosen_days = min(allowed_days, key=lambda x: abs(x - days))
    url = f"{COINGECKO_API_URL}/coins/{coin_id}/ohlc"
    params = {'vs_currency': 'usd', 'days': chosen_days}
    for attempt in range(3):
        try:
            r = requests.get(url, headers=_CG_HEADERS, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(30 * (2 ** attempt))
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            if not (isinstance(data, list) and len(data) >= 20):
                return None
            opens  = [row[1] for row in data]
            highs  = [row[2] for row in data]
            lows   = [row[3] for row in data]
            closes = [row[4] for row in data]
            idx    = [pd.to_datetime(row[0], unit='ms') for row in data]
            df = pd.DataFrame(
                {'open': opens, 'close': closes, 'high': highs, 'low': lows},
                index=idx
            )

            # ── Volume: removed interval='daily' which returned ~60 rows vs ~540 OHLC rows
            # Now uses default hourly data and resamples to match OHLC frequency ──
            try:
                mc_url = f"{COINGECKO_API_URL}/coins/{coin_id}/market_chart"
                mc = requests.get(
                    mc_url,
                    headers=_CG_HEADERS,
                    params={'vs_currency': 'usd', 'days': chosen_days},
                    timeout=10
                )
                if mc.status_code == 200:
                    vol_raw = mc.json().get('total_volumes', [])
                    if vol_raw:
                        vol_df = pd.DataFrame(vol_raw, columns=['ts', 'volume'])
                        vol_df['ts'] = pd.to_datetime(vol_df['ts'], unit='ms')
                        vol_df = vol_df.set_index('ts')
                        vol_resampled = vol_df.resample('4h').mean()
                        df = df.join(vol_resampled, how='left')
                        df['volume'] = df['volume'].ffill()
                    else:
                        df['volume'] = np.nan
                else:
                    df['volume'] = np.nan
            except:
                df['volume'] = np.nan

            df.to_csv(cache_file)
            return df
        except:
            time.sleep(5)
    return None

# --- INDICATOR FUNCTIONS (same as before, abbreviated for space) ---
def compute_rsi(prices, window=9):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if len(rsi.dropna()) > 0 else np.nan

def compute_atr_series(high, low, close, window=10):
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(window).mean()

def compute_trend_slope(series, window):
    s = series.dropna()
    if len(s) < window: return np.nan
    last = s.iloc[-window:]
    x = np.arange(len(last))
    try:
        return np.polyfit(x, last.values, 1)[0]
    except:
        return np.nan

def compute_volume_surge(volumes, mult=1.5, window=20):
    if len(volumes) < window: return False
    avg = volumes.iloc[-window:-1].mean()
    return volumes.iloc[-1] > avg * mult

def compute_candle_strength(high, low, close):
    h, l, c = high.iloc[-1], low.iloc[-1], close.iloc[-1]
    rng = h - l
    return ((c - l) / rng) * 100 if rng > 0 else 0

def compute_rsi_reversal(prices, thresh=35):
    rsi = 100 - (100 / (1 + (prices.diff().clip(lower=0).rolling(9).mean() / -prices.diff().clip(upper=0).rolling(9).mean())))
    rsi_now, rsi_prev = rsi.iloc[-1], rsi.iloc[-4]
    return (not np.isnan(rsi_now) and not np.isnan(rsi_prev) and rsi_prev < thresh and rsi_now >= thresh)

def compute_macd(prices):
    ema_fast = prices.ewm(span=8).mean()
    ema_slow = prices.ewm(span=17).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9).mean()
    return macd_line.iloc[-1], signal_line.iloc[-1]

def compute_dmi(high, low, close):
    up_move = high.diff()
    down_move = low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(10).mean()
    plus_di = (pd.Series(plus_dm, index=high.index).rolling(10).mean() / atr) * 100
    minus_di = (pd.Series(minus_dm, index=high.index).rolling(10).mean() / atr) * 100
    adx = ((plus_di - minus_di).abs() / (plus_di + minus_di) * 100).rolling(10).mean()
    return plus_di.iloc[-1], minus_di.iloc[-1], adx.iloc[-1]


# --- 🚀 NEW ALPHA INDICATORS ---

def compute_volatility_ratio(high, low, close, window=5):
    """
    Ratio of intraday range to close price.
    High ratio = illiquid/manipulated, avoid these.
    """
    range_pct = ((high - low) / close).rolling(window).mean()
    return range_pct.iloc[-1] if len(range_pct.dropna()) > 0 else np.nan


def compute_accumulation_distribution(high, low, close, volume):
    """
    Money Flow multiplier - detects smart money accumulation vs distribution.
    Positive AD slope while price flat = stealth accumulation (bullish).
    """
    if not isinstance(volume, pd.Series) or volume.isna().all():
        return np.nan, np.nan

    mf_mult = ((close - low) - (high - close)) / (high - low + 1e-9)
    mf_volume = mf_mult * volume
    ad_line = mf_volume.cumsum()

    # Get slopes for divergence detection
    ad_slope = compute_trend_slope(ad_line, 5)
    price_slope = compute_trend_slope(close, 5)

    return ad_slope, price_slope


def compute_relative_strength_vs_btc(token_closes, btc_closes, window=7):
    """
    Token performance relative to BTC.
    Positive = outperforming BTC (alpha).
    """
    if len(token_closes) < window or len(btc_closes) < window:
        return np.nan

    token_ret = (token_closes.iloc[-1] / token_closes.iloc[-window]) - 1
    btc_ret = (btc_closes.iloc[-1] / btc_closes.iloc[-window]) - 1

    return token_ret - btc_ret


def detect_breakout_quality(high, low, close, window=20):
    """
    Quality breakout = tight consolidation (low BB width) then expansion.
    Avoids random volatility spikes.
    """
    if len(close) < window:
        return False, 0.0

    sma = close.rolling(window).mean()
    std = close.rolling(window).std()
    bb_width = std / sma

    bb_width_now = bb_width.iloc[-1]
    bb_width_prev = bb_width.iloc[-5] if len(bb_width) >= 5 else bb_width.iloc[0]

    if np.isnan(bb_width_now) or np.isnan(bb_width_prev):
        return False, 0.0

    # Good breakout: width was tight, now expanding
    was_tight = bb_width_prev < CONFIG['BB_WIDTH_TIGHT_THRESHOLD']
    is_expanding = bb_width_now > bb_width_prev * CONFIG['BB_EXPANSION_MULT']

    return (was_tight and is_expanding), bb_width_now


def compute_distance_from_extremes(close, window=7):
    """
    Distance from recent high and low.
    Avoid FOMO entries near highs, avoid falling knives near lows.
    """
    if len(close) < window:
        return np.nan, np.nan

    high_window = close.rolling(window).max().iloc[-1]
    low_window = close.rolling(window).min().iloc[-1]
    current = close.iloc[-1]

    dist_from_high = current / high_window if high_window > 0 else np.nan
    dist_from_low = current / low_window if low_window > 0 else np.nan

    return dist_from_high, dist_from_low


def compute_intraday_momentum(close, lookback=3):
    """
    Check if last N candles show consistent momentum (all green or mostly green).
    """
    if len(close) < lookback + 1:
        return False, 0

    changes = close.diff().iloc[-lookback:]
    green_candles = (changes > 0).sum()
    momentum_score = green_candles / lookback

    return momentum_score >= 0.66, momentum_score  # At least 2/3 green


# ─────────────────────────────────────────────────────────────────────────────
# NEW PRE-TREND SIGNAL HELPERS (ported from master_orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

def _rsi_series_pk(closes, window=14):
    """Full RSI series needed for divergence detection."""
    delta = closes.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_rsi_divergence(closes, window=20, rsi_window=14,
                            price_gap=0.98, rsi_gap=3.0):
    """
    Bullish RSI divergence: price makes a lower low while RSI makes a higher
    low — earliest signal that sellers are exhausting before price reverses up.
    """
    if len(closes) < window + rsi_window + 2:
        return False
    rsi_s = _rsi_series_pk(closes, rsi_window)
    c_win = closes.iloc[-window:]
    r_win = rsi_s.iloc[-window:]
    if r_win.isna().sum() > window // 2:
        return False
    mid  = window // 2
    c1, c2 = c_win.iloc[:mid], c_win.iloc[mid:]
    r1, r2 = r_win.iloc[:mid], r_win.iloc[mid:]
    idx1 = int(np.nanargmin(c1.values))
    idx2 = int(np.nanargmin(c2.values))
    p_low1, p_low2 = float(c1.iloc[idx1]), float(c2.iloc[idx2])
    r_low1, r_low2 = float(r1.iloc[idx1]), float(r2.iloc[idx2])
    if any(np.isnan(v) for v in (p_low1, p_low2, r_low1, r_low2)):
        return False
    return p_low2 <= p_low1 * price_gap and r_low2 >= r_low1 + rsi_gap


def compute_macd_turning(closes, fast=8, slow=17, signal=9):
    """
    MACD histogram rising from its trough while still below zero — fires
    2-5 candles before the crossover, earlier than any standard MACD signal.
    """
    ema_f = closes.ewm(span=fast,   adjust=False).mean()
    ema_s = closes.ewm(span=slow,   adjust=False).mean()
    macd  = ema_f - ema_s
    sig   = macd.ewm(span=signal,   adjust=False).mean()
    hist  = (macd - sig).dropna()
    if len(hist) < 4:
        return False
    h_now, h_prev, h_prev2 = float(hist.iloc[-1]), float(hist.iloc[-2]), float(hist.iloc[-3])
    # Negative but rising for at least 2 consecutive bars
    return h_now < 0 and h_now > h_prev > h_prev2


def compute_higher_lows(lows, window=20):
    """
    Last 3 swing lows (local minima) are each higher than the previous —
    market structure shifting from downtrend to base, buyers absorbing supply.
    """
    if len(lows) < window:
        return False
    recent = lows.iloc[-window:]
    swings = [
        float(recent.iloc[i])
        for i in range(1, len(recent) - 1)
        if float(recent.iloc[i]) < float(recent.iloc[i - 1])
        and float(recent.iloc[i]) < float(recent.iloc[i + 1])
    ]
    return len(swings) >= 3 and swings[-1] > swings[-2] > swings[-3]


def compute_declining_sell_volume(ohlc_df, window=10, reduction=0.80):
    """
    Average volume on red candles in the recent half of `window` is <=
    `reduction` × the earlier half — sellers running out of fuel.
    """
    needed = ['open', 'close', 'volume']
    if not all(c in ohlc_df.columns for c in needed):
        return False
    recent = ohlc_df.iloc[-window:].dropna(subset=needed)
    if len(recent) < 6:
        return False
    mid   = len(recent) // 2
    early = recent.iloc[:mid]
    late  = recent.iloc[mid:]
    rv_e  = early.loc[early['close'] < early['open'], 'volume'].values
    rv_l  = late.loc[late['close']  < late['open'],  'volume'].values
    if len(rv_e) == 0 or len(rv_l) == 0:
        return False
    return float(np.mean(rv_l)) <= float(np.mean(rv_e)) * reduction


def compute_prime_score(metrics, weights):
    """
    Weighted composite score for ranking tokens.
    Higher = more conviction.
    """
    score = 0
    max_score = 0

    for key, weight in weights.items():
        max_score += weight
        if key in metrics and metrics[key]:
            score += weight

    return (score / max_score * 100) if max_score > 0 else 0


# --- BTC DATA FOR RELATIVE STRENGTH ---
BTC_OHLC_CACHE = None

def get_btc_ohlc(days=30):
    """Fetch BTC OHLC for relative strength calculations."""
    global BTC_OHLC_CACHE
    if BTC_OHLC_CACHE is not None:
        return BTC_OHLC_CACHE

    ohlc = fetch_ohlc('bitcoin', days)
    if ohlc is not None:
        BTC_OHLC_CACHE = ohlc
    return ohlc


def fetch_top_coins():
    url = f"{COINGECKO_API_URL}/coins/markets"
    coins = []
    for page in range(1, 5):
        params = {'vs_currency':'usd','order':'market_cap_desc','per_page':250,'page':page,'price_change_percentage':'7d'}
        try:
            r = requests.get(url, headers=_CG_HEADERS, params=params, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if not data: break
                coins.extend(data)
                if len(coins) >= 1000: break
                time.sleep(1)
        except:
            break
    return coins[:1000]

def pre_filter(coins: list, btc_7d: float) -> set:
    """
    Pipeline Stage 1b: returns set of coin_ids passing prime key alpha checks.
    Reuses OHLCV already in shared cache from Stage 1a — 0 new API calls.
    """
    sym_to_id = {c['symbol'].upper(): c['id'] for c in coins}
    df = apply_prime_key_filter(coins, btc_7d)
    if df is None or df.empty:
        return set()
    result = set()
    for _, row in df.iterrows():
        sym = str(row['TOKEN']).upper()
        if sym in sym_to_id:
            result.add(sym_to_id[sym])
    return result


def apply_prime_key_filter(coins_data, btc_7d):
    # Global macro gate
    if CONFIG['REQUIRE_BTC_HEALTHY'] and (btc_7d is None or btc_7d < CONFIG['MIN_BTC_7D_CHANGE']):
        print("🛑 BTC unhealthy. Skipping Prime Key scan.")
        return pd.DataFrame()

    # Pre-fetch BTC OHLC for relative strength
    btc_ohlc = get_btc_ohlc(30)
    btc_closes = btc_ohlc['close'] if btc_ohlc is not None else None

    primary, fallback = [], []
    print(f"🔍 Scanning {len(coins_data)} tokens for Prime Key setups (ENHANCED)...")

    for i, coin in enumerate(coins_data):
        symbol, coin_id, rank = coin['symbol'].upper(), coin['id'], coin.get('market_cap_rank')
        volume_24h, price, price_7d = coin.get('total_volume'), coin.get('current_price'), coin.get('price_change_percentage_7d_in_currency')

        # Basic filters with new thresholds
        min_rank = CONFIG['MIN_MARKET_CAP_RANK']
        max_rank = CONFIG['MAX_MARKET_CAP_RANK']
        min_vol = CONFIG['MIN_24H_VOLUME_USD']
        min_price = CONFIG['MIN_PRICE']
        min_7d = CONFIG['MIN_PRICE_CHANGE_7D']
        max_7d = CONFIG['MAX_PRICE_CHANGE_7D']

        if not (isinstance(rank, int) and min_rank <= rank <= max_rank):
            continue
        if not (isinstance(volume_24h, (int, float)) and volume_24h >= min_vol):
            continue
        if not (isinstance(price, (int, float)) and price >= min_price):
            continue
        if not (isinstance(price_7d, (int, float)) and min_7d <= price_7d <= max_7d):
            continue

        print(f"[{i+1}] {symbol} (#{rank})")
        _cf = CACHE_DIR / f"{coin_id}_30d.csv"
        _cache_fresh = _cf.exists() and (time.time() - _cf.stat().st_mtime) / 3600 < 4.0
        ohlc = fetch_ohlc(coin_id, 30)
        if ohlc is None or len(ohlc) < 25:
            continue

        try:
            close, high, low = ohlc['close'], ohlc['high'], ohlc['low']
            current_price = close.iloc[-1]
            has_volume = 'volume' in ohlc.columns and not ohlc['volume'].isna().all()
            volume = ohlc['volume'] if has_volume else pd.Series([np.nan] * len(close))

            # --- CORE INDICATORS ---
            rsi = compute_rsi(close)
            atr_series = compute_atr_series(high, low, close)
            atr = atr_series.iloc[-1]
            macd_val, signal_val = compute_macd(close)
            di_plus, di_minus, adx = compute_dmi(high, low, close)

            if any(np.isnan([rsi, atr, macd_val, di_plus, adx])):
                continue

            atr_pct = atr / current_price

            # --- NEW ALPHA INDICATORS ---

            # 1. Volatility ratio (liquidity filter)
            vol_ratio = compute_volatility_ratio(high, low, close, window=5)
            passes_liquidity = not np.isnan(vol_ratio) and vol_ratio < CONFIG['MAX_VOLATILITY_RATIO']

            # 2. Relative strength vs BTC
            rs_vs_btc = np.nan
            passes_rs = True
            if CONFIG['REQUIRE_RS_VS_BTC'] and btc_closes is not None:
                rs_vs_btc = compute_relative_strength_vs_btc(close, btc_closes, CONFIG['RS_WINDOW'])
                passes_rs = not np.isnan(rs_vs_btc) and rs_vs_btc >= CONFIG['MIN_RS_VS_BTC']

            # 3. Accumulation/Distribution
            ad_slope, price_slope = np.nan, np.nan
            passes_accumulation = True
            if CONFIG['REQUIRE_ACCUMULATION'] and has_volume:
                ad_slope, price_slope = compute_accumulation_distribution(high, low, close, volume)
                passes_accumulation = not np.isnan(ad_slope) and ad_slope > CONFIG['MIN_ACCUMULATION_SLOPE']

            # 4. Breakout quality
            is_breakout, bb_width = detect_breakout_quality(high, low, close)
            passes_breakout = is_breakout if CONFIG['REQUIRE_BREAKOUT_QUALITY'] else True

            # 5. Distance from extremes
            dist_high, dist_low = compute_distance_from_extremes(close, window=7)
            # Fix: allow near-high entries when BB is tight (consolidation breakout setup)
            # Only reject FOMO when BB is wide (already in a trending/extended move)
            if not np.isnan(bb_width) and bb_width < CONFIG['BB_WIDTH_TIGHT_THRESHOLD']:
                passes_not_fomo = True  # Tight BB near high = valid breakout candidate
            else:
                passes_not_fomo = not np.isnan(dist_high) and dist_high <= CONFIG['MAX_DISTANCE_FROM_HIGH']
            passes_not_falling = not np.isnan(dist_low) and dist_low >= CONFIG['MIN_DISTANCE_FROM_LOW']

            # 6. Intraday momentum
            has_momentum, momentum_score = compute_intraday_momentum(close, CONFIG['INTRADAY_LOOKBACK'])
            passes_momentum = has_momentum if CONFIG['REQUIRE_INTRADAY_MOMENTUM'] else True

            # --- NEW PRE-TREND SIGNALS ---

            # 7. RSI bullish divergence (highest-weight signal — price lower-low, RSI higher-low)
            is_rsi_div = compute_rsi_divergence(
                close,
                window=CONFIG['DIV_WINDOW'],
                rsi_window=CONFIG['RSI_WINDOW'],
                price_gap=CONFIG['DIV_PRICE_GAP'],
                rsi_gap=CONFIG['DIV_RSI_GAP'],
            )

            # 8. MACD turning pre-crossover (fires before MACD cross — earliest momentum signal)
            is_macd_turning = compute_macd_turning(close)

            # 9. Higher lows structure (ascending demand base being built)
            is_higher_lows = compute_higher_lows(low, window=CONFIG['HL_WINDOW'])

            # 10. Declining sell volume (sellers exhausting — key accumulation sign)
            is_declining_sell = compute_declining_sell_volume(
                ohlc, window=10, reduction=CONFIG['SELL_VOL_REDUCTION']
            )

            # --- EXISTING INDICATORS ---
            passes_atr = atr_pct > CONFIG['MIN_ATR_PCT']

            passes_atr_trend = True
            atr_slope = np.nan
            if CONFIG['REQUIRE_ATR_UPTREND']:
                atr_slope = compute_trend_slope(atr_series / close, CONFIG['ATR_TREND_WINDOW'])
                passes_atr_trend = not np.isnan(atr_slope) and atr_slope > CONFIG['MIN_ATR_TREND_SLOPE']

            passes_vol_surge = True
            if CONFIG['REQUIRE_VOLUME_SURGE'] and has_volume:
                passes_vol_surge = compute_volume_surge(volume, CONFIG['VOLUME_SURGE_MULTIPLIER'])

            candle_str = compute_candle_strength(high, low, close)
            passes_candle = candle_str > CONFIG['MIN_CANDLE_STRENGTH'] if CONFIG['REQUIRE_RECENT_CANDLE_STRENGTH'] else True

            passes_rsi_rev = compute_rsi_reversal(close, CONFIG['RSI_REVERSAL_THRESHOLD']) if CONFIG['REQUIRE_RSI_REVERSAL'] else True

            passes_sma = True
            sma_20 = np.nan
            if CONFIG['REQUIRE_PRICE_ABOVE_SMA']:
                sma_20 = close.rolling(20).mean().iloc[-1]
                passes_sma = not np.isnan(sma_20) and current_price > sma_20

            passes_rsi = CONFIG['RSI_LOW'] < rsi < CONFIG['RSI_HIGH']
            passes_macd = macd_val > signal_val
            passes_dmi = di_plus > di_minus and adx > CONFIG['MIN_ADX']

            # --- COMPUTE PRIME SCORE ---
            metrics = {
                'rsi_divergence':     is_rsi_div,
                'rs_vs_btc':          passes_rs,
                'macd_turning':       is_macd_turning,
                'accumulation':       passes_accumulation,
                'atr_trend':          passes_atr_trend,
                'higher_lows':        is_higher_lows,
                'declining_sell_vol': is_declining_sell,
                'volume_surge':       passes_vol_surge,
                'breakout_quality':   is_breakout,
                'intraday_momentum':  has_momentum,
                'rsi_reversal':       passes_rsi_rev,
                'macd_cross':         passes_macd,
                'dmi_bullish':        passes_dmi,
                'candle_strength':    passes_candle,
                'price_above_sma':    passes_sma,
            }
            prime_score = compute_prime_score(metrics, CONFIG['WEIGHTS'])

            # --- QUALIFICATION LOGIC ---
            # Core requirements (must pass all)
            core_pass = all([
                passes_atr,
                passes_liquidity,
                passes_not_fomo,
                passes_not_falling,
            ])

            # Alpha requirements: need 3 of 7 (expanded — new signals included)
            alpha_checks = [
                passes_rs, passes_accumulation, passes_momentum, passes_atr_trend,
                is_rsi_div, is_higher_lows, is_macd_turning,
            ]
            alpha_pass = sum(alpha_checks) >= 3

            # Technical confirmation
            tech_checks = [passes_rsi, passes_macd, passes_dmi, passes_vol_surge, passes_candle, passes_sma]
            tech_pass = sum(tech_checks) >= 4

            # Prime Key qualification
            if core_pass and alpha_pass and tech_pass and prime_score >= 55:
                primary.append({
                    'TOKEN': symbol,
                    'RANK': rank,
                    'PRICE': round(current_price, 6),
                    'PRIME_SCORE': round(prime_score, 1),
                    'RS_VS_BTC': round(rs_vs_btc * 100, 2) if not np.isnan(rs_vs_btc) else 0,
                    'ATR_PCT': round(atr_pct * 100, 2),
                    'RSI': round(rsi, 1),
                    'ADX': round(adx, 1),
                    'CANDLE_STR': round(candle_str, 1),
                    'ACCUM': round(ad_slope, 4) if not np.isnan(ad_slope) else 0,
                    'DIST_HIGH': round(dist_high * 100, 1) if not np.isnan(dist_high) else 0,
                    'MOMENTUM': round(momentum_score * 100, 0),
                    'BREAKOUT': 'YES' if is_breakout else 'NO',
                    'POOL': 'prime_key'
                })
            elif core_pass and prime_score >= 40:
                fallback.append({
                    'TOKEN': symbol,
                    'RANK': rank,
                    'PRICE': round(current_price, 6),
                    'PRIME_SCORE': round(prime_score, 1),
                    'RS_VS_BTC': round(rs_vs_btc * 100, 2) if not np.isnan(rs_vs_btc) else 0,
                    'ATR_PCT': round(atr_pct * 100, 2),
                    'POOL': 'watchlist'
                })

        except Exception as e:
            print(f"   ⚠️ Error: {e}")
            continue

        if not _cache_fresh:
            time.sleep(_API_DELAY)

    # Return results sorted by Prime Score
    if primary:
        df = pd.DataFrame(primary).sort_values(
            ['PRIME_SCORE', 'RS_VS_BTC', 'ATR_PCT'],
            ascending=[False, False, False]
        ).head(50)
        print(f"\n✅ Prime Key: {len(df)} high-conviction tokens")
        print(f"   Top 5: {', '.join(df['TOKEN'].head(5).tolist())}")
        return df
    elif fallback:
        df = pd.DataFrame(fallback).sort_values(
            ['PRIME_SCORE', 'RS_VS_BTC'],
            ascending=[False, False]
        ).head(50)
        print(f"\n⚠️ Watchlist: {len(df)} tokens (no prime setups found)")
        return df

    print("\n❌ No qualifying tokens found")
    return pd.DataFrame()

def save_csv(df, filename):
    df.to_csv(filename, index=False)
    if not df.empty:
        print(f"📁 Saved to {filename}")


def print_summary(df):
    """Print a formatted summary of top picks."""
    if df.empty:
        return

    print("\n" + "=" * 70)
    print("🎯 TOP PRIME KEY PICKS")
    print("=" * 70)

    for _, row in df.head(10).iterrows():
        token = row['TOKEN']
        score = row.get('PRIME_SCORE', 0)
        rs = row.get('RS_VS_BTC', 0)
        atr = row.get('ATR_PCT', 0)
        rsi = row.get('RSI', 0)
        dist = row.get('DIST_HIGH', 0)
        breakout = row.get('BREAKOUT', 'NO')

        rs_indicator = "🚀" if rs > 5 else "📈" if rs > 0 else "📉"
        breakout_indicator = "💥" if breakout == 'YES' else ""

        print(f"  {token:8} | Score: {score:5.1f} | RS: {rs:+6.1f}% {rs_indicator} | "
              f"ATR: {atr:5.1f}% | RSI: {rsi:4.1f} | Dist: {dist:4.0f}% {breakout_indicator}")

    print("=" * 70)
    print(f"Total qualified: {len(df)} tokens")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("🎯 RSPS PRIME KEY v2.0: Enhanced Alpha Scanner")
    print("   Multi-factor analysis with relative strength & accumulation")
    print("=" * 70 + "\n")

    btc_7d = get_btc_7d_change()
    if btc_7d:
        btc_status = "🟢 Healthy" if btc_7d > 0 else "🟡 Cautious" if btc_7d > -5 else "🔴 Weak"
        print(f"📊 BTC 7d: {btc_7d:+.1f}% ({btc_status})")
    else:
        print("⚠️ BTC data unavailable - proceeding with caution")

    print(f"📋 Filters: Rank {CONFIG['MIN_MARKET_CAP_RANK']}-{CONFIG['MAX_MARKET_CAP_RANK']} | "
          f"Vol ${CONFIG['MIN_24H_VOLUME_USD']:,}+ | RS vs BTC >{CONFIG['MIN_RS_VS_BTC']*100:.0f}%\n")

    coins = fetch_top_coins()
    df = apply_prime_key_filter(coins, btc_7d)

    if not df.empty:
        print_summary(df)
        save_csv(df, str(_OUTPUT_DIR / "rspS_prime_key_pool.csv"))

        # Also save detailed version with all metrics
        detailed_file = str(_OUTPUT_DIR / "rspS_prime_key_detailed.csv")
        df.to_csv(detailed_file, index=False)
        print(f"📁 Detailed metrics saved to {detailed_file}")
    else:
        print("\n⚠️ No tokens qualified. Consider relaxing filters or waiting for better conditions.")