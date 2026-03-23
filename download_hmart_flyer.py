import os
import re
import json
import logging
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

STORE_NAME = "H Mart"
PAGE_URL   = "https://www.hmart.com/weekly-ads/northern-california"
FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "hmart")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def parse_hmart_date(date_str: str, year: str) -> str:
    """Parse 'MAR. 11TH' or 'MAR 11' style to 'YYYY-MM-DD'."""
    cleaned = re.sub(r"(\d+)(ST|ND|RD|TH)", r"\1", date_str.upper())
    cleaned = cleaned.replace(".", "").strip()
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(f"{cleaned} {year}", fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Could not parse H Mart date: {date_str!r}")


def get_flyer_info():
    """
    Use Playwright to render the VTEX page (client-side JS) and extract
    the weekly ad image CDN URL and valid date range.
    """
    log.info("Launching headless browser to fetch H Mart weekly ad...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(PAGE_URL, wait_until="networkidle", timeout=60000)

        # Wait for the flyer image to be rendered by React
        page.wait_for_selector('img[alt*="Weekly Ad Northern California"]', timeout=30000)

        img_el    = page.locator('img[alt*="Weekly Ad Northern California"]').first
        image_url = img_el.get_attribute("src")
        page_text = page.inner_text("body")
        browser.close()

    if not image_url:
        raise RuntimeError("Could not find weekly ad image URL on H Mart page")

    # Extract date range, e.g. "MAR. 11TH - MAR. 17TH, 2026"
    date_pattern = re.compile(
        r"([A-Z]{3}\.?\s+\d+(?:ST|ND|RD|TH)?)\s*[-\u2013]\s*"
        r"([A-Z]{3}\.?\s+\d+(?:ST|ND|RD|TH)?),?\s*(\d{4})",
        re.IGNORECASE,
    )
    m = date_pattern.search(page_text)
    if not m:
        raise RuntimeError("Could not find date range in H Mart page text")

    from_str, to_str, year = m.group(1), m.group(2), m.group(3)
    valid_from = parse_hmart_date(from_str, year)
    valid_to   = parse_hmart_date(to_str, year)

    return image_url, valid_from, valid_to


def download_image(image_url: str, valid_from: str) -> str:
    """Download the flyer JPG to flyers/hmart/. Skip if already present."""
    os.makedirs(FLYERS_DIR, exist_ok=True)
    filepath = os.path.join(FLYERS_DIR, f"hmart_{valid_from}.jpg")

    if os.path.exists(filepath):
        log.info(f"Flyer already exists, skipping: {filepath}")
        return filepath

    log.info(f"Downloading H Mart flyer from CDN...")
    resp = requests.get(image_url, headers=HEADERS, timeout=60, stream=True)
    resp.raise_for_status()

    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    size_kb = os.path.getsize(filepath) // 1024
    log.info(f"Flyer saved: {filepath} ({size_kb} KB)")
    return filepath


def run():
    try:
        image_url, valid_from, valid_to = get_flyer_info()
        image_path = download_image(image_url, valid_from)

        meta = {
            "store":      STORE_NAME,
            "valid_from": valid_from,
            "valid_to":   valid_to,
            "image_url":  image_url,
            "image_path": image_path,
            "fetched_at": datetime.now().isoformat(),
        }
        meta_path = os.path.join(FLYERS_DIR, f"hmart_{valid_from}_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        print("\n=== Download complete ===")
        print(f"Image:  {image_path}")
        print(f"Meta:   {meta_path}")
        print(f"Valid:  {valid_from} → {valid_to}")

    except Exception as e:
        log.error(f"Error: {e}")
        raise


if __name__ == "__main__":
    run()
