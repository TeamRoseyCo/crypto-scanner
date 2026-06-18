import requests
import time
import pandas as pd
from datetime import datetime, timedelta

# CoinGecko API base URL
BASE_URL = "https://api.coingecko.com/api/v3"

def get_top_coins(limit=500):
    """
    Fetch top coins by market cap (up to 500).
    """
    coins = []
    per_page = 250  # CoinGecko max per page
    pages = (limit + per_page - 1) // per_page  # Calculate number of pages needed

    for page in range(1, pages + 1):
        fetch = min(per_page, limit - len(coins))
        url = f"{BASE_URL}/coins/markets"
        params = {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': fetch,
            'page': page,
            'sparkline': False
        }
        response = requests.get(url, params=params)
        if response.status_code == 200:
            coins.extend(response.json())
        else:
            print(f"Error fetching top coins (page {page}): {response.status_code}")
            break
        time.sleep(1)  # Be nice to the API
    return coins

def get_24h_volume(coin_id):
    """
    Get current 24h volume for a coin.
    """
    url = f"{BASE_URL}/coins/{coin_id}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data.get('market_data', {}).get('total_volume', {}).get('usd', 0)
    return 0

def get_7day_average_volume(coin_id):
    """
    Get average daily volume over the last 7 days.
    """
    now = int(time.time())
    seven_days_ago = int((datetime.now() - timedelta(days=7)).timestamp())
    url = f"{BASE_URL}/coins/{coin_id}/market_chart/range"
    params = {
        'vs_currency': 'usd',
        'from': seven_days_ago,
        'to': now,
        'precision': 'full'
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        volumes = [v[1] for v in data.get('total_volumes', [])]  # [timestamp, volume] pairs
        if volumes:
            return sum(volumes) / len(volumes)  # Simple average
    return 0

def scan_volume_spikes():
    """
    Scan for coins where 24h volume > 5x 7-day average.
    """
    print("Fetching top 500 coins...")
    coins = get_top_coins(500)
    
    results = []
    for coin in coins:
        coin_id = coin['id']
        symbol = coin['symbol'].upper()
        name = coin['name']
        
        print(f"Checking {name} ({symbol})...")
        
        volume_24h = get_24h_volume(coin_id)
        avg_volume_7d = get_7day_average_volume(coin_id)
        
        if avg_volume_7d > 0:
            multiplier = volume_24h / avg_volume_7d
            if multiplier > 5:
                results.append({
                    'Symbol': symbol,
                    'Name': name,
                    '24h Volume (USD)': f"${volume_24h:,.2f}",
                    '7d Avg Volume (USD)': f"${avg_volume_7d:,.2f}",
                    'Multiplier': f"{multiplier:.2f}x"
                })
    
    # Output as DataFrame for easy viewing
    if results:
        df = pd.DataFrame(results)
        print("\nCoins with 24h volume > 5x 7-day average:")
        print(df.to_string(index=False))
        df.to_csv('volume_spike_coins.csv', index=False)
        print("\nSaved to 'volume_spike_coins.csv'")
    else:
        print("No coins met the criteria today.")
    
    return results

if __name__ == "__main__":
    scan_volume_spikes()