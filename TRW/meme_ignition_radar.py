import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone
from pathlib import Path

# --- 🌕 MEME IGNITION CONFIG (With RSI + BB) ---
CONFIG = {
    'MAX_TOKENS_TO_FETCH': 250,
    'MIN_VOLUME_USD_1H': 75_000,
    'MIN_PRICE_USD': 0.00001,
    'MIN_PRICE_CHANGE_1H': 25.0,
    'MAX_PRICE_CHANGE_1H': 3000.0,
    'MAX_MARKET_CAP_USD': 750_000_000,
    'EXCLUDE_TOP_RANKED': True,
    'MAX_AGE_DAYS': 200,

    # 🔥 RSI + BB Settings
    'RSI_LENGTH': 7,          # Fast RSI for memes
    'RSI_EMA_SMOOTH': 7,      # Smooth RSI with EMA
    'BB_LENGTH': 20,          # BB on RSI
    'BB_STDDEV': 2.0,
    'REQUIRE_RSI_ABOVE_BB': True,  # Only take if RSI breaks upper BB
}

COINGECKO_API_URL = "https://api.coingecko.com/api/v3"
CACHE_DIR = Path("cache_meme_radar")
CACHE_DIR.mkdir(exist_ok=True)

# --- RSI + BOLLINGER BANDS ON RSI ---
def compute_rsi_with_bb(prices, rsi_length=7, smooth_ema=7, bb_length=20, bb_std=2.0):
    """
    Returns (rsi_smoothed, upper_bb, lower_bb, current_rsi)
    """
    # Calculate RSI
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0))
    loss = (-delta.where(delta < 0, 0))
    avg_gain = gain.ewm(span=rsi_length, min_periods=rsi_length).mean()
    avg_loss = loss.ewm(span=rsi_length, min_periods=rsi_length).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    # Smooth RSI with EMA
    rsi_smooth = rsi.ewm(span=smooth_ema, adjust=False).mean()
    
    # Bollinger Bands on smoothed RSI
    bb_mid = rsi_smooth.rolling(window=bb_length).mean()
    bb_std = rsi_smooth.rolling(window=bb_length).std()
    bb_upper = bb_mid + (bb_std * bb_stddev)
    bb_lower = bb_mid - (bb_std * bb_stddev)
    
    return (
        rsi_smooth.iloc[-1] if not rsi_smooth.empty else np.nan,
        bb_upper.iloc[-1] if not bb_upper.empty else np.nan,
        bb_lower.iloc[-1] if not bb_lower.empty else np.nan,
        rsi.iloc[-1] if not rsi.empty else np.nan
    )

# --- FETCH OHLCV for RSI (need 30+ candles) ---
def fetch_ohlc_for_rsi(coin_id, days=30):
    cache_file = CACHE_DIR / f"{coin_id}_ohlc_rsi.csv"
    if cache_file.exists():
        try:
            return pd.read_csv(cache_file, index_col=0, parse_dates=True)
        except:
            pass
    try:
        url = f"{COINGECKO_API_URL}/coins/{coin_id}/market_chart"
        params = {'vs_currency': 'usd', 'days': days, 'interval': 'hourly'}
        r = requests.get(url, params=params, timeout=15)
        if r.status_code == 200:
            data = r.json()
            prices = [x[1] for x in data.get('prices', [])]
            if len(prices) >= 25:
                df = pd.DataFrame({'close': prices})
                df.to_csv(cache_file)
                return df
    except:
        pass
    return None

# --- FETCH TOP GAINERS (same as before) ---
def fetch_top_gainers_1h():
    url = f"{COINGECKO_API_URL}/coins/markets"
    params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 250, 'page': 1}
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()[:200]
    except:
        pass
    return []

# --- MAIN FILTER WITH RSI + BB ---
def apply_meme_filter(coins_data):
    pool = []
    stablecoins = {'USDT', 'USDC', 'DAI', 'BUSD', 'TUSD', 'USDP', 'FDUSD', 'PYUSD', 'EURC'}
    
    for coin in coins_
        symbol = coin['symbol'].upper()
        name = coin['name']
        price_change_1h = coin.get('price_change_percentage_1h')
        volume_1h = coin.get('total_volume_1h', 0)
        price = coin.get('current_price', 0)
        market_cap = coin.get('market_cap_usd', 0)
        rank = coin.get('market_cap_rank', 9999)
        age_days = coin.get('age_days', 999)
        
        # Basic filters
        if price_change_1h is None or not (25 <= price_change_1h <= 3000):
            continue
        if CONFIG['EXCLUDE_TOP_RANKED'] and rank <= 150:
            continue
        if symbol in stablecoins or price < 0.00001 or volume_1h < 75_000 or market_cap > 750_000_000 or age_days > 200:
            continue

        # Fetch OHLC for RSI
        ohlc = fetch_ohlc_for_rsi(coin['id'], days=30)
        if ohlc is None or len(ohlc) < 25:
            continue

        try:
            rsi_smooth, bb_upper, bb_lower, rsi_raw = compute_rsi_with_bb(
                ohlc['close'],
                rsi_length=CONFIG['RSI_LENGTH'],
                smooth_ema=CONFIG['RSI_EMA_SMOOTH'],
                bb_length=CONFIG['BB_LENGTH'],
                bb_std=CONFIG['BB_STDDEV']
            )
            if any(np.isnan([rsi_smooth, bb_upper])):
                continue

            # RSI breakout condition
            passes_rsi_bb = rsi_smooth > bb_upper if CONFIG['REQUIRE_RSI_ABOVE_BB'] else True

            if passes_rsi_bb:
                pool.append({
                    'TOKEN': symbol,
                    'NAME': name,
                    'PRICE_CHANGE_1H': round(price_change_1h, 2),
                    'VOLUME_1H_USD': int(volume_1h),
                    'RSI_SMOOTH': round(rsi_smooth, 1),
                    'RSI_BB_UPPER': round(bb_upper, 1),
                    'MARKET_CAP_USD': int(market_cap) if market_cap else 0,
                    'AGE_DAYS': age_days,
                    'POOL': 'meme_ignition'
                })
        except Exception as e:
            continue
        time.sleep(2.2)  # Rate limit

    if pool:
        df = pd.DataFrame(pool)
        df = df.sort_values(['PRICE_CHANGE_1H', 'VOLUME_1H_USD'], ascending=[False, False])
        return df.head(50)
    return pd.DataFrame()

# --- SAVE & RUN (same) ---
def save_csv(df, filename="meme_ignition_radar.csv"):
    if df.empty:
        print("🌙 No meme ignition with RSI breakout.")
        pd.DataFrame().to_csv(filename, index=False)
    else:
        df.to_csv(filename, index=False)
        print(f"🔥 {len(df)} RSI-breakout meme candidates!")
        print(df[['TOKEN', 'PRICE_CHANGE_1H', 'RSI_SMOOTH', 'RSI_BB_UPPER']].head(10).to_string(index=False))

def enrich_with_1h_data(coins_list):
    """Add 1h change, volume, age to each coin"""
    enriched = []
    for i, coin in enumerate(coins_list):
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

if __name__ == "__main__":
    print("🌕 MEME IGNITION RADAR — With RSI + Bollinger Breakout")
    print("   • Finds tokens with >25% 1h gain + RSI breaking upper BB")
    print("   • Perfect for catching early FOMO surges\n")
    
    base_coins = fetch_top_gainers_1h()
    coins = enrich_with_1h_data(base_coins)
    df = apply_meme_filter(coins)
    save_csv(df, "meme_ignition_radar.csv")