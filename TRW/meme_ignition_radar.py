import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone
from pathlib import Path

# --- 🌕 CONFIG: Optimized for 00:00–01:30 GMT Meme Ignition ---
CONFIG = {
    'MAX_TOKENS_TO_FETCH': 200,
    'MIN_VOLUME_USD_1H': 50_000,      # Real volume, not bots
    'MIN_PRICE_USD': 0.00001,
    'MIN_PRICE_CHANGE_1H': 5.0,       # Only >5% moves
    'MAX_MARKET_CAP_USD': 1_000_000_000,  # Up to $1B
    'EXCLUDE_TOP_RANKED': True,        # Skip top 200
    'MAX_AGE_DAYS': 365,               # Up to 1 year old

    # 🔥 RSI Settings
    'RSI_LENGTH': 7,          # Fast RSI
    'RSI_EMA_LENGTH': 7,      # EMA of RSI
}

COINGECKO_API_URL = "https://api.coingecko.com/api/v3"
CACHE_DIR = Path("cache_meme_radar")
CACHE_DIR.mkdir(exist_ok=True)

def compute_rsi_and_ema(prices, rsi_length=7, ema_length=7):
    """Return (rsi_current, rsi_ema_current)"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0))
    loss = (-delta.where(delta < 0, 0))
    avg_gain = gain.ewm(span=rsi_length, min_periods=rsi_length).mean()
    avg_loss = loss.ewm(span=rsi_length, min_periods=rsi_length).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi_ema = rsi.ewm(span=ema_length, adjust=False).mean()
    return (
        rsi.iloc[-1] if len(rsi.dropna()) > 0 else np.nan,
        rsi_ema.iloc[-1] if len(rsi_ema.dropna()) > 0 else np.nan
    )

def fetch_ohlc_hourly(coin_id, hours=24):
    """Fetch hourly OHLC for RSI calculation"""
    cache_file = CACHE_DIR / f"{coin_id}_hourly.csv"
    if cache_file.exists():
        try:
            return pd.read_csv(cache_file, index_col=0)
        except:
            pass
    try:
        url = f"{COINGECKO_API_URL}/coins/{coin_id}/market_chart"
        params = {'vs_currency': 'usd', 'days': 1, 'interval': 'hourly'}
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            closes = [x[1] for x in data.get('prices', [])]
            if len(closes) >= 10:
                df = pd.DataFrame({'close': closes})
                df.to_csv(cache_file)
                return df
    except:
        pass
    return None

def enrich_with_1h_data(coins_list):
    """Add 1h % change, volume, age, market cap"""
    enriched = []
    for coin in coins_list[:150]:  # Limit to avoid rate limits
        try:
            detail = requests.get(f"{COINGECKO_API_URL}/coins/{coin['id']}", timeout=10)
            if detail.status_code == 200:
                d = detail.json()
                market_data = d.get('market_data', {})
                coin['price_change_percentage_1h'] = market_data.get('price_change_percentage_1h_in_currency', {}).get('usd')
                coin['total_volume_1h'] = market_data.get('total_volume', {}).get('usd', 0)
                coin['market_cap_usd'] = market_data.get('market_cap', {}).get('usd', 0)
                # Age
                genesis = d.get('genesis_date')
                if genesis:
                    try:
                        launch = datetime.fromisoformat(genesis.replace("Z", "+00:00"))
                        coin['age_days'] = (datetime.now(timezone.utc) - launch).days
                    except:
                        coin['age_days'] = 999
                else:
                    coin['age_days'] = 999
                enriched.append(coin)
            time.sleep(2.2)
        except:
            time.sleep(1)
    return enriched

def apply_meme_filter(coins_data):
    pool = []
    stablecoins = {'USDT', 'USDC', 'DAI', 'BUSD', 'TUSD', 'USDP', 'FDUSD', 'PYUSD', 'EURC'}
    
    for coin in coins_data:  # ✅ Fixed variable name
        symbol = coin['symbol'].upper()
        name = coin['name']
        price_change_1h = coin.get('price_change_percentage_1h')
        volume_1h = coin.get('total_volume_1h', 0)
        price = coin.get('current_price', 0)
        market_cap = coin.get('market_cap_usd', 0)
        rank = coin.get('market_cap_rank', 9999)
        age_days = coin.get('age_days', 999)
        
        # Basic filters
        if price_change_1h is None or not (price_change_1h > CONFIG['MIN_PRICE_CHANGE_1H']):
            continue
        if CONFIG['EXCLUDE_TOP_RANKED'] and rank <= 200:
            continue
        if symbol in stablecoins or price < CONFIG['MIN_PRICE_USD'] or volume_1h < CONFIG['MIN_VOLUME_USD_1H']:
            continue
        if market_cap > CONFIG['MAX_MARKET_CAP_USD'] or age_days > CONFIG['MAX_AGE_DAYS']:
            continue

        # Fetch hourly data for RSI
        ohlc = fetch_ohlc_hourly(coin['id'])
        if ohlc is None or len(ohlc) < 10:
            continue

        try:
            rsi_val, rsi_ema_val = compute_rsi_and_ema(
                ohlc['close'],
                rsi_length=CONFIG['RSI_LENGTH'],
                ema_length=CONFIG['RSI_EMA_LENGTH']
            )
            if np.isnan(rsi_val) or np.isnan(rsi_ema_val):
                continue

            # 🔥 Core signal: RSI > 50 AND RSI > its EMA
            if rsi_val > 50 and rsi_val > rsi_ema_val:
                pool.append({
                    'TOKEN': symbol,
                    'NAME': name,
                    'PRICE_CHANGE_1H': round(price_change_1h, 2),
                    'VOLUME_1H_USD': int(volume_1h),
                    'RSI': round(rsi_val, 1),
                    'RSI_EMA': round(rsi_ema_val, 1),
                    'MARKET_CAP_USD': int(market_cap) if market_cap else 0,
                    'AGE_DAYS': age_days,
                    'POOL': 'meme_ignition'
                })
        except Exception as e:
            continue
        time.sleep(2.2)

    if pool:
        df = pd.DataFrame(pool)
        df = df.sort_values(['PRICE_CHANGE_1H', 'VOLUME_1H_USD'], ascending=[False, False])
        return df.head(50)
    return pd.DataFrame()

def save_csv(df, filename="meme_ignition_radar.csv"):
    if df.empty:
        print("🌙 No tokens meet ignition criteria (5%+ gain, RSI>50, RSI>EMA).")
        pd.DataFrame().to_csv(filename, index=False)
    else:
        df.to_csv(filename, index=False)
        print(f"🔥 {len(df)} meme ignition candidates found!")
        print(df[['TOKEN', 'PRICE_CHANGE_1H', 'RSI', 'RSI_EMA']].head(10).to_string(index=False))

def fetch_top_coins():
    url = f"{COINGECKO_API_URL}/coins/markets"
    params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 200, 'page': 1}
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return []

if __name__ == "__main__":
    print("🌕 MEME IGNITION RADAR — 00:00–01:30 GMT Edition")
    print("   • Price up >5% in last hour")
    print("   • RSI(7) > 50 AND above its EMA(7)")
    print("   • Runs best at 01:31 GMT\n")
    
    base_coins = fetch_top_coins()
    coins = enrich_with_1h_data(base_coins)
    df = apply_meme_filter(coins)
    save_csv(df, "meme_ignition_radar.csv")