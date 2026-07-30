import json, os, sys, yaml, re
from datetime import datetime, timezone
import requests

CONFIG_PATH = os.getenv('STOCK_TWEET_BOT_CONFIG',
                        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml'))
WATCHLIST_PATH = '/www/wwwroot/macro-bot/daily-news-fetcher/watchlist.json'


def load_config(path):
    with open(path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    # Prefer environment variable for API key so secrets stay out of files.
    env_key = os.getenv('XAI_API_KEY')
    if env_key and cfg.get('xai'):
        cfg['xai']['api_key'] = env_key
    return cfg


def load_watchlist(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def clean_ticker(ticker):
    for suffix in ['.SZ', '.SS', '.HK', '.T', '.PA', '.SW', '.DE', '.US']:
        if ticker.upper().endswith(suffix):
            return ticker[:-len(suffix)]
    return ticker


def build_holdings(watchlist):
    sources = watchlist.get('forceAnalyze') or watchlist.get('tickers') or []
    keywords = watchlist.get('companyKeywords', {})
    holdings = []
    for raw in sources:
        clean = clean_ticker(raw)
        names = keywords.get(raw, [clean])
        display_name = clean
        for name in names:
            if name.upper() != clean.upper() and len(name) > 1:
                display_name = name
                break
        aliases = []
        seen = {clean.upper()}
        for name in names:
            key = name.upper()
            if key not in seen and len(name) > 1:
                seen.add(key)
                aliases.append(name)
        holdings.append({'ticker': clean, 'display_name': display_name, 'aliases': aliases})
    return holdings


def build_prompt(ticker_cfg, count):
    t = ticker_cfg['ticker']
    name = ticker_cfg.get('display_name', t)
    aliases = ticker_cfg.get('aliases', [])
    parts = [t, name] + aliases
    seen = set()
    clean_parts = []
    for p in parts:
        key = p.strip().lower()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        clean_parts.append(p.strip())
    clean_parts = clean_parts[:5]
    query = ' OR '.join('"' + p + '"' for p in clean_parts)
    return (
        'Use X keyword search to find posts about (' + query + ') from the past 24 hours. '
        'You are a financial analyst. Your job is NOT to return raw tweets. '
        'Instead, summarize the market narrative around this company/stock and identify the most relevant posts. '
        'Return a single JSON object (not an array) inside a markdown code block with this exact structure: '
        '{"ticker": "' + t + '", "narrative": "1-2 sentences summarizing the main discussion on X", '
        '"key_points": ["point 1", "point 2", ...], '
        '"sources": [{"url": "https://x.com/...", "summary": "what this post says", "author": "@handle", "relevance": "investment|brand|macro|noise"}], '
        '"market_relevance": "high|medium|low|none", '
        '"is_investment_related": true/false, '
        '"price_movement_hypothesis": "how this narrative might relate to recent price action, if any"}. '
        'Include sources from both investment/finance accounts AND relevant brand/marketing/cultural events if they could move the stock. '
        'Do not include product deals, spam, or purely personal lifestyle posts. '
        'Return ONLY the JSON object inside a markdown code block, no other text.'
    )


def call_xai_search(prompt, config, timeout=45, retries=1):
    # Warn-only check: xAI 控制台如已确认其他型号有效，从白名单移除即可。
    # 仅警告不强制 fallback，避免未来新增的 grok-4-x 型号被无声重定向。
    _known_models = {
        'grok-4-1-fast-non-reasoning', 'grok-4-1-fast-reasoning',
        'grok-4-fast-reasoning', 'grok-4-fast-non-reasoning',
    }
    model = config['xai']['model']
    if model not in _known_models:
        print(
            f"warning: xAI model {model!r} not in known set; passing through as-is. "
            f"如 xAI 返回 400，请把新模型名加入 _known_models 或检查控制台。",
            file=sys.stderr,
        )
    payload = {
        'model': model,
        'input': prompt,
        'tools': [{'type': 'x_search'}],
        'tool_choice': 'required',
    }
    headers = {
        'Authorization': 'Bearer ' + config['xai']['api_key'],
        'Content-Type': 'application/json',
    }
    url = config['xai'].get('api_url', 'https://api.x.ai/v1/responses')
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                print(f"    retry {attempt + 1} for xAI: {e}", file=sys.stderr)
    raise last_err


def extract_json_array(text):
    start = text.find('[')
    if start == -1:
        return []
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except Exception:
                    return []
    return []


def extract_json_object(text):
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == '\\':
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except Exception:
                    return None
    return None


def extract_json_object_from_response(resp):
    if not resp:
        return None
    for item in resp.get('output', []):
        if item.get('type') != 'message':
            continue
        for c in item.get('content', []):
            if c.get('type') != 'output_text':
                continue
            obj = extract_json_object(c.get('text', ''))
            if isinstance(obj, dict):
                return obj
    return None

def extract_tweets(resp):
    for item in resp.get('output', []):
        if item.get('type') != 'message':
            continue
        for c in item.get('content', []):
            if c.get('type') != 'output_text':
                continue
            arr = extract_json_array(c.get('text', ''))
            if isinstance(arr, list):
                return arr
    return []


def is_relevant_tweet(t):
    """简单后过滤：排除明显促销、 Deals、粉丝日常、无意义短文本。"""
    text = (t.get('text') or '').lower()
    if not text or len(text.strip()) < 30:
        return False
    spam_terms = [
        'deal', 'deals', 'promo', 'coupon', 'cupom', 'oferta', 'discount', 'buy now',
        'amazon.com', 'amazon.', 'temu', 'aliexpress', 'shein', 'wish.com',
        'popular deal', '🔥', '💰', '🛒', '⚡️', 'free shipping', 'limited time',
        'ad ', 'sponsored', 'giveaway', 'win a', 'click here', 'link in bio',
    ]
    if any(s in text for s in spam_terms):
        return False
    if 'robot vacuum' in text and not any(inv in text for inv in ['stock', 'earnings', 'revenue', 'sales', 'market', 'share', 'growth', 'invest']):
        return False
    investment_signals = [
        'stock', 'stocks', 'share', 'shares', 'market', 'investing', 'investor',
        'earnings', 'revenue', 'profit', 'guidance', 'forecast', 'outlook', 'target',
        'analyst', 'rating', 'upgrade', 'downgrade', 'bull', 'bear', 'short',
        '财报', '业绩', '营收', '利润', '净利润', '毛利率', '增长', '预期',
        '券商', '研报', '评级', '上调', '下调', '增持', '减持', '买入', '卖出',
        '做多', '做空', '估值', 'pe', 'pb', 'eps', 'roe', 'margin', 'guidance',
        '美股', 'a股', '港股', '财报季', '季绩', '分红', '股息', '回购',
    ]
    if not any(s in text for s in investment_signals):
        return False
    return True


def normalize_tweet(t):
    if not isinstance(t, dict):
        return None
    return {
        'id': str(t.get('id', '')),
        'text': t.get('content', t.get('text', '')),
        'author': t.get('author.username', ''),
        'author_name': t.get('author.name', ''),
        'url': t.get('url', ''),
        'timestamp': t.get('timestamp', ''),
        'likes': int(t.get('engagement.likes', 0) or 0),
        'reposts': int(t.get('engagement.reposts', 0) or 0),
        'quotes': int(t.get('engagement.quotes', 0) or 0),
        'replies': int(t.get('engagement.replies', 0) or 0),
        'views': int(t.get('engagement.views', 0) or 0),
    }


def normalize_summary(t):
    """Parse xAI summary object for a ticker."""
    if not isinstance(t, dict):
        return None
    # Accept both upper/lower keys
    ticker = t.get('ticker') or t.get('TICKER', '')
    narrative = t.get('narrative') or t.get('NARRATIVE', '')
    key_points = t.get('key_points') or t.get('KEY_POINTS') or []
    sources = t.get('sources') or t.get('SOURCES') or []
    market_relevance = t.get('market_relevance') or t.get('MARKET_RELEVANCE', 'low')
    is_investment_related = t.get('is_investment_related') or t.get('IS_INVESTMENT_RELATED', False)
    hypothesis = t.get('price_movement_hypothesis') or t.get('PRICE_MOVEMENT_HYPOTHESIS', '')
    if not narrative and not key_points and not sources:
        return None
    return {
        'ticker': ticker,
        'narrative': narrative,
        'key_points': key_points if isinstance(key_points, list) else [],
        'sources': sources if isinstance(sources, list) else [],
        'market_relevance': market_relevance,
        'is_investment_related': bool(is_investment_related),
        'price_movement_hypothesis': hypothesis,
        'engagement_score': 0,
    }


def main():
    config = load_config(CONFIG_PATH)
    if os.path.exists(WATCHLIST_PATH):
        holdings = build_holdings(load_watchlist(WATCHLIST_PATH))
        print(f'[*] Loaded {len(holdings)} holdings from watchlist', file=sys.stderr)
    else:
        holdings = config.get('holdings', [])
        print(f'[*] Loaded {len(holdings)} holdings from config.yaml', file=sys.stderr)
    count = config.get('tweets_per_stock', 5)
    workers = 1
    timeout = config.get('xai_timeout', 60)
    retries = config.get('xai_retries', 2)
    if not holdings:
        print('[!] No holdings'); sys.exit(1)

    all_tweets = []
    print('=' * 60, file=sys.stderr)
    print(f'[*] Starting fetch for {len(holdings)} holdings (workers={workers}, timeout={timeout}s)', file=sys.stderr)
    print('=' * 60, file=sys.stderr)

    def run_one(tc):
        try:
            prompt = build_prompt(tc, count)
            raw = call_xai_search(prompt, config, timeout=timeout, retries=retries)
            summary_obj = normalize_summary(extract_json_object_from_response(raw))
            if summary_obj:
                summary_obj['ticker'] = tc['ticker']
                summary_obj['display_name'] = tc.get('display_name', '')
                summary_obj['_fetch_time_utc'] = datetime.now(timezone.utc).isoformat()
                return tc['ticker'], [summary_obj], None
            return tc['ticker'], [], "no summary returned"
        except Exception as e:
            return tc['ticker'], [], str(e)

    for h in holdings:
        ticker, tweets, err = run_one(h)
        if err:
            print(f"[!] {ticker} failed: {err}", file=sys.stderr)
        else:
            all_tweets.extend(tweets)
            print(f"[+] {ticker}: {len(tweets)} summary", file=sys.stderr)

    for t in all_tweets:
        t['engagement_score'] = t.get('likes', 0) + t.get('reposts', 0) * 2 + t.get('views', 0) / 100
    all_tweets.sort(key=lambda x: x.get('engagement_score', 0), reverse=True)

    # Optional: filter low-quality spam
    min_score = config.get('min_engagement_score', 0)
    all_tweets = [t for t in all_tweets if t.get('engagement_score', 0) >= min_score]

    out = os.path.join(os.path.dirname(CONFIG_PATH), 'tweets.jsonl')
    with open(out, 'w', encoding='utf-8') as f:
        for t in all_tweets:
            f.write(json.dumps(t, ensure_ascii=False) + '\n')
    print(f'[+] {len(all_tweets)} summaries -> {out}', file=sys.stderr)


if __name__ == '__main__':
    main()
