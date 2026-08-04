"""Fetch 99 Ranch weekly deals directly from their JSON API. No browser.

99 Ranch is not carried by Flipp, so it keeps its own fetcher. The previous
version drove headless Chromium to the search page and passively listened for
the XHR the page fires, keeping the listener for a fixed 1.5s. It captured
nothing in 5 of 8 recorded weeks and wrote a well-formed 337-byte file with zero
products each time, which then imported as an empty batch.

The API needs exactly one header, `storeid`. No cookie, no token, no user-agent
check — verified by subtracting headers one at a time until only `storeid`
remained. `pageSize=500` returns every item in one call, so the pagination that
replayed captured browser headers is gone too.

The trap this replaces: the API answers HTTP 200 even on failure, putting the
error inside the body (`{"code":101002,"message":"TimeOut Error","data":null}`).
Checking only the HTTP status reports success on exactly the failure that used to
hurt. Validate `code == 0` instead.
"""
import os
import sys
import json
import time
import logging
from datetime import date, datetime, timedelta

import requests

STORE_NAME = "99 Ranch"
STORE_ID = int(os.getenv("RANCH99_STORE_ID", "8899"))   # Mountain View
API_URL = "https://www.99ranch.com/be-api/search/web/products"

# themeId 18 = "Special Offer" (the weekly sale), 51 = "99 Ranch Own Brand"
THEME_IDS = [18, 51]
PAGE_SIZE = 500          # verified to return every item; hasNextPage comes back false
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
USER_AGENT = "Remi-grocery-agent/1.0 (weekly flyer ingest; contact: kaiwufun@gmail.com)"

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "99ranch")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


class Ranch99Error(RuntimeError):
    pass


def get_week_range(today=None):
    """99 Ranch ad weeks run Friday -> Thursday.

    The API returns no validity dates, so this window is computed locally and
    stamped on. Fetching on the wrong day therefore labels deals with the wrong
    week — which is why the scheduler runs daily rather than on a fixed weekday.
    """
    today = today or date.today()
    days_since_friday = (today.weekday() - 4) % 7
    valid_from = today - timedelta(days=days_since_friday)
    return valid_from.isoformat(), (valid_from + timedelta(days=6)).isoformat()


def fetch_theme(theme_id):
    """All products for one theme. Raises rather than returning empty."""
    payload = {"themeId": str(theme_id), "page": 1, "pageSize": PAGE_SIZE, "availability": 1}
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                API_URL, json=payload, timeout=REQUEST_TIMEOUT,
                headers={
                    "storeid": str(STORE_ID),
                    "content-type": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
            resp.raise_for_status()
            body = resp.json()

            # HTTP 200 with an error envelope is the normal failure shape here.
            if body.get("code") != 0 or not body.get("success"):
                raise Ranch99Error(
                    f"themeId={theme_id}: API returned code={body.get('code')} "
                    f"message={body.get('message')!r}"
                )

            data = body.get("data") or {}
            items = data.get("list") or []
            total = data.get("total", 0)
            if not items:
                raise Ranch99Error(f"themeId={theme_id}: API reported success but returned 0 products")
            if data.get("hasNextPage"):
                raise Ranch99Error(
                    f"themeId={theme_id}: more than {PAGE_SIZE} products; pagination needed"
                )
            if len(items) != total:
                raise Ranch99Error(f"themeId={theme_id}: got {len(items)} products, API says total={total}")

            log.info(f"  themeId={theme_id}: {len(items)} products")
            return items
        except Ranch99Error:
            raise                      # an API-level error will not fix itself on retry
        except Exception as exc:       # noqa: BLE001 - transport errors are worth retrying
            last = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    raise Ranch99Error(f"themeId={theme_id}: request failed after {MAX_RETRIES} attempts: {last}")


def run(out_dir=None):
    valid_from, valid_to = get_week_range()
    out_dir = out_dir or FLYERS_DIR
    os.makedirs(out_dir, exist_ok=True)

    all_products, theme_meta = [], []
    for tid in THEME_IDS:
        products = fetch_theme(tid)
        all_products.extend(products)
        theme_meta.append({"theme_id": tid, "count": len(products)})

    seen, unique = set(), []
    for p in all_products:
        pid = p.get("productId")
        if pid not in seen:
            seen.add(pid)
            unique.append(p)

    if not unique:
        raise Ranch99Error("0 products after de-duplication; refusing to write a file")

    output = {
        "store": STORE_NAME,
        "store_id": STORE_ID,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "fetched_at": datetime.now().isoformat(),
        "themes": theme_meta,
        "products": unique,
    }

    # Write-then-rename. A file at the final path always means a fetch that
    # actually succeeded, so nothing downstream has to second-guess it.
    path = os.path.join(out_dir, f"99ranch_{valid_from}_raw.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

    print(f"\n=== Download complete ===")
    print(f"Raw JSON: {path}")
    print(f"Products: {len(unique)}")
    print(f"Valid:    {valid_from} -> {valid_to}")
    for t in theme_meta:
        print(f"  themeId={t['theme_id']}: {t['count']} items")
    return path


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
