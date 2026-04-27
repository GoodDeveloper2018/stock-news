# Market Intel — Stock News Intelligence Dashboard

A full-stack web application that fetches live stock data and financial news from Yahoo Finance, then ranks each article using a 5-signal scoring algorithm designed to surface high-confidence, actionable information for traders and investors.

---

## Features

### Live Stock Data
- Real-time price quote (price, change, change %, volume, exchange)
- 5-day OHLC sparkline chart rendered via the HTML5 Canvas API
- Company name lookup for ~42 major tickers

### Intelligent News Ranking
Each article is scored out of 100 across five independent signals:

| Signal | Max Points | Logic |
|---|---|---|
| **Recency** | 30 | Full score if < 2 hours old; exponential decay with 24-hour half-life |
| **Source Authority** | 20 | Tier-based: Reuters/Bloomberg/WSJ (20), CNBC/MarketWatch/FT (15), others (8) |
| **Relevance** | 25 | Keyword density — ticker + company name mentions, 5 pts each (capped) |
| **Sentiment** | 15 | Net bullish/bearish word count from ~50 curated terms; baseline 7.5 pts |
| **Title Quality** | 10 | Penalizes clickbait (?, !, excess caps, short titles); rewards factual length |

Each article card shows a color-coded total score (High ≥ 65 / Mid 40–64 / Low < 40), a one-line reasoning summary, and an expandable signal breakdown.

### Watchlist
- Add/remove tickers to a persistent watchlist stored in SQLite
- Sidebar shows live price and change % for each saved ticker
- Click any watchlist entry to load that stock

### Trending Suggestions
- Right-panel suggestions ranked by search frequency (logged per session)
- Fallback defaults to popular tickers when no history exists

---

## Tech Stack

**Backend** — Python / Flask  
**Database** — SQLite via Flask-SQLAlchemy (watchlist, news cache, search log)  
**Frontend** — Vanilla JS, HTML5, CSS3 (no frameworks)  
**Data** — Yahoo Finance public RSS + JSON APIs (no API key required)

---

## Getting Started

```bash
# Install dependencies
pip install -r requirements.txt

# Run the development server
python app.py
```

Open `http://localhost:5000` in your browser.

---

## Project Structure

```
api-full-stack-project-arshct1/
├── app.py          # Flask routes + news ranking algorithm
├── models.py       # SQLAlchemy models (Watchlist, NewsCache, SearchLog)
├── index.html      # App shell
├── index.js        # Frontend logic (data fetching, rendering, chart)
├── index.css       # Dark theme styles
└── requirements.txt
```

---

## Potential Improvements

- [ ] User authentication and per-user watchlists
- [ ] Real-time price updates via WebSocket or polling
- [ ] Expanded ticker coverage beyond the ~42-entry company name map
- [ ] Replace Yahoo Finance scraping with a paid data provider (Alpha Vantage, Polygon.io) for reliability
- [ ] Sentiment analysis via NLP model instead of keyword lists
- [ ] Portfolio tracking (shares owned, cost basis, P&L)
- [ ] Price alert notifications
- [ ] Export watchlist / news digest to email or CSV
- [ ] Test suite for the scoring algorithm
- [ ] Deploy to a cloud host (Railway, Render, Fly.io)
