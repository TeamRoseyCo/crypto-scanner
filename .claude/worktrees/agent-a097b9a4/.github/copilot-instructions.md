# Ankh Project - AI Coding Assistant Instructions

## Project Overview
Ankh is a hybrid crypto analytics platform combining:
- **Frontend**: Next.js 15 app with countdown timer and authentication
- **Backend**: Python scripts for crypto market data analysis and momentum detection
- **APIs**: CoinGecko for market data, Bybit for trading integration
- **Data**: MongoDB storage with CSV-cached historical price data

## Architecture Patterns
- **Hybrid Stack**: JavaScript/TypeScript frontend + Python data processing
- **Data Flow**: CoinGecko API → Python analysis → CSV cache → MongoDB → Next.js display
- **Authentication**: NextAuth.js with custom auth pages
- **Styling**: Tailwind CSS with DaisyUI components

## Key Directories & Files
- `app/`: Next.js app router (pages, API routes, auth)
- `TRW/`: Python momentum analysis scripts (e.g., `rsps_momentum_ignition.py`)
- `cache_*/`: CSV historical data (60-day OHLC for 500+ tokens)
- `scripts/`: TypeScript utilities (Bybit API validation)
- `config/`: App configuration (target dates, settings)

## Development Workflows
### Python Scripts
- Activate venv: `& ".venv\Scripts\Activate.ps1"`
- Run momentum analysis: `python TRW/rsps_momentum_ignition.py`
- Use bat files for convenience: `run_momentum.bat`

### Frontend Development
- Start dev server: `npm run dev`
- Build: `npm run build`
- Test Bybit connection: `npm run test-bybit`

### Data Processing
- Scripts fetch top gainers from CoinGecko
- Filter by volume ($500k+), price change (10-1000%), market cap rank (>21)
- Cache results in `rspS_momentum_ignition_pool.csv`
- Historical data in `cache_prime_key/` and `cache_momentum/`

## Coding Conventions
- **Python**: Pandas for data manipulation, requests for API calls
- **Next.js**: App router, server components, client components for interactivity
- **Error Handling**: Try/catch with console logging, graceful degradation
- **Environment**: `.env.local` for API keys (Bybit, MongoDB)
- **Imports**: Relative paths with `@/` aliases in Next.js

## Common Patterns
- **API Calls**: Use `requests` in Python, fetch in JS
- **Data Filtering**: Short-circuit checks for performance
- **CSV Output**: `df.to_csv()` with index=False
- **Authentication**: `useSession()` hook in components
- **Styling**: Tailwind classes, DaisyUI components

## Integration Points
- **CoinGecko**: Market data, top gainers, BTC health check
- **Bybit**: Trading API (testnet mode in scripts)
- **MongoDB**: Data persistence for user/app state
- **NextAuth**: GitHub/other providers for login

## Deployment
- Vercel for frontend (auto-detects Next.js)
- Python scripts run locally/on server
- Target launch: July 6, 2025 (countdown timer)</content>
<parameter name="filePath">c:\Users\bruno\OneDrive\Ambiente de Trabalho\Workspace\ankh\.github\copilot-instructions.md