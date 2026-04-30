from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from models import db, Watchlist, NewsCache, SearchLog
import requests as http
import xml.etree.ElementTree as ET
import hashlib
import math
import re
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
CORS(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///stocknews.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

with app.app_context():
    db.create_all()


# ============================================================
# TICKER -> COMPANY NAME LOOKUP
# Used by the relevance scorer to search for company name
# keywords in article text, not just the ticker symbol.
# ============================================================
COMPANY_NAMES = {
    'AAPL': 'Apple', 'MSFT': 'Microsoft', 'GOOGL': 'Alphabet', 'GOOG': 'Alphabet',
    'AMZN': 'Amazon', 'META': 'Meta', 'TSLA': 'Tesla', 'NVDA': 'Nvidia',
    'NFLX': 'Netflix', 'AMD': 'AMD', 'INTC': 'Intel', 'ORCL': 'Oracle',
    'CRM': 'Salesforce', 'ADBE': 'Adobe', 'PYPL': 'PayPal', 'UBER': 'Uber',
    'LYFT': 'Lyft', 'SNAP': 'Snap', 'SPOT': 'Spotify', 'SHOP': 'Shopify',
    'ZM': 'Zoom', 'PLTR': 'Palantir', 'GME': 'GameStop', 'AMC': 'AMC',
    'BABA': 'Alibaba', 'NIO': 'NIO', 'JPM': 'JPMorgan', 'BAC': 'Bank of America',
    'GS': 'Goldman Sachs', 'MS': 'Morgan Stanley', 'WMT': 'Walmart',
    'DIS': 'Disney', 'BA': 'Boeing', 'GE': 'General Electric', 'F': 'Ford',
    'GM': 'General Motors', 'T': 'AT&T', 'VZ': 'Verizon', 'COIN': 'Coinbase',
    'RBLX': 'Roblox', 'SPY': 'S&P 500 ETF', 'QQQ': 'Nasdaq ETF',
    'IWM': 'Russell 2000 ETF', 'SQ': 'Block', 'TWTR': 'Twitter',
}

# ============================================================
# SOURCE AUTHORITY TIER LIST
# Maps partial source-name strings to authority scores (max 20).
# Tier 1 (20pts): Reuters, Bloomberg, WSJ — these outlets have
#   dedicated fact-checking pipelines, editorial standards, and
#   are the primary information sources that professional traders
#   monitor. A Reuters flash is higher-signal than a blog post.
# Tier 2 (15pts): CNBC, Forbes, MarketWatch, FT, Barron's —
#   strong financial coverage but with more opinion mixed in.
# Tier 3 (8pts): all others — default, no penalty, but no boost.
# ============================================================
SOURCE_TIERS = {
    'reuters': 20, 'bloomberg': 20, 'wsj': 20, 'wall street journal': 20,
    'cnbc': 15, 'forbes': 15, 'marketwatch': 15, 'financial times': 15,
    'ft.com': 15, 'barrons': 15, "barron's": 15, 'the motley fool': 12,
    'seeking alpha': 12, 'investopedia': 10,
}

# ============================================================
# SENTIMENT WORD LISTS
# Deliberately kept small (~20 words each) so the scorer is
# fast and interpretable — not trying to match a full NLP model,
# just give a directional signal that a human can audit.
# ============================================================
BULLISH_WORDS = {
    'surges', 'surge', 'rallies', 'rally', 'beats', 'beat', 'record',
    'growth', 'profit', 'upgrade', 'upgraded', 'outperform', 'buy',
    'strong', 'bullish', 'rises', 'rise', 'gains', 'gain', 'boosts',
    'boost', 'expands', 'soars', 'soar', 'jumps', 'jump',
}
BEARISH_WORDS = {
    'plunges', 'plunge', 'falls', 'fall', 'drops', 'drop', 'misses',
    'miss', 'loss', 'losses', 'downgrade', 'downgraded', 'underperform',
    'sell', 'weak', 'bearish', 'declines', 'decline', 'slumps', 'slump',
    'cuts', 'cut', 'warning', 'recall', 'investigation', 'fears', 'fear',
}


# ============================================================
# SIGNAL 1 — RECENCY SCORE (max 30 pts)
#
# Why recency is weighted highest (30/100):
# Short-term traders and investors need the most current
# information. A 6-hour-old earnings surprise is highly
# actionable; a 3-day-old analyst downgrade is largely priced
# in. Exponential decay mirrors how market participants
# discount stale information — roughly halving value every 24h.
#
# Formula: 30 * exp(-k * hours_since_2h_mark)
#   where k = ln(2)/24 gives a half-life of exactly 24 hours.
# Articles < 2h old are not penalized at all (full 30pts).
# ============================================================
def score_recency(published_at):
    try:
        pub = None
        if isinstance(published_at, str) and published_at.strip():
            s = published_at.strip().replace(' GMT', ' +0000')
            for fmt in ('%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%dT%H:%M:%S%z'):
                try:
                    pub = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if pub is None:
                # Handle ISO "Z" suffix (Python < 3.11 doesn't parse Z in %z)
                try:
                    pub = datetime.strptime(
                        published_at.strip().replace('Z', '+0000'), '%Y-%m-%dT%H:%M:%S+0000'
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    return 15.0, 'unknown age'

        if pub is None:
            return 15.0, 'unknown age'

        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)

        hours_old = max(0.0, (datetime.now(timezone.utc) - pub).total_seconds() / 3600)

        if hours_old <= 2:
            score = 30.0
        else:
            # Exponential decay: half-life = 24h after the 2h grace window
            k = math.log(2) / 24.0
            score = 30.0 * math.exp(-k * (hours_old - 2))

        score = round(max(0.0, min(30.0, score)), 1)

        if hours_old < 1:
            label = f'{int(hours_old * 60)}m ago'
        elif hours_old < 24:
            label = f'{hours_old:.1f}h ago'
        else:
            label = f'{(hours_old / 24):.1f}d ago'

        return score, label

    except Exception:
        return 15.0, 'unknown age'


# ============================================================
# SIGNAL 2 — SOURCE AUTHORITY SCORE (max 20 pts)
#
# Why source authority proxies for reliability:
# Professional editors at tier-1 outlets verify facts before
# publishing, reducing misinformation risk. Smaller outlets
# may republish press releases verbatim or amplify rumors.
# By scoring the source we penalize low-credibility noise
# without blacklisting — every source still contributes to
# the total, just with less weight.
# ============================================================
def score_source(source_name):
    lower = source_name.lower()
    for key, pts in SOURCE_TIERS.items():
        if key in lower:
            tier = 'Tier 1' if pts == 20 else 'Tier 2' if pts >= 15 else 'Tier 3'
            return float(pts), f'{tier} source'
    return 8.0, 'Standard source'


# ============================================================
# SIGNAL 3 — RELEVANCE SCORE (max 25 pts)
#
# Counts keyword density of ticker symbol + company name words
# in the article title + description. Ensures we aren't
# surfacing tangentially related content.
# 5 pts per unique mention, capped at 25 (5 mentions max out).
# Stop-words and short words (<=3 chars) are excluded from
# company-name matching to avoid false positives on "Inc", "The".
# ============================================================
def score_relevance(ticker, company_name, title, description):
    text = (title + ' ' + (description or '')).lower()

    ticker_hits = len(re.findall(r'\b' + re.escape(ticker.lower()) + r'\b', text))

    stop = {'corp', 'inc', 'inc.', 'ltd', 'company', 'co', 'co.', 'the', 'and', 'group'}
    company_words = [
        w for w in company_name.lower().split()
        if len(w) > 3 and w not in stop
    ]
    company_hits = sum(text.count(w) for w in company_words) if company_words else 0

    total = ticker_hits + company_hits
    score = round(min(25.0, total * 5.0), 1)
    return score, f'{total} keyword mentions'


# ============================================================
# SIGNAL 4 — SENTIMENT SIGNAL (max 15 pts)
#
# Simple positive/negative word list scoring on the headline
# plus the first 120 chars of description. No external library —
# just a dict of ~25 bullish/bearish words that consistently
# signal market direction in financial headlines.
#
# Net = bullish_count - bearish_count.
#   Neutral baseline = 7.5 pts (pure noise).
#   Each net unit shifts score ±3 pts.
# Returns a label (BULLISH/BEARISH/NEUTRAL) used directly in UI.
# ============================================================
def score_sentiment(title, description):
    text = (title + ' ' + (description or '')[:120]).lower()
    words = re.findall(r'\b\w+\b', text)

    bull_hits = [w for w in words if w in BULLISH_WORDS]
    bear_hits = [w for w in words if w in BEARISH_WORDS]
    bull_count, bear_count = len(bull_hits), len(bear_hits)

    if bull_count == 0 and bear_count == 0:
        return 7.5, 'NEUTRAL', 'no signal words'

    net = bull_count - bear_count
    if net > 0:
        label = 'BULLISH'
        score = min(15.0, 7.5 + net * 3.0)
    elif net < 0:
        label = 'BEARISH'
        score = max(0.0, 7.5 + net * 3.0)
    else:
        label = 'NEUTRAL'
        score = 7.5

    return round(score, 1), label, f'bull:{bull_count} bear:{bear_count}'


# ============================================================
# SIGNAL 5 — ENGAGEMENT PROXY (max 10 pts)
#
# Why we penalize clickbait (noise reduction):
# Sensationalist headlines ("You won't BELIEVE what TSLA did!")
# generate clicks and social shares but rarely contain
# actionable information. Traders who react to clickbait are
# more likely to make emotional decisions. This signal rewards
# declarative, factual titles and penalizes patterns that
# correlate with low-information content:
#   - Question marks (speculative, not factual)
#   - Exclamation marks (hype/emotional)
#   - ALL-CAPS words beyond known tickers (shouting = noise)
#   - Very short titles (< 20 chars) lack context
# A small bonus is given to 50–120 char titles — the sweet
# spot for a factual, complete financial headline.
# ============================================================
def score_engagement(title):
    score = 10.0

    # Penalize questions (speculative framing)
    score -= min(4.0, title.count('?') * 2.0)

    # Penalize exclamation (hype/emotional)
    score -= min(4.0, title.count('!') * 2.0)

    # Penalize excessive ALL-CAPS (beyond ticker symbols)
    all_caps = re.findall(r'\b[A-Z]{3,}\b', title)
    ticker_like = re.findall(r'\b[A-Z]{1,5}\b', title)
    caps_penalty = max(0, len(all_caps) - len(ticker_like))
    score -= min(3.0, caps_penalty * 1.0)

    # Penalize very short titles
    if len(title) < 20:
        score -= 3.0

    # Reward factual-length titles
    if 50 <= len(title) <= 120:
        score += 1.0

    return round(max(0.0, min(10.0, score)), 1), f'len:{len(title)}'


# ============================================================
# ONE-LINE REASONING GENERATOR
#
# Assembles a human-readable explanation by finding the
# highest- and lowest-contributing signals (normalized to
# each signal's max) and weaving them into a sentence.
# The tier word (High/Mid/Low) gives an instant summary.
# A trader should be able to parse this in under 3 seconds
# without reading the full score breakdown.
# ============================================================
def build_reasoning(ticker, rec_score, rec_label, src_score, src_detail,
                    rel_score, rel_detail, sent_score, sent_label,
                    eng_score, total):
    signals = [
        ('recency',         rec_score,  30, rec_label),
        ('source authority', src_score, 20, src_detail),
        ('relevance',       rel_score,  25, rel_detail),
        ('sentiment',       sent_score, 15, sent_label),
        ('title quality',   eng_score,  10, ''),
    ]
    # Normalize each signal to % of its maximum so we can compare apples-to-apples
    normed = [(name, score / max_pts, label) for name, score, max_pts, label in signals]
    best  = max(normed, key=lambda x: x[1])
    worst = min(normed, key=lambda x: x[1])

    tier = 'High' if total >= 65 else 'Mid' if total >= 40 else 'Low'
    best_detail  = f' ({best[2]})'  if best[2]  else ''
    worst_detail = f' ({worst[2]})' if worst[2] else ''

    return (
        f'{tier}-signal: strong {best[0]}{best_detail}; '
        f'weak {worst[0]}{worst_detail}. Score: {total:.0f}/100.'
    )


def rank_articles(ticker, company_name, articles):
    """Score every article, attach full breakdown, sort by total descending."""
    scored = []
    for art in articles:
        title       = art.get('title', '')
        description = art.get('description', '') or ''
        source      = art.get('source', 'Unknown')
        published_at = art.get('published_at', '')
        url         = art.get('url', '#')

        rec_score,  rec_label  = score_recency(published_at)
        src_score,  src_detail = score_source(source)
        rel_score,  rel_detail = score_relevance(ticker, company_name, title, description)
        sent_score, sent_label, sent_detail = score_sentiment(title, description)
        eng_score,  eng_detail = score_engagement(title)

        total = rec_score + src_score + rel_score + sent_score + eng_score
        reasoning = build_reasoning(
            ticker,
            rec_score, rec_label, src_score, src_detail,
            rel_score, rel_detail, sent_score, sent_label,
            eng_score, total,
        )

        scored.append({
            'title':            title,
            'url':              url,
            'source':           source,
            'published_at':     published_at,
            'total_score':      round(total, 1),
            'sentiment_label':  sent_label,
            'one_line_reasoning': reasoning,
            'breakdown': {
                'recency':         {'score': rec_score,  'max': 30, 'detail': rec_label},
                'source_authority':{'score': src_score,  'max': 20, 'detail': src_detail},
                'relevance':       {'score': rel_score,  'max': 25, 'detail': rel_detail},
                'sentiment':       {'score': sent_score, 'max': 15, 'detail': sent_detail},
                'engagement':      {'score': eng_score,  'max': 10, 'detail': eng_detail},
            },
        })

    scored.sort(key=lambda x: x['total_score'], reverse=True)
    return scored


# ============================================================
# ROUTES
# ============================================================

# ============================================================
# STATIC FILE SERVING
# Serve index.html / index.css / index.js directly from Flask
# so the browser and API share the same origin (port 5000).
# This eliminates the need for Live Server and prevents it from
# reloading the page every time stocknews.db is written.
# ============================================================
@app.route('/')
def serve_index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/<path:filename>')
def serve_static(filename):
    # Only serve known front-end files; everything else falls through to 404
    allowed = {'index.css', 'index.js'}
    if filename in allowed:
        return send_from_directory(BASE_DIR, filename)
    return jsonify({'error': 'not found'}), 404


@app.route('/api/stock/<ticker>', methods=['GET'])
def get_stock(ticker):
    ticker = ticker.upper().strip()
    try:
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d'
        resp = http.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        result = data['chart']['result'][0]
        meta   = result['meta']
        quotes = result.get('indicators', {}).get('quote', [{}])[0]
        timestamps = result.get('timestamp', [])

        current_price = meta.get('regularMarketPrice', 0)
        prev_close    = meta.get('chartPreviousClose') or meta.get('previousClose') or current_price
        change        = current_price - prev_close
        change_pct    = (change / prev_close * 100) if prev_close else 0

        closes  = quotes.get('close',  [])
        opens   = quotes.get('open',   [])
        highs   = quotes.get('high',   [])
        lows    = quotes.get('low',    [])
        volumes = quotes.get('volume', [])

        ohlc = []
        for i, ts in enumerate(timestamps):
            if i < len(closes) and closes[i] is not None:
                ohlc.append({
                    'timestamp': ts,
                    'open':   opens[i]   if i < len(opens)   else None,
                    'high':   highs[i]   if i < len(highs)   else None,
                    'low':    lows[i]    if i < len(lows)    else None,
                    'close':  closes[i],
                    'volume': volumes[i] if i < len(volumes) else None,
                })

        # Record this search for the suggestions algorithm
        db.session.add(SearchLog(ticker=ticker))
        db.session.commit()

        return jsonify({
            'ticker':       ticker,
            'company_name': COMPANY_NAMES.get(ticker, ticker),
            'price':        round(current_price, 2),
            'change':       round(change, 2),
            'change_pct':   round(change_pct, 2),
            'volume':       meta.get('regularMarketVolume', 0),
            'currency':     meta.get('currency', 'USD'),
            'exchange':     meta.get('exchangeName', ''),
            'ohlc':         ohlc,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/news/<ticker>', methods=['GET'])
def get_news(ticker):
    ticker       = ticker.upper().strip()
    company_name = COMPANY_NAMES.get(ticker, ticker)
    articles     = []

    # Primary source: Yahoo Finance RSS (no API key)
    try:
        rss_url = f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US'
        resp = http.get(rss_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        if resp.status_code == 200:
            root  = ET.fromstring(resp.content)
            items = root.findall('.//item')
            for item in items[:20]:
                title    = item.findtext('title', '').strip()
                link     = item.findtext('link', '#').strip()
                pub_date = item.findtext('pubDate', '').strip()
                desc     = item.findtext('description', '').strip()
                # Strip any HTML tags from the description blurb
                desc = re.sub(r'<[^>]+>', '', desc).strip()

                source_el = item.find('source')
                source = (source_el.text or 'Yahoo Finance') if source_el is not None else 'Yahoo Finance'

                if title:
                    articles.append({
                        'title':        title,
                        'url':          link,
                        'source':       source,
                        'published_at': pub_date,
                        'description':  desc,
                    })
    except Exception as e:
        print(f'[RSS] {e}')

    # Fallback: Yahoo Finance query2 JSON endpoint
    if len(articles) < 3:
        try:
            json_url = f'https://query2.finance.yahoo.com/v2/finance/news?symbols={ticker}'
            resp = http.get(json_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
            if resp.status_code == 200:
                data  = resp.json()
                items = data.get('items', {}).get('result', [])
                for item in items[:20]:
                    pub_ts = item.get('published_at') or item.get('publishedAt') or item.get('time') or 0
                    try:
                        pub_str = datetime.fromtimestamp(int(pub_ts), tz=timezone.utc).strftime(
                            '%a, %d %b %Y %H:%M:%S +0000'
                        )
                    except Exception:
                        pub_str = ''

                    articles.append({
                        'title':        item.get('title', ''),
                        'url':          item.get('link', '#'),
                        'source':       item.get('publisher', 'Yahoo Finance'),
                        'published_at': pub_str,
                        'description':  item.get('summary', ''),
                    })
        except Exception as e:
            print(f'[JSON news] {e}')

    if not articles:
        return jsonify({'ticker': ticker, 'company_name': company_name, 'articles': []})

    ranked = rank_articles(ticker, company_name, articles)

    # Persist top-5 results to cache (deduplicated by URL hash)
    try:
        for art in ranked[:5]:
            h = hashlib.md5(art['url'].encode()).hexdigest()
            if not NewsCache.query.filter_by(ticker=ticker, article_hash=h).first():
                db.session.add(NewsCache(ticker=ticker, article_hash=h, score=art['total_score']))
        db.session.commit()
    except Exception as e:
        print(f'[cache write] {e}')

    return jsonify({'ticker': ticker, 'company_name': company_name, 'articles': ranked})


@app.route('/api/suggestions', methods=['GET'])
def get_suggestions():
    """Return tickers ranked by search frequency, padded with popular defaults."""
    try:
        from sqlalchemy import func
        rows = (
            db.session.query(SearchLog.ticker, func.count(SearchLog.ticker).label('cnt'))
            .group_by(SearchLog.ticker)
            .order_by(func.count(SearchLog.ticker).desc())
            .limit(8)
            .all()
        )
        tickers = [r.ticker for r in rows]

        defaults = ['AAPL', 'TSLA', 'NVDA', 'MSFT', 'AMZN', 'META', 'GOOGL', 'AMD', 'COIN', 'PLTR']
        for d in defaults:
            if d not in tickers:
                tickers.append(d)

        return jsonify({'suggestions': tickers[:10]})
    except Exception as e:
        return jsonify({'suggestions': ['AAPL', 'TSLA', 'NVDA', 'MSFT', 'AMZN'], 'error': str(e)})


@app.route('/api/watchlist', methods=['GET', 'POST'])
def watchlist():
    if request.method == 'POST':
        data   = request.get_json() or {}
        ticker = data.get('ticker', '').upper().strip()
        if not ticker:
            return jsonify({'error': 'ticker required'}), 400
        if Watchlist.query.filter_by(ticker=ticker).first():
            return jsonify({'message': 'Already in watchlist', 'ticker': ticker}), 200
        db.session.add(Watchlist(ticker=ticker))
        db.session.commit()
        return jsonify({'message': 'Added', 'ticker': ticker}), 201
    else:
        items = Watchlist.query.order_by(Watchlist.added_at.desc()).all()
        return jsonify({
            'watchlist': [{'ticker': w.ticker, 'added_at': w.added_at.isoformat()} for w in items]
        })


import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
