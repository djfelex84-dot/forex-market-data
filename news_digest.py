import html
import os
import sqlite3
import time

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import requests


BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

FREE_CHANNEL_ID = os.getenv(
    "TELEGRAM_FREE_CHANNEL_ID"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "/app/data/trading.db",
)


GDELT_URL = (
    "https://api.gdeltproject.org/"
    "api/v2/doc/doc"
)


# Two digests per day.
#
# 10:30 UTC:
# European session digest
#
# 16:30 UTC:
# US session digest
NEWS_SLOTS = (
    (
        "EUROPE",
        10,
        30,
    ),
    (
        "US",
        16,
        30,
    ),
)


# A digest can still be sent
# within 90 minutes after its
# scheduled time.
SEND_WINDOW_MINUTES = 90


# If GDELT has a temporary problem,
# do not request it every 5 minutes.
SOURCE_RETRY_SECONDS = 20 * 60


# Search the previous 8 hours.
NEWS_TIMESPAN = "8h"


MAX_ARTICLES_PER_CATEGORY = 3

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


FOREX_QUERY = (
    "("
    "forex "
    'OR "Federal Reserve" '
    "OR ECB "
    'OR "Bank of England" '
    "OR EURUSD "
    "OR GBPUSD "
    "OR euro "
    "OR dollar "
    "OR pound "
    "OR inflation "
    'OR "interest rates"'
    ") "
    "sourcelang:english"
)


CRYPTO_QUERY = (
    "("
    "bitcoin "
    "OR ethereum "
    "OR cryptocurrency "
    "OR crypto "
    'OR "spot ETF"'
    ") "
    "sourcelang:english"
)


# We deliberately restrict the
# public digest to established
# financial / market sources.
TRUSTED_DOMAINS = {
    "reuters.com",
    "bloomberg.com",
    "cnbc.com",
    "ft.com",
    "marketwatch.com",
    "finance.yahoo.com",
    "investing.com",
    "fxstreet.com",
    "dailyfx.com",
    "coindesk.com",
    "decrypt.co",
    "theblock.co",
    "blockworks.co",
    "cointelegraph.com",
}


SOURCE_NAMES = {
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "cnbc.com": "CNBC",
    "ft.com": "Financial Times",
    "marketwatch.com": "MarketWatch",
    "finance.yahoo.com": "Yahoo Finance",
    "investing.com": "Investing.com",
    "fxstreet.com": "FXStreet",
    "dailyfx.com": "DailyFX",
    "coindesk.com": "CoinDesk",
    "decrypt.co": "Decrypt",
    "theblock.co": "The Block",
    "blockworks.co": "Blockworks",
    "cointelegraph.com": "Cointelegraph",
}


FOREX_KEYWORDS = {
    "federal reserve": 8,
    "fed rate": 8,
    "fed rates": 8,
    "ecb": 8,
    "bank of england": 8,
    "boe": 7,

    "interest rate": 6,
    "interest rates": 6,
    "rate cut": 6,
    "rate cuts": 6,
    "rate hike": 6,

    "inflation": 6,
    "cpi": 6,
    "payroll": 6,
    "nonfarm": 6,
    "jobs": 4,
    "gdp": 5,

    "eur/usd": 8,
    "eurusd": 8,
    "gbp/usd": 8,
    "gbpusd": 8,

    "euro": 4,
    "dollar": 4,
    "pound": 4,
    "sterling": 5,

    "forex": 5,
    "currency": 3,
}


CRYPTO_KEYWORDS = {
    "bitcoin": 8,
    "btc": 6,

    "ethereum": 8,
    "ether": 6,
    "eth": 5,

    "crypto": 5,
    "cryptocurrency": 6,

    "bitcoin etf": 8,
    "ethereum etf": 8,
    "spot etf": 7,

    "sec": 4,
    "stablecoin": 5,
    "blockchain": 3,
}


_last_source_attempt = {}


def get_connection():
    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def init_news_digest_tables():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            news_digest_log (
                slot_key TEXT
                PRIMARY KEY,

                processed_at TEXT
                NOT NULL,

                status TEXT
                NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            news_article_log (
                url TEXT
                PRIMARY KEY,

                category TEXT
                NOT NULL,

                sent_at TEXT
                NOT NULL
            )
            """
        )

        connection.commit()


def digest_already_processed(
    slot_key
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT slot_key

            FROM news_digest_log

            WHERE slot_key = ?

            LIMIT 1
            """,
            (
                slot_key,
            ),
        ).fetchone()

    return row is not None


def mark_digest_processed(
    slot_key,
    status,
):
    processed_at = datetime.now(
        timezone.utc
    ).strftime(
        TIME_FORMAT
    )

    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO
            news_digest_log (
                slot_key,
                processed_at,
                status
            )

            VALUES (?, ?, ?)
            """,
            (
                slot_key,
                processed_at,
                status,
            ),
        )

        connection.commit()


def article_already_sent(
    url
):
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT url

            FROM news_article_log

            WHERE url = ?

            LIMIT 1
            """,
            (
                url,
            ),
        ).fetchone()

    return row is not None


def mark_articles_sent(
    articles,
    category,
):
    sent_at = datetime.now(
        timezone.utc
    ).strftime(
        TIME_FORMAT
    )

    with get_connection() as connection:
        for article in articles:
            connection.execute(
                """
                INSERT OR IGNORE INTO
                news_article_log (
                    url,
                    category,
                    sent_at
                )

                VALUES (?, ?, ?)
                """,
                (
                    article["url"],
                    category,
                    sent_at,
                ),
            )

        connection.commit()


def normalize_domain(
    domain
):
    domain = (
        domain
        or ""
    ).lower().strip()

    if domain.startswith(
        "www."
    ):
        domain = domain[4:]

    return domain


def domain_is_trusted(
    domain
):
    domain = normalize_domain(
        domain
    )

    if domain in TRUSTED_DOMAINS:
        return True

    for trusted in TRUSTED_DOMAINS:
        if domain.endswith(
            "." + trusted
        ):
            return True

    return False


def source_name(
    domain
):
    domain = normalize_domain(
        domain
    )

    if domain in SOURCE_NAMES:
        return SOURCE_NAMES[
            domain
        ]

    for trusted, name in (
        SOURCE_NAMES.items()
    ):
        if domain.endswith(
            "." + trusted
        ):
            return name

    return domain


def article_score(
    title,
    keywords
):
    title_lower = (
        title
        .lower()
        .strip()
    )

    score = 0

    for keyword, points in (
        keywords.items()
    ):
        if keyword in title_lower:
            score += points

    return score


def parse_seen_date(
    value
):
    if not value:
        return None

    formats = (
        "%Y%m%dT%H%M%SZ",
        "%Y%m%dT%H%M%S",
    )

    for date_format in formats:
        try:
            return datetime.strptime(
                value,
                date_format,
            ).replace(
                tzinfo=timezone.utc
            )

        except ValueError:
            continue

    return None


def fetch_gdelt_articles(
    query
):
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": 75,
        "timespan": NEWS_TIMESPAN,
        "sort": "datedesc",
        "format": "json",
    }

    response = requests.get(
        GDELT_URL,
        params=params,
        timeout=25,
        headers={
            "User-Agent":
                "AS-Forex-Crypto-News/1.0"
        },
    )

    response.raise_for_status()

    try:
        data = response.json()

    except ValueError as error:
        raise RuntimeError(
            "GDELT returned "
            "invalid JSON"
        ) from error

    articles = data.get(
        "articles"
    )

    if articles is None:
        return []

    if not isinstance(
        articles,
        list,
    ):
        raise RuntimeError(
            "Unexpected GDELT response"
        )

    return articles


def select_articles(
    raw_articles,
    keywords,
    limit,
):
    candidates = []

    seen_urls = set()

    for article in raw_articles:
        title = (
            article.get(
                "title"
            )
            or ""
        ).strip()

        url = (
            article.get(
                "url"
            )
            or ""
        ).strip()

        domain = normalize_domain(
            article.get(
                "domain"
            )
        )

        if not title:
            continue

        if not url:
            continue

        if url in seen_urls:
            continue

        seen_urls.add(
            url
        )

        if not domain_is_trusted(
            domain
        ):
            continue

        if article_already_sent(
            url
        ):
            continue

        score = article_score(
            title,
            keywords,
        )

        if score <= 0:
            continue

        seen_date = parse_seen_date(
            article.get(
                "seendate"
            )
        )

        candidates.append(
            {
                "title": title,
                "url": url,
                "domain": domain,
                "source":
                    source_name(
                        domain
                    ),
                "score": score,
                "seen_date":
                    seen_date,
            }
        )

    candidates.sort(
        key=lambda item: (
            item["score"],
            (
                item["seen_date"]
                or datetime(
                    1970,
                    1,
                    1,
                    tzinfo=timezone.utc,
                )
            ),
        ),
        reverse=True,
    )

    selected = []

    used_sources = set()

    # First pass:
    # prefer different sources.
    for article in candidates:
        if len(selected) >= limit:
            break

        source = article[
            "source"
        ]

        if source in used_sources:
            continue

        selected.append(
            article
        )

        used_sources.add(
            source
        )

    # Second pass:
    # if we still need stories,
    # allow another story from the
    # same source.
    if len(selected) < limit:
        selected_urls = {
            article["url"]
            for article in selected
        }

        for article in candidates:
            if len(selected) >= limit:
                break

            if (
                article["url"]
                in selected_urls
            ):
                continue

            selected.append(
                article
            )

            selected_urls.add(
                article["url"]
            )

    return selected


def format_article_time(
    article
):
    seen_date = article.get(
        "seen_date"
    )

    if seen_date is None:
        return ""

    return seen_date.strftime(
        "%H:%M UTC"
    )


def shorten_title(
    title,
    max_length=150,
):
    title = " ".join(
        title.split()
    )

    if len(title) <= max_length:
        return title

    return (
        title[
            :max_length - 1
        ].rstrip()
        + "…"
    )


def append_news_section(
    lines,
    heading,
    articles,
):
    if not articles:
        return

    lines.extend(
        [
            "",
            heading,
        ]
    )

    for article in articles:
        title = html.escape(
            shorten_title(
                article["title"]
            )
        )

        url = html.escape(
            article["url"],
            quote=True,
        )

        source = html.escape(
            article["source"]
        )

        article_time = (
            format_article_time(
                article
            )
        )

        lines.append(
            f"• <b>{title}</b>"
        )

        if article_time:
            lines.append(
                f"  {source} · "
                f"{article_time}"
            )

        else:
            lines.append(
                f"  {source}"
            )

        lines.append(
            f'  <a href="{url}">'
            "Read source"
            "</a>"
        )


def build_news_digest(
    now,
    forex_articles,
    crypto_articles,
):
    lines = [
        "📰 <b>AS · MARKET NEWS DIGEST</b>",
        "",
        (
            "📅 "
            f"<b>{now.strftime('%d %b %Y')}</b>"
            " · "
            f"{now.strftime('%H:%M')} UTC"
        ),
    ]

    append_news_section(
        lines,
        "💱 <b>FOREX & MACRO</b>",
        forex_articles,
    )

    append_news_section(
        lines,
        "₿ <b>CRYPTO</b>",
        crypto_articles,
    )

    lines.extend(
        [
            "",
            (
                "ℹ️ News selection is automated. "
                "Open the original source for "
                "full context."
            ),
            "",
            (
                'News discovery: '
                '<a href="https://www.gdeltproject.org/">'
                "GDELT Project"
                "</a>"
            ),
            "",
            "<b>AS | Forex & Crypto</b>",
            "@ASForexCrypto",
        ]
    )

    return "\n".join(
        lines
    )


def send_free_channel_message(
    text
):
    if (
        not BOT_TOKEN
        or not FREE_CHANNEL_ID
    ):
        print(
            "NEWS DIGEST WARNING | "
            "Telegram configuration "
            "is missing",
            flush=True,
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            json={
                "chat_id":
                    FREE_CHANNEL_ID,

                "text":
                    text,

                "parse_mode":
                    "HTML",

                "disable_web_page_preview":
                    True,
            },
            timeout=15,
        )

        data = response.json()

        if not data.get(
            "ok"
        ):
            print(
                "NEWS DIGEST "
                f"TELEGRAM ERROR | {data}",
                flush=True,
            )

            return False

        return True

    except Exception as error:
        print(
            "NEWS DIGEST "
            "TELEGRAM ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False


def make_slot_key(
    now,
    slot_name,
):
    return (
        f"{now.strftime('%Y-%m-%d')}"
        f"-{slot_name}"
    )


def get_slot_time(
    now,
    hour,
    minute,
):
    return datetime(
        year=now.year,
        month=now.month,
        day=now.day,
        hour=hour,
        minute=minute,
        second=0,
        tzinfo=timezone.utc,
    )


def get_due_slot(
    now
):
    for (
        slot_name,
        hour,
        minute,
    ) in NEWS_SLOTS:

        slot_key = make_slot_key(
            now,
            slot_name,
        )

        if digest_already_processed(
            slot_key
        ):
            continue

        due_time = get_slot_time(
            now,
            hour,
            minute,
        )

        latest_time = (
            due_time
            + timedelta(
                minutes=
                    SEND_WINDOW_MINUTES
            )
        )

        if now < due_time:
            continue

        if now > latest_time:
            mark_digest_processed(
                slot_key,
                "SKIPPED_LATE",
            )

            print(
                "NEWS DIGEST SKIPPED | "
                f"Slot={slot_key} | "
                "Too late",
                flush=True,
            )

            continue

        return {
            "name": slot_name,
            "key": slot_key,
            "due_time": due_time,
        }

    return None


def source_retry_allowed(
    slot_key
):
    previous_attempt = (
        _last_source_attempt.get(
            slot_key
        )
    )

    now_monotonic = (
        time.monotonic()
    )

    if previous_attempt is not None:
        elapsed = (
            now_monotonic
            - previous_attempt
        )

        if (
            elapsed
            < SOURCE_RETRY_SECONDS
        ):
            return False

    _last_source_attempt[
        slot_key
    ] = now_monotonic

    return True


def process_news_digest():
    init_news_digest_tables()

    now = datetime.now(
        timezone.utc
    )

    slot = get_due_slot(
        now
    )

    if slot is None:
        return False

    slot_key = slot[
        "key"
    ]

    if not source_retry_allowed(
        slot_key
    ):
        return False

    try:
        forex_raw = (
            fetch_gdelt_articles(
                FOREX_QUERY
            )
        )

        crypto_raw = (
            fetch_gdelt_articles(
                CRYPTO_QUERY
            )
        )

    except Exception as error:
        print(
            "NEWS DIGEST SOURCE ERROR | "
            f"{type(error).__name__}: "
            f"{error}",
            flush=True,
        )

        return False

    forex_articles = (
        select_articles(
            raw_articles=
                forex_raw,

            keywords=
                FOREX_KEYWORDS,

            limit=
                MAX_ARTICLES_PER_CATEGORY,
        )
    )

    crypto_articles = (
        select_articles(
            raw_articles=
                crypto_raw,

            keywords=
                CRYPTO_KEYWORDS,

            limit=
                MAX_ARTICLES_PER_CATEGORY,
        )
    )

    if (
        not forex_articles
        and not crypto_articles
    ):
        mark_digest_processed(
            slot_key,
            "NO_RELEVANT_NEWS",
        )

        print(
            "NEWS DIGEST SKIPPED | "
            f"Slot={slot_key} | "
            "No relevant trusted news",
            flush=True,
        )

        return False

    text = build_news_digest(
        now,
        forex_articles,
        crypto_articles,
    )

    sent = (
        send_free_channel_message(
            text
        )
    )

    if not sent:
        return False

    mark_articles_sent(
        forex_articles,
        "FOREX",
    )

    mark_articles_sent(
        crypto_articles,
        "CRYPTO",
    )

    mark_digest_processed(
        slot_key,
        "SENT",
    )

    print(
        "NEWS DIGEST SENT | "
        f"Slot={slot_key} | "
        f"Forex="
        f"{len(forex_articles)} | "
        f"Crypto="
        f"{len(crypto_articles)}",
        flush=True,
    )

    return True
