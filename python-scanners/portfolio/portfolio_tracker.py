"""
PORTFOLIO TRACKER & RISK MANAGER
Real-time position monitoring with stop-loss alerts and performance tracking
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime
import json
from pathlib import Path

# ==================== CONFIGURATION ====================

# Resolve output directories relative to this script's location
_SCRIPT_DIR = Path(__file__).resolve().parent
_PORTFOLIO_DIR = _SCRIPT_DIR / "../../outputs/portfolio"
_PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
_SCANNER_DIR = _SCRIPT_DIR / "../../outputs/scanner-results"
_SCANNER_DIR.mkdir(parents=True, exist_ok=True)

PORTFOLIO_FILE = str(_PORTFOLIO_DIR / "portfolio_positions.json")
ALERTS_FILE = str(_PORTFOLIO_DIR / "price_alerts.json")
TRADES_LOG = str(_PORTFOLIO_DIR / "trades_history.csv")

# Default portfolio structure
DEFAULT_PORTFOLIO = {
    "positions": [],
    "cash_balance": 10000,  # Starting capital in USD
    "total_invested": 0,
    "realized_pnl": 0
}

# Default alerts structure
DEFAULT_ALERTS = {
    "positions": []
}

COINGECKO_API = "https://api.coingecko.com/api/v3"

# ==================== POSITION MANAGEMENT ====================

def load_portfolio():
    """Load portfolio from JSON file"""
    if Path(PORTFOLIO_FILE).exists():
        with open(PORTFOLIO_FILE, 'r') as f:
            return json.load(f)
    return DEFAULT_PORTFOLIO.copy()

def save_portfolio(portfolio):
    """Save portfolio to JSON file"""
    with open(PORTFOLIO_FILE, 'w') as f:
        json.dump(portfolio, f, indent=2)
    print(f"✅ Portfolio saved to {PORTFOLIO_FILE}")

def load_alerts():
    """Load alerts from JSON file"""
    if Path(ALERTS_FILE).exists():
        with open(ALERTS_FILE, 'r') as f:
            return json.load(f)
    return DEFAULT_ALERTS.copy()

def save_alerts(alerts):
    """Save alerts to JSON file"""
    with open(ALERTS_FILE, 'w') as f:
        json.dump(alerts, f, indent=2)

def get_current_price(coin_id):
    """Fetch current price from CoinGecko"""
    try:
        url = f"{COINGECKO_API}/simple/price"
        params = {
            'ids': coin_id,
            'vs_currencies': 'usd',
            'include_24hr_change': 'true'
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if coin_id in data:
                return {
                    'price': data[coin_id]['usd'],
                    'change_24h': data[coin_id].get('usd_24h_change', 0)
                }
    except Exception as e:
        print(f"⚠️ Error fetching price for {coin_id}: {e}")
    return None

def add_position(symbol, coin_id, entry_price, quantity, stop_loss, take_profit_levels):
    """Add new position to portfolio"""
    portfolio = load_portfolio()
    alerts = load_alerts()

    position_value = entry_price * quantity

    # Check if enough cash
    if position_value > portfolio['cash_balance']:
        print(f"❌ Insufficient cash. Need ${position_value:.2f}, have ${portfolio['cash_balance']:.2f}")
        return False

    position = {
        'symbol': symbol.upper(),
        'coin_id': coin_id.lower(),
        'entry_price': entry_price,
        'entry_date': datetime.now().isoformat(),
        'quantity': quantity,
        'stop_loss': stop_loss,
        'take_profit_levels': take_profit_levels,
        'status': 'OPEN',
        'notes': ''
    }

    portfolio['positions'].append(position)
    portfolio['cash_balance'] -= position_value
    portfolio['total_invested'] += position_value

    # Set up alerts
    alert = {
        'symbol': symbol.upper(),
        'coin_id': coin_id.lower(),
        'stop_loss': stop_loss,
        'take_profits': take_profit_levels,
        'entry_price': entry_price
    }
    alerts['positions'].append(alert)

    save_portfolio(portfolio)
    save_alerts(alerts)

    # Log trade
    log_trade('BUY', symbol, entry_price, quantity, position_value, 'Position opened')

    print(f"\n✅ Position Added: {symbol}")
    print(f"   Entry: ${entry_price:.6f}")
    print(f"   Quantity: {quantity:.2f}")
    print(f"   Value: ${position_value:.2f}")
    print(f"   Stop-Loss: ${stop_loss:.6f} ({((stop_loss/entry_price - 1) * 100):.1f}%)")
    print(f"   Take-Profits: {take_profit_levels}")

    return True

def close_position(symbol, exit_price, reason='Manual close'):
    """Close position and calculate P&L"""
    portfolio = load_portfolio()

    # Find position
    position = None
    for i, pos in enumerate(portfolio['positions']):
        if pos['symbol'] == symbol and pos['status'] == 'OPEN':
            position = pos
            position_idx = i
            break

    if not position:
        print(f"❌ No open position found for {symbol}")
        return False

    # Calculate P&L
    entry_value = position['entry_price'] * position['quantity']
    exit_value = exit_price * position['quantity']
    pnl = exit_value - entry_value
    pnl_pct = (pnl / entry_value) * 100

    # Update portfolio
    portfolio['positions'][position_idx]['status'] = 'CLOSED'
    portfolio['positions'][position_idx]['exit_price'] = exit_price
    portfolio['positions'][position_idx]['exit_date'] = datetime.now().isoformat()
    portfolio['positions'][position_idx]['pnl'] = pnl
    portfolio['positions'][position_idx]['pnl_pct'] = pnl_pct
    portfolio['positions'][position_idx]['close_reason'] = reason

    portfolio['cash_balance'] += exit_value
    portfolio['realized_pnl'] += pnl

    save_portfolio(portfolio)

    # Log trade
    log_trade('SELL', symbol, exit_price, position['quantity'], exit_value, reason)

    print(f"\n✅ Position Closed: {symbol}")
    print(f"   Entry: ${position['entry_price']:.6f}")
    print(f"   Exit: ${exit_price:.6f}")
    print(f"   P&L: ${pnl:.2f} ({pnl_pct:+.2f}%)")
    print(f"   Reason: {reason}")

    return True

def log_trade(action, symbol, price, quantity, value, notes):
    """Log trade to CSV"""
    trade = {
        'timestamp': datetime.now().isoformat(),
        'action': action,
        'symbol': symbol,
        'price': price,
        'quantity': quantity,
        'value': value,
        'notes': notes
    }

    df = pd.DataFrame([trade])

    if Path(TRADES_LOG).exists():
        df.to_csv(TRADES_LOG, mode='a', header=False, index=False)
    else:
        df.to_csv(TRADES_LOG, index=False)

# ==================== MONITORING & ALERTS ====================

def check_alerts():
    """Check all positions against stop-loss and take-profit levels"""
    portfolio = load_portfolio()
    alerts = load_alerts()

    triggered_alerts = []

    print("\n" + "="*80)
    print("🔔 CHECKING PRICE ALERTS")
    print("="*80)

    for position in portfolio['positions']:
        if position['status'] != 'OPEN':
            continue

        symbol = position['symbol']
        coin_id = position['coin_id']

        # Get current price
        price_data = get_current_price(coin_id)
        if not price_data:
            print(f"⚠️ Could not fetch price for {symbol}")
            continue

        current_price = price_data['price']
        change_24h = price_data['change_24h']
        entry_price = position['entry_price']
        pnl_pct = ((current_price / entry_price) - 1) * 100

        # Check stop-loss
        if current_price <= position['stop_loss']:
            alert = {
                'type': 'STOP_LOSS',
                'symbol': symbol,
                'current_price': current_price,
                'trigger_price': position['stop_loss'],
                'pnl_pct': pnl_pct,
                'message': f"🚨 STOP-LOSS TRIGGERED for {symbol}! Current: ${current_price:.6f}, Stop: ${position['stop_loss']:.6f}"
            }
            triggered_alerts.append(alert)

        # Check take-profit levels
        for i, tp_level in enumerate(position['take_profit_levels']):
            if current_price >= tp_level:
                alert = {
                    'type': 'TAKE_PROFIT',
                    'symbol': symbol,
                    'level': i + 1,
                    'current_price': current_price,
                    'trigger_price': tp_level,
                    'pnl_pct': pnl_pct,
                    'message': f"🎯 TAKE-PROFIT {i+1} REACHED for {symbol}! Current: ${current_price:.6f}, Target: ${tp_level:.6f}"
                }
                triggered_alerts.append(alert)

        # Status display
        status_icon = "🟢" if pnl_pct > 0 else "🔴"
        print(f"\n{status_icon} {symbol}")
        print(f"   Entry: ${entry_price:.6f}")
        print(f"   Current: ${current_price:.6f} ({change_24h:+.2f}% 24h)")
        print(f"   P&L: {pnl_pct:+.2f}%")
        print(f"   Stop-Loss: ${position['stop_loss']:.6f} ({((position['stop_loss']/current_price - 1) * 100):+.2f}%)")

        if position['take_profit_levels']:
            print(f"   Take-Profits:")
            for i, tp in enumerate(position['take_profit_levels']):
                distance_pct = ((tp / current_price - 1) * 100)
                status = "✅" if current_price >= tp else "⏳"
                print(f"      {status} TP{i+1}: ${tp:.6f} ({distance_pct:+.2f}%)")

    # Display triggered alerts
    if triggered_alerts:
        print("\n" + "="*80)
        print("⚠️⚠️⚠️  ALERTS TRIGGERED  ⚠️⚠️⚠️")
        print("="*80)
        for alert in triggered_alerts:
            print(f"\n{alert['message']}")
            print(f"   P&L: {alert['pnl_pct']:+.2f}%")

            if alert['type'] == 'STOP_LOSS':
                print(f"   ⚠️ ACTION REQUIRED: Close position to limit losses!")
            elif alert['type'] == 'TAKE_PROFIT':
                print(f"   💰 ACTION SUGGESTED: Consider taking partial profits (30-50%)")
    else:
        print("\n✅ No alerts triggered. All positions within normal range.")

    print("\n" + "="*80)

def view_portfolio():
    """Display complete portfolio overview"""
    portfolio = load_portfolio()

    print("\n" + "="*80)
    print("📊 PORTFOLIO OVERVIEW")
    print("="*80)

    # Calculate metrics
    total_position_value = 0
    unrealized_pnl = 0

    open_positions = [p for p in portfolio['positions'] if p['status'] == 'OPEN']
    closed_positions = [p for p in portfolio['positions'] if p['status'] == 'CLOSED']

    # Calculate open positions value
    for position in open_positions:
        price_data = get_current_price(position['coin_id'])
        if price_data:
            current_value = price_data['price'] * position['quantity']
            entry_value = position['entry_price'] * position['quantity']
            total_position_value += current_value
            unrealized_pnl += (current_value - entry_value)

    total_portfolio_value = portfolio['cash_balance'] + total_position_value
    total_pnl = portfolio['realized_pnl'] + unrealized_pnl
    total_return_pct = (total_pnl / portfolio['total_invested'] * 100) if portfolio['total_invested'] > 0 else 0

    # Display summary
    print(f"\n💰 ACCOUNT SUMMARY:")
    print(f"   Total Portfolio Value: ${total_portfolio_value:,.2f}")
    print(f"   Cash Balance: ${portfolio['cash_balance']:,.2f}")
    print(f"   Positions Value: ${total_position_value:,.2f}")
    print(f"   Total Invested: ${portfolio['total_invested']:,.2f}")
    print(f"\n📈 PERFORMANCE:")
    print(f"   Realized P&L: ${portfolio['realized_pnl']:,.2f}")
    print(f"   Unrealized P&L: ${unrealized_pnl:,.2f}")
    print(f"   Total P&L: ${total_pnl:,.2f} ({total_return_pct:+.2f}%)")

    # Open positions
    if open_positions:
        print(f"\n🟢 OPEN POSITIONS ({len(open_positions)}):")
        for pos in open_positions:
            price_data = get_current_price(pos['coin_id'])
            if price_data:
                current_price = price_data['price']
                current_value = current_price * pos['quantity']
                entry_value = pos['entry_price'] * pos['quantity']
                pos_pnl = current_value - entry_value
                pos_pnl_pct = (pos_pnl / entry_value) * 100

                print(f"\n   {pos['symbol']}")
                print(f"      Entry: ${pos['entry_price']:.6f} | Current: ${current_price:.6f}")
                print(f"      Quantity: {pos['quantity']:.2f}")
                print(f"      Value: ${current_value:,.2f}")
                print(f"      P&L: ${pos_pnl:,.2f} ({pos_pnl_pct:+.2f}%)")
                print(f"      Opened: {pos['entry_date'][:10]}")

    # Closed positions
    if closed_positions:
        print(f"\n⚪ CLOSED POSITIONS ({len(closed_positions)}):")
        wins = [p for p in closed_positions if p.get('pnl', 0) > 0]
        losses = [p for p in closed_positions if p.get('pnl', 0) <= 0]
        win_rate = (len(wins) / len(closed_positions) * 100) if closed_positions else 0

        print(f"   Win Rate: {win_rate:.1f}% ({len(wins)} wins, {len(losses)} losses)")

        for pos in closed_positions[-5:]:  # Show last 5 closed
            pnl_icon = "✅" if pos.get('pnl', 0) > 0 else "❌"
            print(f"\n   {pnl_icon} {pos['symbol']}")
            print(f"      Entry: ${pos['entry_price']:.6f} | Exit: ${pos.get('exit_price', 0):.6f}")
            print(f"      P&L: ${pos.get('pnl', 0):,.2f} ({pos.get('pnl_pct', 0):+.2f}%)")
            print(f"      Reason: {pos.get('close_reason', 'N/A')}")

    print("\n" + "="*80)

# ==================== SCANNER INTEGRATION ====================

def import_from_scanner(csv_file=str(_SCANNER_DIR / 'rspS_prime_key_pool.csv')):
    """Import top candidates from scanner results"""
    try:
        df = pd.read_csv(csv_file)

        print("\n" + "="*80)
        print("📡 SCANNER RESULTS - TOP CANDIDATES")
        print("="*80)

        if df.empty:
            print("No candidates found in scanner results.")
            return

        # Display top 5
        for idx, row in df.head(5).iterrows():
            symbol = row['TOKEN']
            price = row['PRICE']
            prime_score = row['PRIME_SCORE']
            rs_vs_btc = row['RS_VS_BTC']

            print(f"\n{idx+1}. {symbol}")
            print(f"   Price: ${price:.6f}")
            print(f"   Prime Score: {prime_score:.1f}")
            print(f"   RS vs BTC: {rs_vs_btc:+.2f}%")

            # Suggested entry based on scanner
            suggested_stop = price * 0.90  # -10% stop
            suggested_tp1 = price * 1.20   # +20%
            suggested_tp2 = price * 1.40   # +40%

            print(f"   Suggested Stop-Loss: ${suggested_stop:.6f}")
            print(f"   Suggested TP1: ${suggested_tp1:.6f} (+20%)")
            print(f"   Suggested TP2: ${suggested_tp2:.6f} (+40%)")

        print("\n" + "="*80)
        print("💡 Use add_position() to enter trades based on scanner results")
        print("="*80)

    except FileNotFoundError:
        print(f"❌ Scanner file not found: {csv_file}")
    except Exception as e:
        print(f"❌ Error importing scanner data: {e}")

# ==================== MAIN MENU ====================

def main_menu():
    """Interactive menu for portfolio management"""
    while True:
        print("\n" + "="*80)
        print("🎯 CRYPTO PORTFOLIO TRACKER & RISK MANAGER")
        print("="*80)
        print("\n1. View Portfolio")
        print("2. Check Alerts")
        print("3. Add Position")
        print("4. Close Position")
        print("5. Import Scanner Results")
        print("6. View Trade History")
        print("7. Exit")

        choice = input("\nSelect option (1-7): ").strip()

        if choice == '1':
            view_portfolio()
        elif choice == '2':
            check_alerts()
        elif choice == '3':
            print("\n--- ADD NEW POSITION ---")
            symbol = input("Token Symbol (e.g., SENT): ").strip().upper()
            coin_id = input("CoinGecko ID (e.g., sentient): ").strip().lower()
            entry_price = float(input("Entry Price: "))
            quantity = float(input("Quantity: "))
            stop_loss = float(input("Stop-Loss Price: "))

            tp_input = input("Take-Profit levels (comma-separated, e.g., 0.05,0.06): ").strip()
            take_profits = [float(x.strip()) for x in tp_input.split(',') if x.strip()]

            add_position(symbol, coin_id, entry_price, quantity, stop_loss, take_profits)
        elif choice == '4':
            print("\n--- CLOSE POSITION ---")
            symbol = input("Token Symbol to close: ").strip().upper()
            exit_price = float(input("Exit Price: "))
            reason = input("Reason (optional): ").strip() or "Manual close"
            close_position(symbol, exit_price, reason)
        elif choice == '5':
            import_from_scanner()
        elif choice == '6':
            if Path(TRADES_LOG).exists():
                df = pd.read_csv(TRADES_LOG)
                print("\n" + df.tail(10).to_string(index=False))
            else:
                print("No trade history found.")
        elif choice == '7':
            print("\n👋 Goodbye! Trade safely.")
            break
        else:
            print("❌ Invalid option. Please try again.")

# ==================== COMMAND LINE EXAMPLES ====================

if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                   CRYPTO PORTFOLIO TRACKER & RISK MANAGER                     ║
║                          Real-time Position Monitoring                         ║
╚═══════════════════════════════════════════════════════════════════════════════╝

QUICK START EXAMPLES:

# Interactive mode:
python portfolio_tracker.py

# Or use Python interactive shell:
>>> from portfolio_tracker import *
>>> view_portfolio()
>>> check_alerts()
>>> import_from_scanner()

# Add position example:
>>> add_position(
...     symbol='SENT',
...     coin_id='sentient',
...     entry_price=0.04028,
...     quantity=1000,
...     stop_loss=0.0362,
...     take_profit_levels=[0.0483, 0.0563, 0.0644]
... )

# Close position example:
>>> close_position('SENT', exit_price=0.0483, reason='TP1 hit')

# Monitor positions:
>>> check_alerts()  # Run this multiple times per day

══════════════════════════════════════════════════════════════════════════════
""")

    # Run main menu
    main_menu()
