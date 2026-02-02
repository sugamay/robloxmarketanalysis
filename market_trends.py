#!/usr/bin/env python3
"""
roblox_market_trends.py

Market trend analysis tool for Roblox limited items.
It discovers limiteds from Rolimon's deals page, persists a watchlist,
and logs price snapshots over time to CSV for trend analysis.

Usage examples:
  python fastSnipe.py                         # default: 10-minute polling
  python fastSnipe.py --poll 300              # poll every 5 minutes
  python fastSnipe.py --no-headless           # show browser
  python fastSnipe.py --discover-only         # only refresh watchlist

Requirements:
  pip install selenium webdriver-manager beautifulsoup4
"""

import argparse
import csv
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

DEFAULT_DEALS_URL = "https://www.rolimons.com/deals"

WATCHLIST_HEADERS = [
    "item_id",
    "title",
    "rolimons_url",
    "first_seen",
    "last_seen",
]

PRICES_HEADERS = [
    "timestamp_utc",
    "item_id",
    "title",
    "price",
    "source_url",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


@dataclass
class Deal:
    item_id: str
    title: str
    price: int
    rolimons_url: str


@dataclass
class WatchItem:
    item_id: str
    title: str
    rolimons_url: str
    first_seen: str
    last_seen: str


def _to_int_from_text(text: str) -> int:
    if not text:
        return 0
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_driver(headless: bool = True, disable_images: bool = True) -> webdriver.Chrome:
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-background-timer-throttling")
    chrome_options.add_argument("--disable-backgrounding-occluded-windows")

    if disable_images:
        prefs = {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_setting_values.media_stream": 2,
        }
        chrome_options.add_experimental_option("prefs", prefs)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(30)
    return driver


def open_deals_page(driver: webdriver.Chrome, wait_timeout: int = 12) -> None:
    driver.get(DEFAULT_DEALS_URL)
    WebDriverWait(driver, wait_timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(0.7)
    logging.info("Loaded Rolimon's deals page.")


VALID_GRADIENTS = [
    "deal_bg_gradient_uncommon",
    "deal_bg_gradient_rare",
    "deal_bg_gradient_epic",
    "deal_bg_gradient_legendary",
]


def _extract_item_id(url: str) -> Optional[str]:
    if not url:
        return None
    match = re.search(r"/item/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r"/catalog/(\d+)", url)
    if match:
        return match.group(1)
    digits = re.findall(r"\d{4,}", url)
    return digits[0] if digits else None


def get_deals_via_selenium(driver: webdriver.Chrome) -> List[Deal]:
    selector = ", ".join(f"div.{c}" for c in VALID_GRADIENTS)
    containers = driver.find_elements(By.CSS_SELECTOR, selector)
    deals: List[Deal] = []

    for c in containers:
        try:
            url = None
            try:
                parent_a = c.find_element(By.XPATH, "./ancestor::a[1]")
                url = parent_a.get_attribute("href")
            except Exception:
                url = None

            title = "Unknown"
            try:
                title_el = c.find_element(By.CSS_SELECTOR, ".deal-title")
                title = title_el.get_attribute("title") or title_el.text.strip() or title
            except Exception:
                pass

            data = {}
            try:
                rows = c.find_elements(By.CSS_SELECTOR, ".mt-1.rounded-bottom .d-flex.justify-content-between")
                for row in rows:
                    try:
                        header = row.find_element(By.CSS_SELECTOR, ".stat-header").text.strip()
                        value = row.find_element(By.CSS_SELECTOR, ".stat-data").text.strip()
                        data[header] = value
                    except Exception:
                        continue
            except Exception:
                pass

            price = _to_int_from_text(data.get("Price", "0"))
            item_id = _extract_item_id(url or "")
            if not item_id or not url:
                continue

            deals.append(
                Deal(
                    item_id=item_id,
                    title=title,
                    price=price,
                    rolimons_url=url,
                )
            )
        except Exception:
            continue

    return deals


def _read_csv_rows(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def _write_csv_rows(path: str, headers: Iterable[str], rows: Iterable[Dict[str, str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(headers))
        writer.writeheader()
        writer.writerows(rows)


def _append_csv_rows(path: str, headers: Iterable[str], rows: Iterable[Dict[str, str]]) -> None:
    file_exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(headers))
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def upsert_watchlist(watchlist_path: str, deals: List[Deal], max_new: int) -> Tuple[List[WatchItem], int]:
    now_iso = _now_utc_iso()
    existing_rows = _read_csv_rows(watchlist_path)
    existing: Dict[str, WatchItem] = {}
    for row in existing_rows:
        item = WatchItem(
            item_id=row["item_id"],
            title=row["title"],
            rolimons_url=row["rolimons_url"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )
        existing[item.item_id] = item

    new_count = 0
    for deal in deals:
        if deal.item_id in existing:
            item = existing[deal.item_id]
            item.title = deal.title or item.title
            item.rolimons_url = deal.rolimons_url or item.rolimons_url
            item.last_seen = now_iso
        else:
            if new_count >= max_new:
                continue
            existing[deal.item_id] = WatchItem(
                item_id=deal.item_id,
                title=deal.title,
                rolimons_url=deal.rolimons_url,
                first_seen=now_iso,
                last_seen=now_iso,
            )
            new_count += 1

    rows = [
        {
            "item_id": item.item_id,
            "title": item.title,
            "rolimons_url": item.rolimons_url,
            "first_seen": item.first_seen,
            "last_seen": item.last_seen,
        }
        for item in existing.values()
    ]
    rows.sort(key=lambda r: (r["first_seen"], r["item_id"]))
    _write_csv_rows(watchlist_path, WATCHLIST_HEADERS, rows)

    return list(existing.values()), new_count


def _fetch_html(url: str, timeout: int = 12) -> Optional[str]:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; RobloxMarketTrends/1.0)",
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        logging.debug("HTTP fetch failed for %s: %s", url, exc)
        return None


def _extract_price_from_html(html: str) -> Optional[int]:
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    label_candidates = soup.find_all(string=re.compile(r"Price|Value|RAP", re.IGNORECASE))
    for label in label_candidates:
        try:
            parent = label.parent
            if not parent:
                continue
            text = parent.get_text(" ", strip=True)
            price = _to_int_from_text(text)
            if price > 0:
                return price
            sibling_texts = []
            for sib in parent.find_all_next(limit=5):
                sibling_texts.append(sib.get_text(" ", strip=True))
            for text in sibling_texts:
                price = _to_int_from_text(text)
                if price > 0:
                    return price
        except Exception:
            continue

    # Fallback: any "Robux" number
    match = re.search(r"([0-9][0-9,]{2,})\s*R\$|([0-9][0-9,]{2,})\s*Robux", html)
    if match:
        return _to_int_from_text(match.group(0))
    return None


def resolve_price(item: WatchItem, price_fallbacks: Dict[str, int]) -> Optional[int]:
    if item.item_id in price_fallbacks and price_fallbacks[item.item_id] > 0:
        return price_fallbacks[item.item_id]
    html = _fetch_html(item.rolimons_url)
    if not html:
        return None
    return _extract_price_from_html(html)


def log_price_snapshots(prices_path: str, items: List[WatchItem], price_map: Dict[str, int]) -> int:
    rows = []
    timestamp = _now_utc_iso()
    for item in items:
        price = price_map.get(item.item_id)
        if not price:
            continue
        rows.append(
            {
                "timestamp_utc": timestamp,
                "item_id": item.item_id,
                "title": item.title,
                "price": str(price),
                "source_url": item.rolimons_url,
            }
        )
    if rows:
        _append_csv_rows(prices_path, PRICES_HEADERS, rows)
    return len(rows)


def _load_recent_prices(prices_path: str, lookback_hours: int = 24) -> Dict[str, List[Tuple[datetime, int, str]]]:
    rows = _read_csv_rows(prices_path)
    if not rows:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    grouped: Dict[str, List[Tuple[datetime, int, str]]] = {}
    for row in rows:
        try:
            ts = datetime.fromisoformat(row["timestamp_utc"])
        except Exception:
            continue
        if ts < cutoff:
            continue
        item_id = row["item_id"]
        price = _to_int_from_text(row["price"])
        title = row.get("title", "")
        grouped.setdefault(item_id, []).append((ts, price, title))
    for item_id in grouped:
        grouped[item_id].sort(key=lambda tup: tup[0])
    return grouped


def print_trend_summary(prices_path: str, lookback_hours: int = 24, limit: int = 10) -> None:
    grouped = _load_recent_prices(prices_path, lookback_hours=lookback_hours)
    if not grouped:
        logging.info("No recent price data to summarize.")
        return

    summaries = []
    for item_id, entries in grouped.items():
        last_ts, last_price, title = entries[-1]
        first_ts, first_price, _ = entries[0]
        delta = last_price - first_price
        pct = (delta / first_price * 100) if first_price else 0.0
        summaries.append((abs(pct), item_id, title, last_price, delta, pct, first_ts, last_ts))

    summaries.sort(reverse=True, key=lambda s: s[0])
    logging.info("Trend summary (last %s hours, top %s movers):", lookback_hours, limit)
    for _, item_id, title, last_price, delta, pct, first_ts, last_ts in summaries[:limit]:
        logging.info(
            "  %s (%s): %s Robux (%+d, %+0.1f%%) [%s -> %s]",
            title,
            item_id,
            last_price,
            delta,
            pct,
            first_ts.isoformat(timespec="minutes"),
            last_ts.isoformat(timespec="minutes"),
        )


def build_price_map(items: List[WatchItem], deals: List[Deal]) -> Dict[str, int]:
    price_map: Dict[str, int] = {deal.item_id: deal.price for deal in deals if deal.price > 0}
    for item in items:
        if item.item_id not in price_map:
            price = resolve_price(item, price_map)
            if price:
                price_map[item.item_id] = price
    return price_map


def main_loop(
    headless: bool,
    disable_images: bool,
    poll_interval: int,
    refresh_interval: int,
    watchlist_path: str,
    prices_path: str,
    max_new: int,
    discover_only: bool,
) -> None:
    driver = init_driver(headless=headless, disable_images=disable_images)
    try:
        open_deals_page(driver)
        last_refresh = time.time()

        logging.info("Starting market trend loop (poll_interval=%ss).", poll_interval)
        while True:
            if time.time() - last_refresh >= refresh_interval:
                driver.refresh()
                time.sleep(0.8)
                last_refresh = time.time()

            deals = get_deals_via_selenium(driver)
            logging.info("Discovered %s deals from Rolimon's.", len(deals))

            watchlist, new_count = upsert_watchlist(watchlist_path, deals, max_new=max_new)
            if new_count:
                logging.info("Added %s new items to watchlist.", new_count)
            else:
                logging.info("No new items added to watchlist.")

            if not discover_only:
                price_map = build_price_map(watchlist, deals)
                logged = log_price_snapshots(prices_path, watchlist, price_map)
                logging.info("Logged %s price snapshots.", logged)
                print_trend_summary(prices_path, lookback_hours=24, limit=10)

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logging.info("User requested stop (KeyboardInterrupt).")
    finally:
        driver.quit()
        logging.info("Driver quit; exiting.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Roblox limited market trend tracker")
    parser.add_argument("--poll", type=int, default=600, help="Polling interval in seconds (default 600).")
    parser.add_argument("--refresh", type=int, default=600, help="Browser refresh interval in seconds (default 600).")
    parser.add_argument("--headless", dest="headless", action="store_true", help="Run headless (default).")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="Run with visible browser window.")
    parser.add_argument("--disable-images", dest="disable_images", action="store_true", help="Disable images/media (default).")
    parser.add_argument("--enable-images", dest="disable_images", action="store_false", help="Enable images/media.")
    parser.add_argument("--watchlist", default="watchlist.csv", help="Path to watchlist CSV.")
    parser.add_argument("--prices", default="prices.csv", help="Path to price history CSV.")
    parser.add_argument("--max-new", type=int, default=50, help="Max new items to add per run (default 50).")
    parser.add_argument("--discover-only", action="store_true", help="Only update watchlist; skip price logging.")
    parser.set_defaults(headless=True, disable_images=True)

    args = parser.parse_args()
    try:
        main_loop(
            headless=args.headless,
            disable_images=args.disable_images,
            poll_interval=args.poll,
            refresh_interval=args.refresh,
            watchlist_path=args.watchlist,
            prices_path=args.prices,
            max_new=args.max_new,
            discover_only=args.discover_only,
        )
    except Exception as exc:
        logging.exception("Fatal error in main: %s", exc)
        raise
