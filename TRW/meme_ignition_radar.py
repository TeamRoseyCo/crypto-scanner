import requests
import pandas as pd
import time
from datetime import datetime, timezone

# --- CONFIG ---
CONFIG = {
    'MIN_PRICE_CHANGE_1H': 20.0,      # Strong early move
    'MIN_VOLUME_USD': 50_000,
    'MIN_LIQUIDITY_USD': 20_000,
    'MAX_AGE_HOURS': 6,               # Brand new only
    'EXCLUDED_BASES': ['USDT', 'USDC', 'DAI'],  # Avoid stable pairs
}

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/pairs"

def fetch_new_pairs():
    """Fetch all new pairs from DexScreener"""
    try:
        r = requests.get(DEXSCREENER_API, timeout=10)
        if r.status_code == 200:
            return r.json().get('pairs', [])
    except Exception as e:
        print(f"⚠️ DexScreener error: {e}")
    return []

def apply_meme_filter(pairs):
    pool = []
    now = datetime.now(timezone.utc)
    
    for pair in pairs:
        try:
            # Skip if not in top chains (Solana, Base, Ethereum, Arbitrum)
            chain = pair.get('chainId')
            if chain not in ['solana', 'base', 'ethereum', 'arbitrum']:
                continue
                
            base_token = pair['baseToken']['symbol'].upper()
            quote_token = pair['quoteToken']['symbol'].upper()
            
            # Skip stable pairs
            if quote_token in CONFIG['EXCLUDED_BASES']:
                continue
                
            # Get price change
            price_change_1h = pair.get('priceChange', {}).get('h1')
            if price_change_1h is None or price_change_1h < CONFIG['MIN_PRICE_CHANGE_1H']:
                continue
                
            # Volume & liquidity
            volume_1h = pair.get('volume', {}).get('h1', 0)
            liquidity_usd = pair.get('liquidity', {}).get('usd', 0)
            if volume_1h < CONFIG['MIN_VOLUME_USD'] or liquidity_usd < CONFIG['MIN_LIQUIDITY_USD']:
                continue
                
            # Age: createdAt is in ISO format
            created_at = pair.get('createdAt')
            if created_at:
                try:
                    launch_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    age_hours = (now - launch_time).total_seconds() / 3600
                    if age_hours > CONFIG['MAX_AGE_HOURS']:
                        continue
                except:
                    continue
            else:
                continue
                
            # Add to pool
            pool.append({
                'TOKEN': base_token,
                'CHAIN': chain,
                'PRICE_CHANGE_1H': round(price_change_1h, 2),
                'VOLUME_1H_USD': int(volume_1h),
                'LIQUIDITY_USD': int(liquidity_usd),
                'AGE_HOURS': round(age_hours, 1),
                'PAIR_URL': f"https://dexscreener.com/{chain}/{pair['pairAddress']}"
            })
        except Exception as e:
            continue
            
    if pool:
        df = pd.DataFrame(pool)
        df = df.sort_values(['PRICE_CHANGE_1H', 'VOLUME_1H_USD'], ascending=[False, False])
        return df.head(50)
    return pd.DataFrame()

def save_csv(df, filename="meme_ignition_dex.csv"):
    if df.empty:
        print("🌙 No new meme ignition candidates found.")
        pd.DataFrame().to_csv(filename, index=False)
    else:
        df.to_csv(filename, index=False)
        print(f"🔥 {len(df)} DEX-based meme candidates found!")
        print(df[['TOKEN', 'CHAIN', 'PRICE_CHANGE_1H', 'VOLUME_1H_USD']].head(10).to_string(index=False))

if __name__ == "__main__":
    print("🌕 MEME IGNITION RADAR — DEX EDITION")
    print("   • Scans DexScreener for new pairs (<6h old)")
    print("   • Catches RIVER/KAIA/MYX-style rockets at liftoff\n")
    
    pairs = fetch_new_pairs()
    df = apply_meme_filter(pairs)
    save_csv(df, "meme_ignition_dex.csv")