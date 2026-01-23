import requests
import pandas as pd
import numpy as np
import time
from pathlib import Path

# --- 🎯 PRIME KEY CONFIG ---
CONFIG = {
    # 🔍 Market filters
    'MAX_TOKENS_TO_FETCH': 1000,
    'MIN_24H_VOLUME_USD': 100_000,
    'MIN_MARKET_CAP_RANK': 21,
    'MAX_MARKET_CAP_RANK': 1000,
    'MIN_PRICE_CHANGE_7D': -40.0,
    'MAX_PRICE_CHANGE_7D': 100.0,
    'MIN_PRICE': 0.0001,

    # 📊 Indicators
    'RSI_WINDOW': 9,
    'ATR_WINDOW': 10,
    'MACD_FAST': 8,
    'MACD_SLOW': 17,
    'MACD_SIGNAL': 9,
    'DMI_WINDOW': 10,

    # 🎯 Thresholds
    'MIN_ATR_PCT': 0.020,
    'RSI_LOW': 20,
    'RSI_HIGH': 75,
    'MIN_ADX': 20,  # Increased for stronger trend confirmation

    # 🔥 Explosive filters
    'REQUIRE_ATR_UPTREND': True,
    'ATR_TREND_WINDOW': 3,
    'MIN_ATR_TREND_SLOPE': 0.005,  # Increased for steeper ATR trends
    'REQUIRE_VOLUME_SURGE': True,
    'VOLUME_SURGE_MULTIPLIER': 1.5,
    'REQUIRE_RECENT_CANDLE_STRENGTH': True,
    'REQUIRE_RSI_REVERSAL': True,
    'RSI_REVERSAL_THRESHOLD': 30,  # Tighter for stronger RSI reversals
    'REQUIRE_PRICE_ABOVE_SMA': True,  # New: Price must be above 20-day SMA

    # 🛡️ Macro guards
    'REQUIRE_BTC_HEALTHY': True,
    'MIN_BTC_7D_CHANGE': -7.0,
}

COINGECKO_API_URL = "https://api.coingecko.com/api/v3"
CACHE_DIR = Path("cache_prime_key")
CACHE_DIR.mkdir(exist_ok=True)

# --- FETCH BTC 7D CHANGE ---
def get_btc_7d_change():
    try:
        r = requests.get(f"{COINGECKO_API_URL}/coins/bitcoin", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data['market_data']['price_change_percentage_7d_in_currency']['usd']
    except:
        pass
    return None

# --- OHLC + VOLUME FETCH (same as before) ---
def fetch_ohlc(coin_id, days=60):
    cache_file = CACHE_DIR / f"{coin_id}_{days}d.csv"
    if cache_file.exists():
        try:
            return pd.read_csv(cache_file, index_col=0)
        except:
            pass
    allowed_days = [1, 7, 14, 30, 90, 180, 365]
    chosen_days = min(allowed_days, key=lambda x: abs(x - days))
    url = f"{COINGECKO_API_URL}/coins/{coin_id}/ohlc"
    params = {'vs_currency': 'usd', 'days': chosen_days}
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(30 * (2 ** attempt))
                continue
            if r.status_code != 200:
                return None
            data = r.json()
            if not (isinstance(data, list) and len(data) >= 20):
                return None
            highs = [row[2] for row in data]
            lows = [row[3] for row in data]
            closes = [row[4] for row in data]
            idx = [pd.to_datetime(row[0], unit='ms') for row in data]
            df = pd.DataFrame({'close': closes, 'high': highs, 'low': lows}, index=idx)
            # Fetch volume
            try:
                mc_url = f"{COINGECKO_API_URL}/coins/{coin_id}/market_chart"
                mc = requests.get(mc_url, params={'vs_currency':'usd','days':chosen_days,'interval':'daily'}, timeout=10)
                if mc.status_code == 200:
                    vols = [v[1] for v in mc.json().get('total_volumes', [])]
                    if len(vols) == len(idx):
                        df['volume'] = vols
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

def fetch_top_coins():
    url = f"{COINGECKO_API_URL}/coins/markets"
    coins = []
    for page in range(1, 5):
        params = {'vs_currency':'usd','order':'market_cap_desc','per_page':250,'page':page,'price_change_percentage':'7d'}
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if not data: break
                coins.extend(data)
                if len(coins) >= 1000: break
                time.sleep(1)
        except:
            break
    return coins[:1000]

def apply_prime_key_filter(coins_data, btc_7d):
    # Global macro gate
    if CONFIG['REQUIRE_BTC_HEALTHY'] and (btc_7d is None or btc_7d < CONFIG['MIN_BTC_7D_CHANGE']):
        print("🛑 BTC unhealthy. Skipping Prime Key scan.")
        return pd.DataFrame()

    primary, fallback = [], []
    print(f"🔍 Scanning {len(coins_data)} tokens for Prime Key setups...")

    for i, coin in enumerate(coins_data):
        symbol, coin_id, rank = coin['symbol'].upper(), coin['id'], coin.get('market_cap_rank')
        volume, price, price_7d = coin.get('total_volume'), coin.get('current_price'), coin.get('price_change_percentage_7d_in_currency')
        if not (isinstance(rank, int) and 21 <= rank <= 1000 and isinstance(volume, (int,float)) and volume >= 100_000 and isinstance(price, (int,float)) and price >= 0.0001 and isinstance(price_7d, (int,float)) and -40 <= price_7d <= 100):
            continue

        print(f"[{i+1}] {symbol} (#{rank})")
        ohlc = fetch_ohlc(coin_id, 60)
        if ohlc is None or len(ohlc) < 25: continue

        try:
            close, high, low = ohlc['close'], ohlc['high'], ohlc['low']
            current_price = close.iloc[-1]
            rsi = compute_rsi(close)
            atr_series = compute_atr_series(high, low, close)
            atr = atr_series.iloc[-1]
            macd_val, signal_val = compute_macd(close)
            di_plus, di_minus, adx = compute_dmi(high, low, close)
            if any(np.isnan([rsi, atr, macd_val, di_plus, adx])): continue
            atr_pct = atr / current_price

            passes_vol = atr_pct > 0.02
            passes_atr_trend = True
            if CONFIG['REQUIRE_ATR_UPTREND']:
                slope = compute_trend_slope(atr_series / close, 3)
                passes_atr_trend = not np.isnan(slope) and slope > CONFIG['MIN_ATR_TREND_SLOPE']
            passes_vol_surge = True
            if CONFIG['REQUIRE_VOLUME_SURGE'] and 'volume' in ohlc.columns:
                passes_vol_surge = compute_volume_surge(ohlc['volume'])
            passes_candle = compute_candle_strength(high, low, close) > 70 if CONFIG['REQUIRE_RECENT_CANDLE_STRENGTH'] else True
            passes_rsi_rev = compute_rsi_reversal(close) if CONFIG['REQUIRE_RSI_REVERSAL'] else True
            passes_sma = True
            if CONFIG['REQUIRE_PRICE_ABOVE_SMA']:
                sma = close.rolling(20).mean().iloc[-1]
                passes_sma = not np.isnan(sma) and close.iloc[-1] > sma
            passes_rsi = 20 < rsi < 75
            passes_macd = macd_val > signal_val
            passes_dmi = di_plus > di_minus
            passes_7d = price_7d > 0

            tech_score = sum([passes_vol, passes_rsi, passes_macd, passes_dmi, passes_7d])
            all_explosive = all([passes_atr_trend, passes_vol_surge, passes_candle, passes_rsi_rev, passes_sma])

            if passes_vol and all_explosive and tech_score >= 4:
                primary.append({
                    'TOKEN': symbol, 'MARKET_CAP_RANK': rank, 'PRICE': current_price,
                    'ATR_PCT': round(atr_pct*100,2), 'RSI_14': round(rsi,2),
                    'CANDLE_STR': round(compute_candle_strength(high, low, close),1),
                    'TECH_SCORE': tech_score, 'POOL': 'prime_key'
                })
            elif passes_vol:
                fallback.append({'TOKEN': symbol, 'ATR_PCT': round(atr_pct*100,2), 'POOL': 'fallback'})
        except Exception as e:
            continue
        time.sleep(6.3)

    # Return top 50
    if primary:
        df = pd.DataFrame(primary).sort_values(['TECH_SCORE','ATR_PCT','PRICE'], ascending=[False,False,False]).head(50)
        print(f"✅ Prime Key: {len(df)} tokens")
        return df
    elif fallback:
        df = pd.DataFrame(fallback).sort_values('ATR_PCT', ascending=False).head(50)
        print(f"⚠️ Fallback: {len(df)} tokens")
        return df
    return pd.DataFrame()

def save_csv(df, filename):
    df.to_csv(filename, index=False)
    if not df.empty:
        print(f"📁 Saved to {filename}")

if __name__ == "__main__":
    print("🎯 RSPS Prime Key: High-Conviction Structured Alts")
    btc_7d = get_btc_7d_change()
    print(f"📈 BTC 7d: {btc_7d:.1f}%" if btc_7d else "⚠️ BTC data unavailable")
    coins = fetch_top_coins()
    df = apply_prime_key_filter(coins, btc_7d)
    save_csv(df, "rspS_prime_key_pool.csv")