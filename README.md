# robloxmarketanalysis

Market trend analysis tool for Roblox limited items. It discovers limiteds from Rolimon's deals page, maintains a watchlist, and logs price snapshots over time so you can analyze trends.

## Features
- Auto-discovers limited items from Rolimon's deals page
- Persists a watchlist to `watchlist.csv`
- Logs time-series price snapshots to `prices.csv`
- Prints a 24-hour trend summary each cycle

## Setup
```bash
pip install selenium webdriver-manager beautifulsoup4
```

## Usage
```bash
python market_trends.py
```

## Options
- `--poll 600` polling interval in seconds (default 600)
- `--refresh 600` browser refresh interval in seconds (default 600)
- `--no-headless` show the browser window
- `--watchlist watchlist.csv` watchlist file path
- `--prices prices.csv` price history file path
- `--max-new 50` maximum new items to add per run
- `--discover-only` only update the watchlist and skip price logging

## Environment
This tool does not require a `.env` file or Roblox cookies. It only reads public market data.

## License
MIT

## Outputs
- `watchlist.csv` contains discovered items (id, title, URLs, timestamps)
- `prices.csv` contains time-series snapshots (timestamp, item id, price)
