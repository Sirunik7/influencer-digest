"""
Daily Influencer Digest
Берёт сегодняшние посты из X (через Apify) и отправляет их в Telegram-канал.
"""

import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

# ================= НАСТРОЙКИ =================
# Ники в X, БЕЗ @. Можно 1 или несколько, через запятую.
HANDLES = ["elonmusk"]

TIMEZONE = "Asia/Yerevan"          # "сегодня" считаем по ереванскому времени
MAX_ITEMS_PER_HANDLE = 50          # сколько твитов максимум брать на одного человека
ACTOR_ID = "xquik~x-tweet-scraper" # скрапер в Apify
EXCERPT_LIMIT = 700                # длина отрывка текста в сообщении
# =============================================

APIFY_TOKEN = os.environ["APIFY_TOKEN"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ["TELEGRAM_CHANNEL"]

TZ = ZoneInfo(TIMEZONE)
TODAY = datetime.now(TZ).date()


def post_json(url, payload, headers=None, timeout=300):
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_tweets(handle):
    """Запускает скрапер в Apify и возвращает список твитов."""
    url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items"
    # начало сегодняшнего дня по Еревану, в формате unix-времени
    midnight = datetime.combine(TODAY, datetime.min.time(), tzinfo=TZ)
    since_ts = int(midnight.timestamp())
    payload = {
        "searchTerms": [f"from:{handle} since_time:{since_ts} -filter:retweets"],
        "maxItems": MAX_ITEMS_PER_HANDLE,
        "sort": "Latest",
    }
    items = post_json(url, payload, headers={"Authorization": f"Bearer {APIFY_TOKEN}"})
    return items if isinstance(items, list) else []


def first(item, *keys):
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def parse_date(value):
    if not value:
        return None
    if isinstance(value, (int, float)):  # unix-время (секунды или миллисекунды)
        ts = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(ts, tz=ZoneInfo("UTC"))
    try:
        return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")  # формат X
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def to_post(item, handle):
    """Достаёт из ответа скрапера нужные поля."""
    text = first(item, "text", "fullText", "full_text") or ""
    created = parse_date(first(item, "createdAt", "created_at", "date", "postedAt", "timestamp"))

    author = item.get("author") or {}
    if isinstance(author, str):
        try:
            author = json.loads(author)
        except ValueError:
            author = {}
    if not isinstance(author, dict):
        author = {}
    username = first(author, "userName", "username", "screen_name") or handle
    name = first(author, "name", "displayName") or username

    tweet_id = first(item, "id", "id_str", "tweetId")
    link = first(item, "url", "twitterUrl", "tweetUrl")
    if not link and tweet_id:
        link = f"https://x.com/{username}/status/{tweet_id}"

    is_retweet = text.startswith("RT @") or bool(item.get("isRetweet"))

    return {
        "id": str(tweet_id or link),
        "text": text,
        "created": created,
        "username": username,
        "name": name,
        "link": link,
        "is_retweet": is_retweet,
    }


def is_today(post):
    if not post["created"]:
        return False
    return post["created"].astimezone(TZ).date() == TODAY


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    result = post_json(url, payload, timeout=30)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def format_post(post):
    text = post["text"]
    if len(text) > EXCERPT_LIMIT:
        text = text[:EXCERPT_LIMIT].rstrip() + "…"
    time_str = post["created"].astimezone(TZ).strftime("%H:%M")
    return (
        f"👤 <b>{html.escape(post['name'])}</b> (@{html.escape(post['username'])}) · {time_str}\n\n"
        f"{html.escape(text)}\n\n"
        f"🔗 <a href=\"{html.escape(post['link'] or '')}\">Open original post</a>"
    )


def main():
    print(f"Today ({TIMEZONE}): {TODAY}")
    posts, seen, failed = [], set(), 0

    for handle in HANDLES:
        try:
            items = fetch_tweets(handle)
        except (urllib.error.URLError, TimeoutError, ValueError) as err:
            failed += 1
            print(f"[{handle}] ERROR while fetching: {err}")
            continue

        # для отладки: покажем даты первых 3 твитов
        for item in items[:3]:
            p = to_post(item, handle)
            print(f"  sample: created={p['created']} rt={p['is_retweet']} text={p['text'][:60]!r}")

        kept = 0
        for item in items:
            post = to_post(item, handle)
            if post["is_retweet"] or not is_today(post) or post["id"] in seen:
                continue
            seen.add(post["id"])
            posts.append(post)
            kept += 1
        print(f"[{handle}] received: {len(items)}, today's posts: {kept}")

    if failed == len(HANDLES):
        print("All handles failed — nothing sent.")
        sys.exit(1)

    if not posts:
        send_telegram(f"📭 No new posts today ({TODAY.strftime('%d.%m.%Y')})")
        print("No new posts today — sent short message.")
        return

    posts.sort(key=lambda p: p["created"])
    for post in posts:
        send_telegram(format_post(post))
        time.sleep(1)  # чтобы не упереться в лимиты Telegram
    print(f"Sent {len(posts)} post(s).")


if __name__ == "__main__":
    main()
