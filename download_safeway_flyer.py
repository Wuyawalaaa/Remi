import os
import json
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

FLIPP_ACCESS_TOKEN = os.getenv("FLIPP_ACCESS_TOKEN")
POSTAL_CODE = os.getenv("FLIPP_POSTAL_CODE", "94086")
FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "safeway")
FLIPP_DATA_DIR = os.path.join(os.path.dirname(__file__), "flyers", "safeway", "flipp_data")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger(__name__)


def get_current_publication():
    """Fetch the current Safeway weekly ad publication from Flipp API."""
    if not FLIPP_ACCESS_TOKEN:
        raise RuntimeError("FLIPP_ACCESS_TOKEN not set in .env")

    url = (
        f"https://api.flipp.com/flyerkit/v4.0/publications/safeway"
        f"?access_token={FLIPP_ACCESS_TOKEN}"
        f"&locale=en-US"
        f"&postal_code={POSTAL_CODE}"
    )
    log.info("Fetching publication list from Flipp API...")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    publications = resp.json()
    if not publications:
        raise RuntimeError("Flipp API returned no publications.")

    # First result is the current weekly ad
    pub = publications[0]
    log.info(
        f"Found publication: '{pub['name']}' "
        f"(valid {pub['valid_from'][:10]} to {pub['valid_to'][:10]})"
    )
    return pub


def download_pdf(pdf_url, valid_from):
    """Download the weekly ad PDF. Skip if already downloaded."""
    os.makedirs(FLYERS_DIR, exist_ok=True)

    date_str = valid_from[:10]  # YYYY-MM-DD
    filename = f"safeway_{date_str}.pdf"
    filepath = os.path.join(FLYERS_DIR, filename)

    if os.path.exists(filepath):
        log.info(f"PDF already exists, skipping download: {filepath}")
        return filepath

    log.info(f"Downloading PDF from {pdf_url} ...")
    resp = requests.get(pdf_url, headers=HEADERS, timeout=30, stream=True)
    resp.raise_for_status()

    with open(filepath, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    size_kb = os.path.getsize(filepath) // 1024
    log.info(f"PDF saved: {filepath} ({size_kb} KB)")
    return filepath


def fetch_flipp_ground_truth(storefront_payload_url, valid_from):
    """
    Fetch and save the Flipp structured deal data (SFML) for this week.
    This will serve as ground truth when validating OCR accuracy later.
    """
    os.makedirs(FLIPP_DATA_DIR, exist_ok=True)

    date_str = valid_from[:10]
    json_path = os.path.join(FLIPP_DATA_DIR, f"safeway_flipp_{date_str}.json")

    if os.path.exists(json_path):
        log.info(f"Flipp ground truth already saved: {json_path}")
        return json_path

    log.info("Fetching Flipp structured data for ground truth...")
    resp = requests.get(storefront_payload_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    # Parse SFML (XML) and extract deal items
    # Flipp uses namespace http://schemas.flipp.com/sfml/0.1 on all tags
    NS = "http://schemas.flipp.com/sfml/0.1"
    root = ET.fromstring(resp.content)
    items = []

    for area in root.iter(f"{{{NS}}}area"):
        label = area.get("label", "").strip()
        item_id = area.get("item-id", "")
        if not label:
            continue

        parts = [p.strip() for p in label.split(",")]

        items.append({
            "item_id": item_id,
            "raw_label": label,
            "parts": parts,
        })

    output = {
        "source": "flipp_api",
        "fetched_at": datetime.now().isoformat(),
        "valid_from": valid_from[:10],
        "total_items": len(items),
        "items": items,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Flipp ground truth saved: {json_path} ({len(items)} items)")
    return json_path


def run():
    try:
        pub = get_current_publication()

        pdf_path = download_pdf(pub["pdf_url"], pub["valid_from"])

        flipp_path = fetch_flipp_ground_truth(
            pub["storefront_payload_url"], pub["valid_from"]
        )

        print("\n=== Download complete ===")
        print(f"PDF:          {pdf_path}")
        print(f"Flipp data:   {flipp_path}")
        print(f"Valid:        {pub['valid_from'][:10]} → {pub['valid_to'][:10]}")

    except requests.exceptions.RequestException as e:
        log.error(f"Network error: {e}")
        raise
    except Exception as e:
        log.error(f"Unexpected error: {e}")
        raise


if __name__ == "__main__":
    run()
