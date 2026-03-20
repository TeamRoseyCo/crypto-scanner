# Python Scanners

Cryptocurrency market scanners using the CoinGecko API to identify trading opportunities.

## Directory Structure

```
python-scanners/
├── scanners/           # Core scanner scripts
│   ├── rsps_prime_key.py        # Prime Key Scanner
│   ├── pump_hunter.py           # Pump detection scanner
│   └── meme_ignition_radar.py   # Meme coin momentum tracker
├── portfolio/          # Position tracking tools
│   ├── portfolio_tracker.py     # Interactive position manager
│   └── position_sizer.py        # Position size calculator
├── utilities/          # Helper scripts
│   ├── fetch_specific_tokens.py # Fetch specific token data
│   └── volume_spike_24h.py      # Volume spike detector
└── launchers/          # Batch file runners
    ├── run_prime_key.bat
    ├── run_prime_key_bg.bat
    ├── run_momentum.bat
    └── run_fetch_tokens.bat
```

## Scanner Details

### Prime Key Scanner (`rsps_prime_key.py`)

**Purpose**: Identify coins with optimal risk/reward setups ready for entry.

**Filtering Criteria**:
1. **Technical Setup**
   - RSI: 30-60 (sweet spot for entries)
   - MACD: Bullish or neutral
   - Volume: Above average

2. **Market Context**
   - BTC Correlation: Within acceptable range
   - Market cap: $10M - $5B (sweet spot)
   - Liquidity: Sufficient daily volume

3. **Momentum Layers**
   - 1-day: Positive or neutral
   - 7-day: Showing strength
   - 30-day: Establishing trend
   - 60-day: Long-term context

**Configuration**: Edit `THRESHOLDS` and `WEIGHTS` dictionaries at top of file

**Output**:
- `../../outputs/scanner-results/rspS_prime_key_pool.csv` (filtered results)
- `../../outputs/scanner-results/rspS_prime_key_detailed.csv` (all metrics)

**Cache**: `../../cache/prime_key/` (stores 60-day historical data)

---

### Pump Hunter (`pump_hunter.py`)

**Purpose**: Detect coins in early pump phases across multiple timeframes.

**Scoring System**:
Coins are scored on 8 momentum layers:
- **Intraday**: 1h, 6h, 24h
- **Short-term**: 3d, 7d
- **Medium-term**: 14d, 30d
- **Long-term**: 60d, 90d

Each layer that shows significant positive momentum adds to the pump score.

**Configuration**: Edit `THRESHOLDS` dictionary:
- `MIN_PUMP_SCORE`: Minimum layers required (default: 4)
- Timeframe thresholds: Adjust % gains needed for each layer

**Output**: `../../outputs/scanner-results/pump_hunter_results.csv`

**Cache**: `../../cache/pump_hunter/`

---

### Meme Ignition Radar (`meme_ignition_radar.py`)

**Purpose**: Track meme coins showing early momentum signals before major moves.

**Detection Logic**:
1. **New Launches** (< 30 days old)
   - Growing volume
   - Community interest signals
   - Price stability

2. **Existing Memes** (> 30 days old)
   - Re-ignition patterns
   - Volume resurgence
   - Price breakouts

**Filters**:
- Market cap range: $100K - $500M
- Minimum liquidity requirements
- Quality indicators (avoids obvious scams)

**Output**: `../../outputs/scanner-results/meme_ignition_pre_momentum.csv`

---

## Portfolio Tools

### Portfolio Tracker (`portfolio_tracker.py`)

**Features**:
- Add/remove positions with entry price and quantity
- Track unrealized P&L in real-time
- Set price alerts (above/below targets)
- Log trades for historical analysis
- Import candidates directly from scanner results

**Commands**:
```
1. View Portfolio       - See all positions and P&L
2. Add Position         - Enter a new trade
3. Close Position       - Exit a trade (logs to history)
4. Set Alert           - Create price notifications
5. View Alerts         - See active alerts
6. Import from Scanner - Load top scanner picks
7. View Trade History  - Review past trades
```

**Data Files**:
- `../../outputs/portfolio/portfolio_positions.json`
- `../../outputs/portfolio/price_alerts.json`
- `../../outputs/portfolio/trades_history.csv`

---

### Position Sizer (`position_sizer.py`)

**Purpose**: Calculate position sizes based on risk management rules.

**Inputs**:
- Account size
- Risk percentage (e.g., 1-2% per trade)
- Entry price
- Stop loss price

**Output**: Optimal position size in coins and USD

---

## Utilities

### fetch_specific_tokens.py

Fetch detailed market data for a specific list of tokens.

**Usage**: Edit `SYMBOLS` list in the file, then run:
```bash
cd utilities
python fetch_specific_tokens.py
```

**Output**: `../../outputs/market-data/specific_tokens_market_data.csv`

---

### volume_spike_24h.py

Detect coins with unusual 24h volume compared to 7-day average.

**Threshold**: 5x normal volume

**Output**: `../../outputs/scanner-results/volume_spike_coins.csv`

---

## Running Scanners

### Method 1: Batch Files (Recommended)

```bash
cd launchers
run_prime_key.bat      # Run Prime Key Scanner
run_momentum.bat       # Run Meme Ignition Radar
run_fetch_tokens.bat   # Fetch specific tokens
```

### Method 2: Direct Python

```bash
cd scanners
python rsps_prime_key.py
python pump_hunter.py
python meme_ignition_radar.py
```

---

## Configuration

### API Rate Limits

CoinGecko free tier limits:
- 50 calls/minute
- Scanners include built-in delays to stay within limits

### Cache Management

- Cache expires: Configurable per scanner (typically 60 days)
- Location: `../../cache/`
- To clear cache: Delete cache directory (will rebuild on next run)

### Customization

Each scanner has configurable thresholds at the top of the file:

**Example (rsps_prime_key.py)**:
```python
THRESHOLDS = {
    'technical': {
        'rsi_min': 30,
        'rsi_max': 60,
        'volume_spike': 1.5,
    },
    'market_context': {
        'mcap_min': 10_000_000,
        'mcap_max': 5_000_000_000,
    }
}
```

---

## Workflow Example

### Daily Scanning Routine

1. **Morning Scan**
   ```bash
   cd launchers
   run_prime_key.bat
   ```

2. **Review Results**
   - Open `outputs/scanner-results/rspS_prime_key_pool.csv`
   - Sort by composite score
   - Research top 5-10 candidates

3. **Track Positions**
   ```bash
   cd ../portfolio
   python portfolio_tracker.py
   ```
   - Import top candidates
   - Set entry alerts
   - Monitor existing positions

4. **Execute Trades**
   - Enter positions when alerts trigger
   - Log entries in portfolio tracker
   - Set stop losses

5. **End of Day**
   - Check portfolio P&L
   - Adjust alerts if needed
   - Review trade history

---

## Dependencies

```bash
pip install requests pandas pathlib
```

**Required**:
- `requests` - API calls
- `pandas` - Data manipulation
- `pathlib` - File path handling

**Optional**:
- Virtual environment (recommended for isolation)

---

## Troubleshooting

### Scanner runs slow on first execution
- **Expected**: Cache is building
- **Solution**: Subsequent runs will be fast

### API rate limit errors
- **Cause**: Too many requests
- **Solution**: Scanners have built-in delays. If still hitting limits, increase delay in code.

### Cache files not found
- **Cause**: Cache directory doesn't exist
- **Solution**: Scripts auto-create cache dirs. If error persists, manually create `../../cache/prime_key/` etc.

### CSV outputs not found
- **Cause**: Output directory doesn't exist
- **Solution**: Ensure `../../outputs/scanner-results/` exists

---

## Best Practices

1. **Run scanners daily**: Markets change quickly
2. **Review all candidates**: Don't blindly follow scanner output
3. **Use risk management**: Position sizer helps prevent overexposure
4. **Track everything**: Portfolio tracker provides valuable trade history
5. **Adjust thresholds**: Customize scanners based on market conditions
6. **Clear cache weekly**: Fresh data prevents stale signals

---

## Future Enhancements

Potential improvements:
- Real-time alerts via Telegram/Discord
- Web dashboard for scanner results
- Backtesting framework
- ML-based signal enhancement
- Exchange API integration for auto-trading

---

## License

Private project - All rights reserved.
