"""
POSITION SIZING CALCULATOR
Calculate optimal position sizes based on risk management rules
"""

import pandas as pd

class PositionSizer:
    """
    Position sizing calculator based on:
    - Account size
    - Risk per trade (% of account)
    - Stop-loss distance
    - Token rank/volatility
    """

    def __init__(self, account_size, risk_per_trade_pct=2.0):
        """
        Args:
            account_size: Total portfolio value in USD
            risk_per_trade_pct: % of account to risk per trade (default 2%)
        """
        self.account_size = account_size
        self.risk_per_trade_pct = risk_per_trade_pct
        self.risk_amount = account_size * (risk_per_trade_pct / 100)

    def calculate_position(self, entry_price, stop_loss_price, token_rank=None):
        """
        Calculate position size based on entry and stop-loss

        Args:
            entry_price: Entry price for the token
            stop_loss_price: Stop-loss price
            token_rank: Market cap rank (optional, for additional validation)

        Returns:
            dict with position details
        """
        # Calculate risk per unit
        risk_per_unit = abs(entry_price - stop_loss_price)

        if risk_per_unit == 0:
            return {
                'error': 'Stop-loss cannot equal entry price'
            }

        # Calculate quantity based on risk
        quantity = self.risk_amount / risk_per_unit

        # Calculate position value
        position_value = quantity * entry_price

        # Calculate position size as % of account
        position_pct = (position_value / self.account_size) * 100

        # Stop-loss percentage
        stop_loss_pct = ((stop_loss_price / entry_price) - 1) * 100

        # Validate against max position size rules
        warnings = []
        max_position_pct = self._get_max_position_pct(token_rank)

        if position_pct > max_position_pct:
            warnings.append(
                f"⚠️ Position size ({position_pct:.1f}%) exceeds max allowed "
                f"({max_position_pct:.1f}%) for rank #{token_rank or 'unknown'}"
            )
            # Adjust to max
            adjusted_quantity = (self.account_size * max_position_pct / 100) / entry_price
            warnings.append(f"   Adjusted quantity: {quantity:.2f} → {adjusted_quantity:.2f}")
            quantity = adjusted_quantity
            position_value = quantity * entry_price
            position_pct = max_position_pct

        return {
            'entry_price': entry_price,
            'stop_loss_price': stop_loss_price,
            'stop_loss_pct': stop_loss_pct,
            'quantity': quantity,
            'position_value': position_value,
            'position_pct': position_pct,
            'risk_amount': self.risk_amount,
            'risk_reward_ratio': None,  # To be calculated with take-profit
            'warnings': warnings
        }

    def _get_max_position_pct(self, token_rank):
        """Get max position size based on token rank"""
        if token_rank is None:
            return 10  # Default for unknown rank

        if token_rank <= 50:
            return 15  # Large cap
        elif token_rank <= 300:
            return 10  # Mid cap
        elif token_rank <= 1000:
            return 5   # Small cap
        else:
            return 2   # Micro cap

    def calculate_take_profits(self, entry_price, stop_loss_price, tp_percentages):
        """
        Calculate take-profit levels and risk/reward ratios

        Args:
            entry_price: Entry price
            stop_loss_price: Stop-loss price
            tp_percentages: List of TP percentages, e.g., [20, 40, 60]

        Returns:
            List of TP levels with R:R ratios
        """
        risk_per_unit = abs(entry_price - stop_loss_price)

        tp_levels = []
        for pct in tp_percentages:
            tp_price = entry_price * (1 + pct / 100)
            reward_per_unit = tp_price - entry_price
            rr_ratio = reward_per_unit / risk_per_unit if risk_per_unit > 0 else 0

            tp_levels.append({
                'percentage': pct,
                'price': tp_price,
                'risk_reward': rr_ratio
            })

        return tp_levels

    def calculate_portfolio_heat(self, open_positions):
        """
        Calculate total portfolio risk (heat) from all open positions

        Args:
            open_positions: List of dicts with 'entry_price', 'stop_loss', 'quantity'

        Returns:
            dict with heat metrics
        """
        total_risk = 0

        for pos in open_positions:
            risk_per_unit = abs(pos['entry_price'] - pos['stop_loss'])
            position_risk = risk_per_unit * pos['quantity']
            total_risk += position_risk

        heat_pct = (total_risk / self.account_size) * 100

        status = "🟢 LOW" if heat_pct < 10 else "🟡 MEDIUM" if heat_pct < 20 else "🔴 HIGH"

        return {
            'total_risk_usd': total_risk,
            'heat_pct': heat_pct,
            'status': status,
            'max_heat_pct': 30,  # Maximum allowed
            'warning': heat_pct > 30
        }

    def print_position_details(self, position_data):
        """Pretty print position sizing details"""
        print("\n" + "="*80)
        print("📊 POSITION SIZING CALCULATOR")
        print("="*80)

        if 'error' in position_data:
            print(f"\n❌ ERROR: {position_data['error']}")
            return

        print(f"\n💰 ACCOUNT DETAILS:")
        print(f"   Account Size: ${self.account_size:,.2f}")
        print(f"   Risk Per Trade: {self.risk_per_trade_pct}% (${self.risk_amount:,.2f})")

        print(f"\n📈 POSITION DETAILS:")
        print(f"   Entry Price: ${position_data['entry_price']:.6f}")
        print(f"   Stop-Loss: ${position_data['stop_loss_price']:.6f} ({position_data['stop_loss_pct']:+.2f}%)")
        print(f"   Quantity: {position_data['quantity']:.2f}")
        print(f"   Position Value: ${position_data['position_value']:,.2f}")
        print(f"   Position Size: {position_data['position_pct']:.2f}% of account")

        if position_data['warnings']:
            print(f"\n⚠️ WARNINGS:")
            for warning in position_data['warnings']:
                print(f"   {warning}")

        print("\n" + "="*80)

def interactive_calculator():
    """Interactive position sizing calculator"""
    print("\n" + "="*80)
    print("🎯 INTERACTIVE POSITION SIZING CALCULATOR")
    print("="*80)

    # Get account details
    account_size = float(input("\nEnter account size (USD): $"))
    risk_pct = float(input("Risk per trade (%, default 2): ") or "2")

    sizer = PositionSizer(account_size, risk_pct)

    while True:
        print("\n" + "-"*80)
        token = input("\nToken symbol (or 'q' to quit): ").strip().upper()

        if token == 'Q':
            break

        entry_price = float(input("Entry price: $"))
        stop_loss = float(input("Stop-loss price: $"))
        rank_input = input("Token rank (optional, press Enter to skip): ").strip()
        token_rank = int(rank_input) if rank_input else None

        # Calculate position
        position = sizer.calculate_position(entry_price, stop_loss, token_rank)
        sizer.print_position_details(position)

        # Calculate take-profits
        tp_input = input("\nEnter take-profit percentages (comma-separated, e.g., 20,40,60): ").strip()
        if tp_input:
            tp_percentages = [float(x.strip()) for x in tp_input.split(',')]
            tp_levels = sizer.calculate_take_profits(entry_price, stop_loss, tp_percentages)

            print(f"\n🎯 TAKE-PROFIT LEVELS:")
            for i, tp in enumerate(tp_levels, 1):
                print(f"   TP{i}: ${tp['price']:.6f} (+{tp['percentage']:.0f}%) - R:R = 1:{tp['risk_reward']:.2f}")

        # Check if want to add another
        cont = input("\nCalculate another position? (y/n): ").strip().lower()
        if cont != 'y':
            break

    print("\n👋 Position sizing complete!")

def quick_calc_examples():
    """Show example calculations"""
    print("\n" + "="*80)
    print("📚 POSITION SIZING EXAMPLES")
    print("="*80)

    # Example 1: SENT trade
    print("\n--- EXAMPLE 1: SENT (Mid-cap) ---")
    sizer = PositionSizer(account_size=10000, risk_per_trade_pct=2)
    pos = sizer.calculate_position(
        entry_price=0.04028,
        stop_loss_price=0.03625,
        token_rank=201
    )
    sizer.print_position_details(pos)

    tp_levels = sizer.calculate_take_profits(0.04028, 0.03625, [20, 40, 60])
    print(f"\n🎯 TAKE-PROFIT LEVELS:")
    for i, tp in enumerate(tp_levels, 1):
        print(f"   TP{i}: ${tp['price']:.6f} (+{tp['percentage']:.0f}%) - R:R = 1:{tp['risk_reward']:.2f}")

    # Example 2: Higher rank token
    print("\n\n--- EXAMPLE 2: Micro-cap Token ---")
    pos2 = sizer.calculate_position(
        entry_price=0.001,
        stop_loss_price=0.0009,
        token_rank=1200
    )
    sizer.print_position_details(pos2)

    # Portfolio heat example
    print("\n\n--- EXAMPLE 3: Portfolio Heat Check ---")
    open_positions = [
        {'entry_price': 0.04028, 'stop_loss': 0.03625, 'quantity': 4963},
        {'entry_price': 17.94, 'stop_loss': 16.20, 'quantity': 40},
        {'entry_price': 0.038, 'stop_loss': 0.034, 'quantity': 1250},
    ]

    heat = sizer.calculate_portfolio_heat(open_positions)
    print(f"\n🔥 PORTFOLIO HEAT:")
    print(f"   Total Risk: ${heat['total_risk_usd']:,.2f}")
    print(f"   Heat: {heat['heat_pct']:.2f}% {heat['status']}")
    print(f"   Max Allowed: {heat['max_heat_pct']}%")
    if heat['warning']:
        print(f"   ⚠️ WARNING: Portfolio heat too high! Reduce position sizes.")

# ==================== ACTION PLAN INTEGRATION ====================

def action_plan_positions():
    """Calculate positions for tokens in action plan"""
    print("\n" + "="*80)
    print("🎯 ACTION PLAN POSITION SIZING")
    print("="*80)

    account_size = float(input("\nEnter your account size (USD): $"))
    sizer = PositionSizer(account_size, risk_per_trade_pct=2)

    # SENT position (from action plan)
    print("\n\n--- SENT (Short-term Momentum) ---")
    sent_pos = sizer.calculate_position(
        entry_price=0.04028,
        stop_loss_price=0.03625,  # -10%
        token_rank=201
    )
    sizer.print_position_details(sent_pos)

    sent_tps = sizer.calculate_take_profits(0.04028, 0.03625, [20, 40, 60])
    print(f"\n🎯 TAKE-PROFIT STRATEGY:")
    print(f"   TP1: ${sent_tps[0]['price']:.6f} (+20%) - Sell 30% - R:R = 1:{sent_tps[0]['risk_reward']:.2f}")
    print(f"   TP2: ${sent_tps[1]['price']:.6f} (+40%) - Sell 40% - R:R = 1:{sent_tps[1]['risk_reward']:.2f}")
    print(f"   TP3: ${sent_tps[2]['price']:.6f} (+60%) - Sell 30% - R:R = 1:{sent_tps[2]['risk_reward']:.2f}")

    # DCR position
    print("\n\n--- DCR (Infrastructure) ---")
    dcr_pos = sizer.calculate_position(
        entry_price=17.94,
        stop_loss_price=16.20,  # -9%
        token_rank=194
    )
    sizer.print_position_details(dcr_pos)

    dcr_tps = sizer.calculate_take_profits(17.94, 16.20, [20, 39])
    print(f"\n🎯 TAKE-PROFIT STRATEGY:")
    print(f"   TP1: ${dcr_tps[0]['price']:.6f} (+20%) - Sell 40% - R:R = 1:{dcr_tps[0]['risk_reward']:.2f}")
    print(f"   TP2: ${dcr_tps[1]['price']:.6f} (+39%) - Sell 30% - R:R = 1:{dcr_tps[1]['risk_reward']:.2f}")
    print(f"   Hold 30% for long-term infrastructure thesis")

    # Portfolio heat with both positions
    print("\n\n--- COMBINED PORTFOLIO HEAT ---")
    combined_positions = [
        {
            'entry_price': sent_pos['entry_price'],
            'stop_loss': sent_pos['stop_loss_price'],
            'quantity': sent_pos['quantity']
        },
        {
            'entry_price': dcr_pos['entry_price'],
            'stop_loss': dcr_pos['stop_loss_price'],
            'quantity': dcr_pos['quantity']
        }
    ]

    heat = sizer.calculate_portfolio_heat(combined_positions)
    print(f"\n🔥 TOTAL PORTFOLIO HEAT:")
    print(f"   Combined Risk: ${heat['total_risk_usd']:,.2f}")
    print(f"   Heat: {heat['heat_pct']:.2f}% {heat['status']}")
    print(f"   Max Allowed: {heat['max_heat_pct']}%")

    if not heat['warning']:
        print(f"\n✅ Portfolio heat is within safe limits. These positions are approved.")
    else:
        print(f"\n⚠️ Portfolio heat too high! Reduce position sizes or wait for one trade to close.")

    print("\n" + "="*80)

# ==================== MAIN ====================

if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                        POSITION SIZING CALCULATOR                             ║
║                    Calculate Risk-Adjusted Position Sizes                     ║
╚═══════════════════════════════════════════════════════════════════════════════╝

QUICK START:

1. Interactive Calculator:
   python position_sizer.py

2. Action Plan Positions:
   >>> from position_sizer import action_plan_positions
   >>> action_plan_positions()

3. See Examples:
   >>> from position_sizer import quick_calc_examples
   >>> quick_calc_examples()

4. Custom Calculation:
   >>> from position_sizer import PositionSizer
   >>> sizer = PositionSizer(account_size=10000, risk_per_trade_pct=2)
   >>> pos = sizer.calculate_position(entry_price=0.04, stop_loss_price=0.036, token_rank=200)
   >>> sizer.print_position_details(pos)

══════════════════════════════════════════════════════════════════════════════
""")

    # Show menu
    print("\nSelect mode:")
    print("1. Interactive Calculator")
    print("2. Action Plan Positions")
    print("3. Show Examples")
    print("4. Exit")

    choice = input("\nEnter choice (1-4): ").strip()

    if choice == '1':
        interactive_calculator()
    elif choice == '2':
        action_plan_positions()
    elif choice == '3':
        quick_calc_examples()
    else:
        print("\n👋 Goodbye!")
