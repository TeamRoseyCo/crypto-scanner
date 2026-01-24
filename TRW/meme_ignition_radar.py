import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone

# --- CONFIG ---
CONFIG = {
    # NEW LAUNCHES (DEX)
    'MIN_NEW_PRICE_CHANGE_1H': 5.0,
    'MIN_NEW_VOLUME_USD': 25_000,
    'MIN_NEW_LIQUIDITY_USD': 10_000,
    'MAX_NEW_AGE_HOURS': 2,
    'NEW_VOLUME_SURGE_MULTIPLIER': 3.0,  # Critical!

    # EXISTING MEMES
    'EXISTING_MIN_PRICE_CHANGE_1H': 3.0,
    'EXISTING_MIN_VOLUME_USD': 100_000,
    'EXISTING_MAX_MARKET_CAP_USD': 10_000_000_000,
    'EXISTING_VOLUME_SURGE_MULTIPLIER': 2.0,
}

# --- KNOWN MEME TOKENS (expand this list) ---
KNOWN_MEMES = {
    'DOGE', 'SHIB', 'PEPE', 'WIF', 'BONK', 'FLOKI', 'MOG', 'TURBO', 'BRETT',
    'KAIA', 'MYX', 'RIVER', 'POPCAT', 'MOTHER', 'NEIRO', 'SPX', 'ACT', 'PUMP',
    'CVX', 'LITE', 'CUMMIES', 'HOGE', 'ELON', 'SAMO', 'DOGO', 'KISHU', 'AKITA', 'HUSKY',
    'SANTA', 'MOON', 'BAB', 'FLOK', 'SHIBA', 'DUST', 'GME', 'AMC', 'BB', 'NOK', 'WSB',
    'APE', 'CATE', 'DINO', 'FROG', 'GALA', 'PEPE2', 'RUG', 'SHEESH', 'TITAN', 'WOJAK',
    'XRP', 'ADA', 'SOL', 'LUNA', 'AVAX', 'MATIC', 'DOT', 'DOGECOIN', 'IP', 'WLFI',
    'CHZ', 'SFP', 'AKITAINU', 'KUMA', 'SHIBAINU', 'FLOKINU', 'HOKK', 'SAMOYEDCOIN',
    'ELONMUSK', 'PEPECASH', 'DOGOCOIN', 'BABYDOGE', 'SAFEMOON', 'SAFEMOONV2', 'CC',
    'AEVO', 'PEPEGOLD', 'PEPEGE', 'PEPEGPT', 'MEME', 'DOGEZILLA', 'SHIBX', 'FLOKIZILLA',
    'PAXG', 'JUP', 'RACA', 'SUI', 'TAMA', 'YFI', 'XMON', 'ZIL', 'ZRX', 'PYTH', 'LRC', 
    'ENS', 'GRT', 'RLY', 'MASK', 'MLN', 'RADAR', 'SAND', 'AXS', 'ILV', 'GNO', 'SYRUP',
    'ZRX', 'MANTA', 'FARTCOIN', 'CRO', 'ENS', 'KAVA', 'ICP', 'BSV', 'VET', 'FIL', 'APT', 
    'BGB', 'BCH', 'CRV', 'DASH', 'DCR', 'EGLD', 'GUSD', 'HOT', 'KSM', 'LTC', 'NEXO', 'QTUM',
    'PENGU', 'ARKM', 'SEI', 'ETC', 'AAVE', 'C98', 'ENA', 'FLOKI', 'PENDLE', 'VIRTUAL',
    'ALGO', 'INJ', 'SXP', 'DYDX', 'HBAR', 'BONK', 'ASR', 'IOTA', 'JASMY', 'GALA', 'AERO', 
    'XCN', 'DASH', 'SAND', 'USELESS', 'MOONSHOT', 'TEST', 'DINGO', 'HYPE', 'ZOO', 'BONE', 'PEPES',
    
}

def fetch_dex_new_pairs():
    """Fetch new DEX pairs (brand-new memes)"""
    try:
        r = requests.get("https://api.dexscreener.com/latest/dex/pairs", timeout=10)
        if r.status_code == 200:
            return r.json().get('pairs', [])
    except:
        pass
    return []

def filter_new_pairs(pairs):
    pool = []
    now = datetime.now(timezone.utc)
    for pair in pairs:
        try:
            chain = pair.get('chainId')
            if chain not in ['solana', 'base', 'ethereum', 'arbitrum', 'bsc']:
                continue
            base_token = pair['baseToken']['symbol'].upper()
            quote_token = pair['quoteToken']['symbol'].upper()
            if quote_token in ['USDT', 'USDC', 'DAI']:
                continue
            price_change_1h = pair.get('priceChange', {}).get('h1', 0)
            if price_change_1h < CONFIG['MIN_NEW_PRICE_CHANGE_1H']:
                continue
            volume_1h = pair.get('volume', {}).get('h1', 0)
            liquidity_usd = pair.get('liquidity', {}).get('usd', 0)
            if volume_1h < CONFIG['MIN_NEW_VOLUME_USD'] or liquidity_usd < CONFIG['MIN_NEW_LIQUIDITY_USD']:
                continue
            created_at = pair.get('createdAt')
            if created_at:
                launch_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_hours = (now - launch_time).total_seconds() / 3600
                if age_hours > CONFIG['MAX_NEW_AGE_HOURS']:
                    continue
            else:
                continue
            pool.append({
                'TOKEN': base_token,
                'TYPE': 'NEW_LAUNCH',
                'CHAIN': chain,
                'PRICE_CHANGE_1H': round(price_change_1h, 2),
                'VOLUME_1H_USD': int(volume_1h),
                'AGE_HOURS': round(age_hours, 1),
                'URL': f"https://dexscreener.com/{chain}/{pair['pairAddress']}"
            })
        except:
            continue
    return pool

def fetch_existing_memes():
    """Fetch known meme tokens from CoinGecko"""
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        'vs_currency': 'usd',
        'order': 'market_cap_desc',
        'per_page': 250,
        'page': 1
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 200:
            coins = r.json()
            return [c for c in coins if c['symbol'].upper() in KNOWN_MEMES]
    except:
        pass
    return []

def enrich_with_1h_data(coins_list):
    enriched = []
    for coin in coins_list:
        try:
            detail = requests.get(f"https://api.coingecko.com/api/v3/coins/{coin['id']}", timeout=10)
            if detail.status_code == 200:
                d = detail.json()
                market_data = d.get('market_data', {})
                coin['price_change_percentage_1h'] = market_data.get('price_change_percentage_1h_in_currency', {}).get('usd', 0)
                coin['total_volume_1h'] = market_data.get('total_volume', {}).get('usd', 0)
                coin['market_cap_usd'] = market_data.get('market_cap', {}).get('usd', 0)
                enriched.append(coin)
            time.sleep(2.2)
        except:
            time.sleep(1)
    return enriched

def filter_existing_memes(coins_data):
    pool = []
    for coin in coins_data:
        symbol = coin['symbol'].upper()
        price_change_1h = coin.get('price_change_percentage_1h', 0)
        volume_1h = coin.get('total_volume_1h', 0)
        market_cap = coin.get('market_cap_usd', 0)
        
        if price_change_1h < CONFIG['EXISTING_MIN_PRICE_CHANGE_1H']:
            continue
        if volume_1h < CONFIG['EXISTING_MIN_VOLUME_USD']:
            continue
        if market_cap > CONFIG['EXISTING_MAX_MARKET_CAP_USD']:
            continue
            
        pool.append({
            'TOKEN': symbol,
            'TYPE': 'EXISTING_MEME',
            'PRICE_CHANGE_1H': round(price_change_1h, 2),
            'VOLUME_1H_USD': int(volume_1h),
            'MARKET_CAP_USD': int(market_cap) if market_cap else 0
        })
    return pool

def save_results(new_pool, existing_pool, filename="meme_ignition_dual.csv"):
    all_results = []
    all_results.extend(new_pool)
    all_results.extend(existing_pool)
    
    if all_results:
        df = pd.DataFrame(all_results)
        # Sort: new launches first, then by momentum
        df['SORT_KEY'] = df['TYPE'].map({'NEW_LAUNCH': 0, 'EXISTING_MEME': 1})
        df = df.sort_values(['SORT_KEY', 'PRICE_CHANGE_1H'], ascending=[True, False])
        df.to_csv(filename, index=False)
        print(f"🔥 {len(df)} total meme candidates:")
        print(df[['TOKEN', 'TYPE', 'PRICE_CHANGE_1H', 'VOLUME_1H_USD']].head(15).to_string(index=False))
    else:
        print("🌙 No meme ignition signals detected.")
        pd.DataFrame().to_csv(filename, index=False)

if __name__ == "__main__":
    print("🌕 MEME IGNITION RADAR — DUAL MODE")
    print("   • NEW LAUNCHES: DEX-based, <6h old")
    print("   • EXISTING MEMES: Known tokens with renewed momentum\n")
    
    # Scan new launches
    print("📡 Scanning DEX for new launches...")
    dex_pairs = fetch_dex_new_pairs()
    new_pool = filter_new_pairs(dex_pairs)
    
    # Scan existing memes
    print("📈 Checking existing memes for momentum...")
    existing_coins = fetch_existing_memes()
    enriched_coins = enrich_with_1h_data(existing_coins)
    existing_pool = filter_existing_memes(enriched_coins)
    
    # Save combined results
    save_results(new_pool, existing_pool, "meme_ignition_dual.csv")