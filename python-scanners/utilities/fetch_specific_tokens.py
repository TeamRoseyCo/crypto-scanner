import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
from pycoingecko import CoinGeckoAPI
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent

def fetch_specific_tokens(symbols):
    cg = CoinGeckoAPI()

    # Fetch all coins list to map symbols to IDs
    try:
        all_coins = cg.get_coins_list()
        symbol_to_id = {coin['symbol'].upper(): coin['id'] for coin in all_coins}
    except Exception as e:
        print(f"Error fetching coins list: {e}")
        symbol_to_id = {}

    # Convert symbols to IDs
    tokens = []
    not_mapped = []
    for sym in symbols:
        sym_upper = sym.upper()
        if sym_upper in symbol_to_id:
            tokens.append(symbol_to_id[sym_upper])
        else:
            not_mapped.append(sym)

    if not_mapped:
        print(f"Symbols not found: {', '.join(not_mapped)}")

    # Fetch market data for the specified tokens
    try:
        coins_data = cg.get_coins_markets(vs_currency='usd', ids=','.join(tokens)) if tokens else []
    except Exception as e:
        print(f"Error fetching data: {e}")
        coins_data = []

    # Prepare data for tokens found
    token_data = []
    for coin in coins_data:
        token_data.append({
            'name': coin.get('name', 'N/A'),
            'symbol': coin.get('symbol', 'N/A'),
            'id': coin.get('id', 'N/A'),
            'market_cap': coin.get('market_cap', 0),
            'circulating_supply': coin.get('circulating_supply', 0)
        })

    # Check for tokens not found
    found_ids = {coin['id'] for coin in coins_data}
    not_found = [token for token in tokens if token not in found_ids]
    if not_found:
        print(f"Tokens not found: {', '.join(not_found)}")

    return token_data

def save_csv(df, filename):
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    if not df.empty:
        print(f"📁 Saved to {filename}")

if __name__ == "__main__":
    print("🚀 Fetch Specific Tokens: Market Cap & Supply Data")
    # List of token symbols (edit this list as needed)
    symbols = ['ELON','BAN','ATH','FET','EGLD','ZIL','MON','ZRO','PIEVERSE','VET','BERA','STX','GALA','NIGHT','NEAR','FIL','ALGO','CC','INJ','MOG','VIRTUAL','TIA','BEAM','WLD']  # Example symbols - replace with your list
    
    token_data = fetch_specific_tokens(symbols)
    
    if token_data:
        df = pd.DataFrame(token_data)
        out_path = SCRIPT_DIR / "../../outputs/market-data/specific_tokens_market_data.csv"
        save_csv(df, out_path)
        print(f"🔥 Tokens found: {len(df)}")
        print(df)
    else:
        print("No data retrieved for the specified tokens.")