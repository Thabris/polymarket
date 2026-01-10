# Polymarket Monitor

A Python-based platform to monitor Polymarket betting markets, detect significant events (large inflows, odds changes, popular bets), and deliver real-time alerts.

## Features

### Phase 1: Monitoring & Alerts (Current)
- **Real-time price monitoring** via WebSocket connection
- **Volume spike detection** - alerts when trading volume exceeds thresholds
- **Whale activity tracking** - alerts on large individual trades
- **Price change alerts** - notifications when odds move significantly
- **Desktop notifications** (Windows toast)
- **REST API** for data access
- **WebSocket streaming** for real-time data
- **System tray** integration for easy control

### Phase 2: Trading Strategies (Future)
- Strategy framework with backtesting
- Automated order execution
- Position management

## Quick Start

### 1. Install Dependencies

```bash
cd polymarket
pip install -r requirements.txt
```

### 2. Configure Credentials

Option A: Use environment variables (development)
```bash
copy .env.example .env
# Edit .env with your credentials
```

Option B: Use Windows Credential Manager (secure)
```bash
python scripts/setup_credentials.py
```

### 3. Initialize Database

```bash
# Run migrations
alembic upgrade head
```

### 4. Run the Application

```bash
python main.py
```

The application will start:
- API server at http://127.0.0.1:8000
- API documentation at http://127.0.0.1:8000/docs
- System tray icon for quick control

## Command Line Options

```bash
python main.py              # Full application
python main.py --api-only   # API server only
python main.py --no-tray    # Without system tray
python main.py --port 9000  # Custom port
```

## API Endpoints

### Markets
- `GET /api/markets` - List markets
- `GET /api/markets/search?q=bitcoin` - Search markets
- `GET /api/markets/trending` - Trending markets
- `GET /api/markets/{id}` - Market details
- `GET /api/markets/{id}/prices` - Price history

### Alerts
- `GET /api/alerts` - List alerts
- `GET /api/alerts/unread` - Unread alerts
- `POST /api/alerts/{id}/acknowledge` - Acknowledge alert
- `GET /api/alerts/config` - Get alert thresholds
- `PUT /api/alerts/config` - Update thresholds

### Watchlist
- `GET /api/markets/watchlist` - Get watchlist
- `POST /api/markets/watchlist` - Add to watchlist
- `DELETE /api/markets/watchlist/{id}` - Remove from watchlist

### WebSocket
- `WS /ws/stream` - Real-time data stream

### System
- `GET /api/status` - System status
- `GET /api/health` - Health check
- `GET /api/settings` - Current settings

## Configuration

### Alert Thresholds

Edit via API or set in `.env`:

| Setting | Default | Description |
|---------|---------|-------------|
| PRICE_CHANGE_THRESHOLD_PCT | 5.0 | Price change % to trigger alert |
| PRICE_CHANGE_WINDOW_MINUTES | 5 | Time window for price detection |
| VOLUME_SPIKE_THRESHOLD_USD | 10000 | Volume in USD to trigger alert |
| VOLUME_SPIKE_WINDOW_MINUTES | 5 | Time window for volume detection |
| WHALE_TRADE_THRESHOLD_USD | 5000 | Single trade size for whale alert |

## Project Structure

```
polymarket/
├── api/                 # FastAPI application
├── config/              # Settings and credentials
├── core/                # Core models and events
├── data/                # API clients and storage
├── db/                  # Database models and migrations
├── monitors/            # Alert monitors
├── notifications/       # Notification delivery
├── scripts/             # Setup scripts
├── strategies/          # Trading strategies (Phase 2)
├── tests/               # Test suite
├── ui/                  # System tray
└── main.py              # Entry point
```

## Development

### Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

### Code Formatting

```bash
black .
ruff check .
```

## Security Notes

- Never commit `.env` file or credentials
- Use `keyring` for production credential storage
- API keys are stored in Windows Credential Manager
- Private keys are required only for trading (Phase 2)

## License

MIT
