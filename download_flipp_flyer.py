"""Fetch a weekly flyer from Flipp's open API and write it in the importer's shape.

One module serves every Flipp merchant; the store is a parameter, not a copy of
this file. Flipp covers Safeway, Cardenas and H Mart (and ~15 other local
grocers); 99 Ranch is not on Flipp and keeps its own fetcher.

Three endpoints, no authentication:
  /flipp/data?postal_code=   -> this postal code's flyers, with ids and dates
  /flipp/flyers/{flyer_id}   -> every item id in that flyer (complete coverage)
  /flipp/items/{item_id}     -> the rich record: description, price_text, unit

The per-item call is what makes unit prices possible: `description` carries the
pack size ("64 - oz.", "6 - lbs. or more.") that the flyer listing omits. It
costs one request per item, roughly 200 per store per week.

Failure is always an exception. This module must never write a file describing a
week it could not actually fetch — an empty-but-well-formed artifact is what let
earlier outages pass as successful runs.
"""
import os
import re
import sys
import json
import time
import logging
from datetime import date, datetime

import requests

import stores

BASE = "https://backflipp.wishabi.com/flipp"
POSTAL_CODE = os.getenv("FLIPP_POSTAL_CODE", "94086")
USER_AGENT = "Remi-grocery-agent/1.0 (weekly flyer ingest; contact: kaiwufun@gmail.com)"
REQUEST_PAUSE = 0.15          # be a polite neighbour; ~200 calls per store
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

# Derived from stores.py, which is the one place stores are declared. Keeping a
# second copy here is what let the same store carry two names in two files, and
# this module's value is what ends up in both batches.store_name and
# pipeline_runs.store_name.
MERCHANTS = {
    key: {"flipp_name": cfg["flipp_merchant"], "store_name": cfg["display_name"]}
    for key, cfg in stores.STORES.items()
    if cfg.get("source") == "flipp"
}

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def _neg_date(iso):
    """Sort key that makes a later date rank first."""
    return tuple(-int(x) for x in iso.split("-")) if iso else (0, 0, 0)


class FlippError(RuntimeError):
    pass


def _get(url, params=None):
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url, params=params, timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:            # noqa: BLE001 - retried, then raised
            last = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    raise FlippError(f"GET {url} failed after {MAX_RETRIES} attempts: {last}")


def find_flyer(merchant_key, postal_code=POSTAL_CODE):
    """Return (flyer_id, valid_from, valid_to) for this merchant's current flyer."""
    cfg = MERCHANTS[merchant_key]
    data = _get(f"{BASE}/data", {"postal_code": postal_code, "locale": "en-us"})
    flyers = data.get("flyers") or []
    if not flyers:
        raise FlippError(f"no flyers at all for postal_code={postal_code}")

    matches = [f for f in flyers if (f.get("merchant") or "").strip() == cfg["flipp_name"]]
    if not matches:
        available = sorted({(f.get("merchant") or "").strip() for f in flyers})
        raise FlippError(
            f"merchant '{cfg['flipp_name']}' not in this week's {len(flyers)} flyers. "
            f"Available: {', '.join(available)}"
        )

    # Stores publish on different cadences, so "the newest flyer" picks next
    # week for one store and this week for another, and can publish a week that
    # has not started. Prefer the flyer valid today; otherwise the one starting
    # soonest. Predictable, and never gets ahead of itself.
    today = datetime.now().date().isoformat()

    def window(f):
        return (f.get("valid_from") or "")[:10], (f.get("valid_to") or "")[:10]

    current = [f for f in matches if window(f)[0] <= today <= window(f)[1]]
    if current:
        # Several flyers can cover today: the real weekly ad plus long-running
        # supplemental ones (Lucky and FoodMaxx both publish 4-week flyers
        # alongside their weekly). Taking the one that ends latest picks the
        # supplemental, which carries far fewer items — the gate then rejects it
        # every week and the store freezes. Prefer the tightest window.
        def span(f):
            a, b = window(f)
            return (date.fromisoformat(b) - date.fromisoformat(a)).days if a and b else 999
        flyer = min(current, key=lambda f: (span(f), _neg_date(window(f)[0])))
    else:
        upcoming = [f for f in matches if window(f)[0] > today]
        if not upcoming:
            windows = ", ".join(f"{a}..{b}" for a, b in map(window, matches))
            raise FlippError(
                f"{cfg['flipp_name']}: no flyer covers today ({today}) and none upcoming. Saw: {windows}"
            )
        flyer = min(upcoming, key=lambda f: window(f)[0])
        log.info(f"  no flyer valid today; taking the next one starting {window(flyer)[0]}")

    vf, vt = window(flyer)
    return flyer["id"], vf, vt


def fetch_items(flyer_id):
    """Every item in the flyer, each enriched with its detail record."""
    listing = _get(f"{BASE}/flyers/{flyer_id}", {"locale": "en-us"})
    stubs = listing.get("items") or []
    if not stubs:
        raise FlippError(f"flyer {flyer_id} returned no items")

    items = []
    for i, stub in enumerate(stubs, 1):
        detail = _get(f"{BASE}/items/{stub['id']}", {"locale": "en-us", "postal_code": POSTAL_CODE})
        merged = dict(stub)
        merged.update(detail.get("item") or {})
        items.append(merged)
        if i % 50 == 0:
            log.info(f"  {i}/{len(stubs)} items")
        time.sleep(REQUEST_PAUSE)

    if len(items) != len(stubs):
        raise FlippError(f"expected {len(stubs)} items, assembled {len(items)}")
    return items


# --- unit extraction ---------------------------------------------------------
# Coverage measured at 95.9% of priced Safeway items using all three tiers.
# The name alone yields only 18%, so the per-item description call is load-bearing.

_SIZE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:to\s*\d+(?:\.\d+)?\s*)?-?\s*"
    r"(oz|fl\.?\s?oz|lb|lbs|ct|pk|pack|g|kg|ml|ltr|l|gal|qt|pt)\b",
    re.I,
)
_PRICE_TEXT_UNIT = re.compile(r"\b(lb|ea|oz)\b", re.I)
_MEMBER_UNIT = re.compile(r"member price:\s*\$?([\d.]+)\s*(?:per\s+)?(ea|lb|oz)\b", re.I)
# H Mart states units bare, with no quantity: "LB", "BOX", "6 MM/LB",
# "16-20'S/LB". A trailing or standalone unit means "priced per that unit",
# which is how most meat and produce is sold.
_BARE_UNIT = re.compile(
    r"(?:^|/)\s*(lb|lbs|ea|each|oz|ct|pk|box|bag|bunch|dozen|doz)\s*$", re.I
)

_UNIT_CANON = {
    "lbs": "lb", "pack": "pk", "ltr": "l", "fl oz": "fl oz", "fl.oz": "fl oz",
}


def extract_unit(name, description, price_text):
    """Return (unit, package_amount, per_unit_volume, member_unit_price)."""
    blob = f"{name or ''} {description or ''}"

    member = _MEMBER_UNIT.search(description or "")
    member_price = float(member.group(1)) if member else None
    member_unit = member.group(2).lower() if member else None

    m = _SIZE.search(blob)
    if m:
        amount = float(m.group(1))
        unit = _UNIT_CANON.get(m.group(2).lower().replace(".", "").strip(), m.group(2).lower())
        return unit, 1, f"{amount:g} {unit}", (member_price, member_unit)

    bare = _BARE_UNIT.search((description or "").strip())
    if bare:
        unit = _UNIT_CANON.get(bare.group(1).lower(), bare.group(1).lower())
        return unit, 1, None, (member_price, member_unit)

    if price_text:
        pt = _PRICE_TEXT_UNIT.search(price_text)
        if pt:
            return pt.group(1).lower(), 1, None, (member_price, member_unit)

    if member_unit:
        return member_unit, 1, None, (member_price, member_unit)

    return None, 1, None, (member_price, member_unit)


def _to_price(raw):
    if raw in (None, ""):
        return None
    try:
        return round(float(str(raw).replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return None


def to_deal(item, store_name, valid_from, valid_to):
    name = " ".join((item.get("name") or "").split())
    if not name:
        return None

    description = item.get("description") or ""
    price_text = item.get("price_text") or ""
    price = _to_price(item.get("price") or item.get("current_price"))

    unit, package_amount, per_unit_volume, (member_price, member_unit) = extract_unit(
        name, description, price_text
    )

    # On a multi-buy the headline price is the bundle ("$5.00" for 2), while the
    # per-unit price hides in the description ("Member Price: $2.50 ea."). Using
    # the headline would double every unit price on those items.
    if member_price is not None and member_unit:
        price = member_price
        unit = unit or member_unit

    return {
        "product_name": name,
        "brand": (item.get("brand") or None),
        "price_flyer": price,
        "original_price": _to_price(item.get("original_price")),
        "unit_flyer": unit,
        "package_amount": package_amount,
        "per_unit_volume": per_unit_volume,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "category": None,            # Flipp's own categories are empty; inferred later
        "deal_type": (item.get("sale_story") or None),
        "deal_conditions": description or None,
        "quality_tags": [],
        "source_item_id": item.get("id"),
    }


def run(merchant_key, postal_code=POSTAL_CODE, out_dir=None):
    if merchant_key not in MERCHANTS:
        raise FlippError(f"unknown merchant '{merchant_key}'. Known: {', '.join(MERCHANTS)}")
    store_name = MERCHANTS[merchant_key]["store_name"]

    flyer_id, valid_from, valid_to = find_flyer(merchant_key, postal_code)
    log.info(f"{store_name}: flyer {flyer_id}, {valid_from} -> {valid_to}")

    items = fetch_items(flyer_id)
    deals = [d for d in (to_deal(i, store_name, valid_from, valid_to) for i in items) if d]
    if not deals:
        raise FlippError(f"{store_name}: flyer {flyer_id} produced 0 usable deals")

    priced = sum(1 for d in deals if d["price_flyer"] is not None)
    united = sum(1 for d in deals if d["unit_flyer"])
    log.info(f"{store_name}: {len(deals)} deals, {priced} priced, {united} with a unit")

    out_dir = out_dir or os.path.join(FLYERS_DIR, merchant_key)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{merchant_key}_{valid_from}_deals.json")
    tmp = path + ".tmp"
    payload = {
        "store": store_name,
        "source": "flipp",
        "flyer_id": flyer_id,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "parsed_at": datetime.now().isoformat(),
        "deals_count": len(deals),
        "priced_count": priced,
        "unit_count": united,
        "deals": deals,
    }
    # Write-then-rename: a crashed run must not leave a half-file that the next
    # stage happily reads, and skip-if-exists must never see a partial artifact.
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

    print(f"\n=== {store_name} ===")
    print(f"Flyer:   {flyer_id}  {valid_from} -> {valid_to}")
    print(f"Deals:   {len(deals)}  (priced {priced}, with unit {united})")
    print(f"Output:  {path}")
    return path


if __name__ == "__main__":
    targets = sys.argv[1:] or list(MERCHANTS)
    for key in targets:
        run(key)
