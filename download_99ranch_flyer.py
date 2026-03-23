import os
import json
import logging
from datetime import date, timedelta
from playwright.sync_api import sync_playwright

STORE_NAME = "99 Ranch"
STORE_ID   = 8899        # Mountain View store code
FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "99ranch")

# themeId=18 → "Special Offer" (weekly sale)
# themeId=51 → "99 Ranch Own Brand" (private label deals)
THEME_IDS = [18, 51]

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def get_week_range():
    """99 Ranch weekly ads run Friday → Thursday."""
    today = date.today()
    days_since_friday = (today.weekday() - 4) % 7  # weekday 4 = Friday
    valid_from = today - timedelta(days=days_since_friday)
    valid_to   = valid_from + timedelta(days=6)
    return valid_from.isoformat(), valid_to.isoformat()


API_URL = "https://www.99ranch.com/be-api/search/web/products"


def get_theme_products(page, theme_id):
    """
    Navigate to the themeId page, capture the real request headers from the
    first API call, then use those headers to paginate through all pages.
    Returns (products_list, theme_name, total_count).
    """
    import json as _json

    captured_headers = {}
    first_page_data  = [None]

    def handle_request(req):
        if "be-api/search/web/products" in req.url:
            captured_headers.update(dict(req.headers))

    def handle_response(resp):
        if "be-api/search/web/products" in resp.url and first_page_data[0] is None:
            try:
                data = resp.json()
                if data.get("data") and data["data"].get("list"):
                    first_page_data[0] = data["data"]
                    log.info(
                        f"  themeId={theme_id} page 1: {len(data['data']['list'])} items "
                        f"(total={data['data']['total']})"
                    )
            except Exception:
                pass

    page.on("request",  handle_request)
    page.on("response", handle_response)

    page.goto(
        f"https://www.99ranch.com/search?themeId={theme_id}",
        wait_until="networkidle",
        timeout=60000,
    )
    page.wait_for_timeout(1500)

    page.remove_listener("request",  handle_request)
    page.remove_listener("response", handle_response)

    if first_page_data[0] is None:
        log.warning(f"  themeId={theme_id}: no API response captured")
        return [], "", 0

    all_products = list(first_page_data[0].get("list", []))
    total        = first_page_data[0].get("total", 0)
    has_next     = first_page_data[0].get("hasNextPage", False)
    page_num     = 2

    # Paginate remaining pages using captured headers
    headers_json = _json.dumps(captured_headers)

    while has_next:
        payload = {
            "page":         page_num,
            "pageSize":     28,
            "themeId":      str(theme_id),
            "availability": 1,
        }
        payload_json = _json.dumps(payload)

        result = page.evaluate(
            f"""async () => {{
                const headers = {headers_json};
                const resp = await fetch("{API_URL}", {{
                    method: "POST",
                    headers: headers,
                    body: JSON.stringify({payload_json}),
                }});
                return await resp.json();
            }}"""
        )

        data  = (result or {}).get("data") or {}
        items = data.get("list") or []
        if not items:
            break

        all_products.extend(items)
        log.info(
            f"  themeId={theme_id} page {page_num}: {len(items)} items "
            f"(total={total}, fetched={len(all_products)})"
        )

        has_next  = data.get("hasNextPage", False)
        page_num += 1

    # Deduplicate by productId
    seen, unique = set(), []
    for p in all_products:
        pid = p.get("productId")
        if pid not in seen:
            seen.add(pid)
            unique.append(p)

    return unique, "", total


def run():
    os.makedirs(FLYERS_DIR, exist_ok=True)
    valid_from, valid_to = get_week_range()

    raw_path = os.path.join(FLYERS_DIR, f"99ranch_{valid_from}_raw.json")
    if os.path.exists(raw_path):
        log.info(f"Raw data already exists, skipping: {raw_path}")
        print(f"\n=== Already downloaded ===\nRaw JSON: {raw_path}")
        return

    log.info("Launching headless browser to fetch 99 Ranch deals via be-api...")

    all_products = []
    theme_meta   = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()

        for tid in THEME_IDS:
            log.info(f"Fetching themeId={tid}...")
            products, theme_name, total = get_theme_products(page, tid)
            log.info(f"  themeId={tid}: {len(products)} products fetched (API total={total})")
            all_products.extend(products)
            theme_meta.append({"theme_id": tid, "theme_name": theme_name, "count": len(products)})

        browser.close()

    # Deduplicate across themes by productId
    seen, unique = set(), []
    for p in all_products:
        pid = p.get("productId")
        if pid not in seen:
            seen.add(pid)
            unique.append(p)

    from datetime import datetime
    output = {
        "store":       STORE_NAME,
        "store_id":    STORE_ID,
        "valid_from":  valid_from,
        "valid_to":    valid_to,
        "fetched_at":  datetime.now().isoformat(),
        "themes":      theme_meta,
        "products":    unique,
    }

    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info(f"Saved raw data: {raw_path}")

    print(f"\n=== Download complete ===")
    print(f"Raw JSON: {raw_path}")
    print(f"Products: {len(unique)}")
    print(f"Valid:    {valid_from} → {valid_to}")
    for t in theme_meta:
        print(f"  themeId={t['theme_id']}: {t['count']} items")


if __name__ == "__main__":
    run()
