# Wavetrend + ATR HEMA Strategy - Testing Guide

## 🎯 Goal: Improve Cobra Metrics with ATR HEMA Trend Filter

**Target Improvements:**
- Intra-trade Max DD: -47.2% → **-25% to -35%**
- Net Profit L/S Ratio: 3.85 → **1.8 to 2.5**
- Sharpe Ratio: 1.45 → **1.7 to 2.0**
- Sortino Ratio: 1.87 → **2.2 to 2.8**

**Maintain:**
- Profit Factor: ~4.5 to 6.0
- Win Rate: >55%
- Trades: 40-60

---

## 📊 What I Built

### **Strategy Components:**

**1. Wavetrend Oscillator (LazyBear)** - Primary Signal
- Detects crossovers from oversold/overbought zones
- Provides entry timing
- Same settings as your original

**2. ATR HEMA (SeerQuant)** - Trend Filter
- Hull Moving Average (fast, smooth)
- ATR-adjusted bands (adapts to volatility)
- Filters trades to trend direction only

### **Logic:**
```
LONG = Wavetrend long signal + Price > ATR HEMA
SHORT = Wavetrend short signal + Price < ATR HEMA
```

**Always in position:** Flips from long to short when both indicators agree.

---

## 🔧 Parameter Settings

### **Default Settings (Start Here):**

**Wavetrend (Same as Original):**
- Channel Length: 28
- Average Length: 35
- OB Level 2: 53
- OS Level 2: -53
- Neutral Zone Only: ✅ True

**ATR HEMA (Recommended):**
- **HEMA Length: 50** (Medium-term trend)
- **ATR Length: 14** (Standard volatility period)
- **ATR Multiplier: 1.5** (Band width - adjust for sensitivity)
- **Enable Filter: ✅ True**

---

## 📈 How to Test

### **Step 1: Copy to TradingView**
1. Copy all code from `btc_strategy_WT_ATRHEMA.txt`
2. Open TradingView
3. Pine Editor → New indicator → Paste code
4. Save as "BTC Wavetrend + ATR HEMA"

### **Step 2: Apply to BTC 1D Chart**
1. Symbol: BTCUSD or BTCUSDT
2. Timeframe: 1D (daily)
3. Add the strategy to chart

### **Step 3: Configure Settings**
1. Click strategy name → Settings
2. Properties tab:
   - Initial Capital: $10,000
   - Order Size: 100% of equity (already set)
   - Pyramiding: 0
3. Inputs tab:
   - Backtest Start: Jan 1, 2018
   - Enable ATR HEMA Filter: ✅ ON
   - All other defaults

### **Step 4: Run Backtest**
1. Strategy Tester panel (bottom)
2. Wait for backtest to complete
3. View Cobra metrics table (on chart)
4. Screenshot the metrics

---

## 🎨 Visual Features

### **On Chart:**
1. **ATR HEMA Line:**
   - Green = Bullish trend
   - Red = Bearish trend
   - Thick line = Main HEMA

2. **ATR Bands:**
   - Upper/Lower bands (thin lines)
   - Shaded area between bands
   - Width adapts to volatility

3. **Background Colors:**
   - Light green = Wavetrend long signal
   - Light red = Wavetrend short signal
   - Darker green/red = Confirmed trade (WT + ATR HEMA agree)

4. **Debug Table (Top Left):**
   - Shows each indicator status
   - Final combined signal
   - Current position

---

## 🧪 Testing Scenarios

### **Test 1: Default Settings (Recommended First)**
```
HEMA Length: 50
ATR Length: 14
ATR Multiplier: 1.5
Enable Filter: TRUE
```
**Expected:** Balanced filtering, ~45-60 trades

---

### **Test 2: Faster HEMA (More Trades)**
```
HEMA Length: 30
ATR Length: 14
ATR Multiplier: 1.5
```
**Expected:** More trades (~60-70), less filtering, might not reduce DD as much

---

### **Test 3: Slower HEMA (Fewer Trades)**
```
HEMA Length: 80
ATR Length: 14
ATR Multiplier: 1.5
```
**Expected:** Fewer trades (~30-40), stricter filtering, lower DD but might reduce profit

---

### **Test 4: Tighter Bands (Stricter Filter)**
```
HEMA Length: 50
ATR Length: 14
ATR Multiplier: 1.0  ⬅️ Reduced
```
**Expected:** More restrictive, requires price closer to HEMA, fewer counter-trend trades

---

### **Test 5: Wider Bands (Looser Filter)**
```
HEMA Length: 50
ATR Length: 14
ATR Multiplier: 2.0  ⬅️ Increased
```
**Expected:** More permissive, allows more trades, might not filter enough

---

### **Test 6: Disable ATR HEMA (Baseline)**
```
Enable Filter: FALSE
```
**Purpose:** Compare to original Wavetrend-only to see ATR HEMA's isolated impact

---

## 📊 Results Tracking

Use this template:

```
TEST #: _____
SETTINGS: HEMA=___ ATR=___ Mult=___

COBRA METRICS:
─────────────────────────────
Equity Max DD:        _____%
Intra-trade Max DD:   _____%  ⬅️ Main target
Sortino Ratio:        _____
Sharpe Ratio:         _____
Profit Factor:        _____
Profitable %:         _____%
Trades:               _____
Net Profit %:         _____%
Net Profit L/S Ratio: _____  ⬅️ Main target

VERDICT: ✅ Improvement / ❌ Worse / ⚠️ Mixed
NOTES: _________________________
```

---

## ✅ Success Criteria

**ATR HEMA is WORKING if:**

✅ **Intra-trade DD improved:**
- Original: -47.2%
- Target: <-35%
- Acceptable: <-40%

✅ **L/S Ratio balanced:**
- Original: 3.85
- Target: 1.5-2.5
- Acceptable: <3.0

✅ **Core metrics maintained:**
- Profit Factor: >3.5
- Win Rate: >50%
- Trades: >35
- Net Profit: >40%

✅ **Sharpe/Sortino improved:**
- Sharpe: >1.5
- Sortino: >2.0

---

## ❌ If ATR HEMA Makes It Worse

**Troubleshooting:**

### **Problem: Too few trades (<30)**
**Solution:**
- Reduce HEMA Length (50 → 40 → 30)
- Increase ATR Multiplier (1.5 → 2.0)
- This makes filter less restrictive

### **Problem: Intra-trade DD still high (>-40%)**
**Solution:**
- Increase HEMA Length (50 → 60 → 80)
- Decrease ATR Multiplier (1.5 → 1.0)
- This makes filter more restrictive

### **Problem: Win rate dropped significantly**
**Solution:**
- ATR HEMA might be filtering out the good trades
- Try different HEMA length (30, 40, 60, 80)
- Or use ATR HEMA differently (we can pivot to another approach)

### **Problem: Everything got worse**
**Solution:**
- Similar to the EMA200 disaster
- ATR HEMA might not be compatible with Wavetrend for BTC 1D
- We'll try a different Indicator 2 instead

---

## 🚀 After Testing

### **Scenario A: ATR HEMA Works! ✅**
If metrics improve (especially intra-trade DD and L/S ratio):
1. Lock in the optimal parameters
2. Move to adding Indicator 3 (Volume/Momentum)
3. Build the 5-indicator system

### **Scenario B: ATR HEMA Doesn't Help ❌**
If metrics worsen or no improvement:
1. Try parameter optimization first
2. If still bad, we pivot to different Indicator 2:
   - Supertrend
   - Ichimoku Cloud
   - VWAP
   - Or your choice

### **Scenario C: Mixed Results ⚠️**
If some metrics improve, others worsen:
1. Decide what's most important (intra-trade DD? L/S ratio?)
2. Optimize for primary goal
3. Might need additional indicators to balance

---

## 📝 What to Share

After testing, please share:

1. **Screenshot of Cobra Metrics table**
2. **Which settings you used** (HEMA length, ATR mult, etc.)
3. **Your observations:**
   - Did intra-trade DD improve?
   - Did L/S ratio balance?
   - How many trades?
   - Visual check: Does ATR HEMA line make sense on chart?

---

## 💡 Quick Tips

1. **Start with Test 1 (default settings)** - Don't over-optimize immediately
2. **Check the visual chart** - Does the ATR HEMA line follow trend logically?
3. **Compare to original** - Run Test 6 (filter OFF) to see baseline
4. **One change at a time** - Don't change multiple parameters at once
5. **Trust the process** - If this doesn't work, we have 10+ other indicators to try

---

**Ready to test! Run Test 1 (defaults) first and share the Cobra metrics screenshot.**

**Good luck! 🚀**
