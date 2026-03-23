import os
import json
import logging
import requests
from datetime import datetime

STORE_NAME   = "Cardenas"
FLIPP_SLUG   = "cardenas"
ACCESS_TOKEN = "0f5c3d7bf6e60e86eec604efa4c2ac19"
POSTAL_CODE  = "94086"
STORE_CODE   = "215"          # East Palo Alto — nearest NorCal Cardenas
FLYERS_DIR   = os.path.join(os.path.dirname(__file__), "flyers", "cardenas")

BASE_URL = "https://dam.flippenterprise.net/flyerkit"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def get_current_publication():
    """Fetch current Cardenas publication from Flipp API."""
    url = (
        f"{BASE_URL}/publications/{FLIPP_SLUG}"
        f"?languages[]=en&locale=en"
        f"&access_token={ACCESS_TOKEN}"
        f"&show_storefronts=true"
        f"&postal_code={POSTAL_CODE}"
        f"&store_code={STORE_CODE}"
    )
    log.info("Fetching publication list from Flipp API...")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    publications = resp.json()
    if not publications:
        raise RuntimeError("Flipp API returned no publications.")

    pub = publications[0]
    log.info(
        f"Found publication: '{pub['name']}' "
        f"(valid {pub['valid_from'][:10]} to {pub['valid_to'][:10]})"
    )
    return pub


def fetch_products(pub_id: int) -> list:
    """Fetch structured product deals for a publication."""
    url = (
        f"{BASE_URL}/publication/{pub_id}/products"
        f"?display_type=all&locale=en&access_token={ACCESS_TOKEN}"
    )
    log.info(f"Fetching products for publication {pub_id}...")
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    products = resp.json()
    log.info(f"Fetched {len(products)} products")
    return products


def run():
    os.makedirs(FLYERS_DIR, exist_ok=True)

    pub = get_current_publication()
    valid_from = pub["valid_from"][:10]
    valid_to   = pub["valid_to"][:10]
    pub_id     = pub["id"]

    # Skip if already downloaded
    raw_path = os.path.join(FLYERS_DIR, f"cardenas_{valid_from}_raw.json")
    if os.path.exists(raw_path):
        log.info(f"Raw data already exists, skipping: {raw_path}")
    else:
        products = fetch_products(pub_id)

        output = {
            "store":        STORE_NAME,
            "publication_id": pub_id,
            "valid_from":   valid_from,
            "valid_to":     valid_to,
            "fetched_at":   datetime.now().isoformat(),
            "pdf_url":      pub.get("pdf_url"),
            "products":     products,
        }
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        log.info(f"Saved raw data: {raw_path}")

    print("\n=== Download complete ===")
    print(f"Raw JSON: {raw_path}")
    print(f"Valid:    {valid_from} → {valid_to}")


if __name__ == "__main__":
    run()
