# Polymarket Smart Money / Insider-Pattern Radar

A local anomaly-detection pipeline that monitors Polymarket for suspicious "smart money" activity and sends explainable Telegram alerts.

---

## What it does

- Monitors high-insider-risk markets (politics, geopolitics, elections, M&A, sanctions)
- Detects anomalous trade patterns using only **public data**
- Sends **explainable Telegram alerts** — not just "suspicious", but *why*
- No auto-betting. Decision support only.

---

## Architecture

```
Polymarket API  ──►  Collector ──►  DB (Postgres)
Polygonscan API ──►  Chain Watcher ──►  DB

DB ──► Scorer ──► Summarizer ──► Notifier ──► Telegram
```

---

## Quickstart

### 1. Prerequisites
- Docker + Docker Compose
- Python 3.11+
- Telegram bot token + your chat ID
- Polygonscan API key (free at polygonscan.com)

### 2. Configure
```bash
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your credentials
```

### 3. Start the database
```bash
docker-compose up -d postgres
```

### 4. Install dependencies
```bash
pip install -r requirements.txt
```

### 5. Run migrations
```bash
python scripts/migrate.py
```

### 6. Start the radar
```bash
python -m src.main
```

---

## Configuration (`config/config.yaml`)

| Key | Description |
|-----|-------------|
| `telegram.bot_token` | From @BotFather |
| `telegram.chat_id` | Your DM chat ID |
| `polygonscan.api_key` | Free from polygonscan.com |
| `scoring.strong_threshold` | Score ≥ this → STRONG alert (default 70) |
| `scoring.medium_threshold` | Score ≥ this → MEDIUM alert (default 55) |
| `polling.interval_seconds` | How often to poll trades (default 60) |
| `funding.lookback_minutes` | How far back to look for funding events (default 180) |

---

## Signal Score breakdown

| Feature | Max Points | Description |
|---------|-----------|-------------|
| SizeScore | 30 | Larger notional bet |
| TimingScore | 20 | Closer to market deadline |
| WalletHistoryScore | 15 | New/low-activity wallet |
| FundingScore | 15 | Funded shortly before bet |
| ImpactScore | 10 | Large trade, small price move |
| ClusterScore | 10 | Multiple wallets, same funder |

**STRONG** ≥ 70 | **MEDIUM** ≥ 55

---

## Alert example

```
🚨 STRONG SIGNAL [Score: 82/100]
📊 Will Microsoft acquire Activision by Dec 2025?

• Large entry: ~$94,200 on YES
• Placed within ~3h of regulatory deadline
• Wallet appears new (< 7 days old)
• Wallet funded +$150,000 USDC ~47 min before trade
• High size with low immediate price impact (0.3%)

Trader: 0xabc...def
Tx: 0x123...789
Price: 0.71 → YES
Time: 2025-12-01 14:23 UTC
```

---

## Project structure

```
src/
  adapters/
    polymarket.py     # REST + GraphQL adapter
    polygonscan.py    # USDC transfer watcher
  core/
    scorer.py         # Signal scoring engine
    summarizer.py     # Human-readable reasons
  db/
    models.py         # SQLAlchemy models
    session.py        # DB session factory
    repository.py     # Data access layer
  notifications/
    telegram.py       # Alert sender
  main.py             # Pipeline orchestrator
config/
  config.yaml         # Your config (gitignored)
  config.example.yaml # Template
docker/
  init.sql            # DB schema
docker-compose.yml
requirements.txt
```

---

## Notes
- All data is public on-chain and via Polymarket APIs
- No private keys, no trading, no financial advice
- Thresholds are conservative by default — tune to your preference
"# polymarket-scanner" 
