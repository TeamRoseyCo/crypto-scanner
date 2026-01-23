import requests
import pandas as pd
import numpy as np
import time
from pathlib import Path

# Updated CONFIG for early detection
CONFIG = {
    'MAX_TOKENS_TO_SCAN': 500,  # Scan more coins
    'MIN_VOLUME_USD': 100_000,  # Lower volume threshold
    'MIN_PRICE_USD': 0.0001,
    'MAX_PRICE_CHANGE_24H': 10.0,  # Focus on low-change coins
    'MIN_PRICE_CHANGE_24H': -10.0,  # Allow slight declines
    'REQUIRE_BTC_HEALTHY': True,
    # New early-signal thresholds
    'RSI_OVERSOLD': 35,
    'MIN_VOLUME_TREND': 1.2,  # Volume 20% higher than average
    'REQUIRE_PRICE_ABOVE_SMA': True,  # Price > 50-day SMA
    'REQUIRE_RSI_REVERSAL': True,
    'REQUIRE_BULLISH_DIVERGENCE': True,  # Optional: MACD divergence
}

COINGECKO_API_URL = "https://api.coingecko.com/api/v3"
CACHE_DIR = Path("cache_momentum")
CACHE_DIR.mkdir(exist_ok=True)

def get_btc_7d_change():
    try:
        r = requests.get(f"{COINGECKO_API_URL}/coins/bitcoin", timeout=10)
        if r.status_code == 200:
            return r.json()['market_data']['price_change_percentage_7d_in_currency']['usd']
    except:
        pass
    return None

# --- OHLC + VOLUME FETCH ---
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

# Update fetch_top_gainers to fetch broader list
def fetch_potential_early_momentum():
    url = f"{COINGECKO_API_URL}/coins/markets"
    coins = []
    for page in range(1, 6):  # Fetch more pages
        params = {'vs_currency':'usd','order':'market_cap_desc','per_page':250,'page':page,'price_change_percentage':'24h'}
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 200:
                coins.extend(r.json())
                if len(coins) >= 1000: break
        except:
            break
    return coins[:1000]

# Add indicator functions (reuse from rsps_prime_key.py)
def compute_rsi(prices, window=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs)).iloc[-1]

def compute_volume_trend(volumes, window=20):
    avg_vol = volumes.iloc[-window:].mean()
    recent_vol = volumes.iloc[-5:].mean()
    return recent_vol / avg_vol if avg_vol > 0 else 1

def compute_macd_divergence(high, low, close):
    # Simple check: if close is near low but MACD is rising
    macd = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    signal = macd.ewm(span=9).mean()
    return macd.iloc[-1] > signal.iloc[-1] and close.iloc[-1] < close.iloc[-10:].max() * 0.95  # Rough divergence

# Updated filter function
def apply_early_momentum_filter(coins_data, btc_7d):
    if CONFIG['REQUIRE_BTC_HEALTHY'] and (btc_7d is None or btc_7d < CONFIG['MIN_BTC_7D_CHANGE']):
        print("🛑 BTC unhealthy. Skipping Early Momentum.")
        return pd.DataFrame()

    pool = []
    for coin in coins_data[:CONFIG['MAX_TOKENS_TO_SCAN']]:
        symbol = coin['symbol'].upper()
        price_change_24h = coin.get('price_change_percentage_24h')
        volume = coin.get('total_volume')
        price = coin.get('current_price')
        rank = coin.get('market_cap_rank', 9999)

        if not (
            isinstance(price_change_24h, (int, float)) and CONFIG['MIN_PRICE_CHANGE_24H'] <= price_change_24h <= CONFIG['MAX_PRICE_CHANGE_24H']
            and isinstance(volume, (int, float)) and volume >= CONFIG['MIN_VOLUME_USD']
            and isinstance(price, (int, float)) and price >= CONFIG['MIN_PRICE_USD']
            and rank >= 21
        ):
            continue

        # Fetch OHLC and compute indicators
        ohlc = fetch_ohlc(coin['id'], 60)  # Reuse from prime_key
        if ohlc is None or len(ohlc) < 30: continue

        close = ohlc['close']
        rsi = compute_rsi(close)
        vol_trend = compute_volume_trend(ohlc['volume']) if 'volume' in ohlc.columns else 1
        sma_50 = close.rolling(50).mean().iloc[-1]
        price_above_sma = close.iloc[-1] > sma_50 if not np.isnan(sma_50) else False
        divergence = compute_macd_divergence(ohlc['high'], ohlc['low'], close) if CONFIG['REQUIRE_BULLISH_DIVERGENCE'] else True

        if (
            rsi < CONFIG['RSI_OVERSOLD']
            and vol_trend >= CONFIG['MIN_VOLUME_TREND']
            and (not CONFIG['REQUIRE_PRICE_ABOVE_SMA'] or price_above_sma)
            and divergence
        ):
            pool.append({
                'TOKEN': symbol,
                'RSI': round(rsi, 2),
                'VOLUME_TREND': round(vol_trend, 2),
                'PRICE_CHANGE_24H': price_change_24h,
                'POOL': 'early_momentum'
            })

    if pool:
        df = pd.DataFrame(pool).sort_values(['RSI', 'VOLUME_TREND'], ascending=[True, False]).head(50)
        print(f"🌱 Early Momentum: {len(df)} tokens")
        return df
    return pd.DataFrame()

def save_csv(df, filename):
    df.to_csv(filename, index=False)
    if not df.empty:
        print(f"📁 Saved to {filename}")

# Update main
if __name__ == "__main__":
    print("🌱 RSPS Early Momentum: Catch Uptrends Before They Ignite")
    btc_7d = get_btc_7d_change()
    print(f"📈 BTC 7d: {btc_7d:.1f}%" if btc_7d else "⚠️ BTC data unavailable")
    coins = fetch_potential_early_momentum()
    df = apply_early_momentum_filter(coins, btc_7d)
    save_csv(df, "rspS_early_momentum_pool.csv")