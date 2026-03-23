"""
Validate OCR+parse accuracy against Flipp ground truth.
Outputs a detailed report with match confidence and price comparison.
"""
import json
import re
import difflib

FLIPP_PATH = "flyers/safeway/flipp_data/safeway_flipp_2026-02-18.json"
OCR_PATH   = "flyers/safeway/safeway_2026-02-18_deals.json"


# ── Flipp price parser ────────────────────────────────────────────────────────

def parse_flipp_price(label: str):
    """
    Extract normalized sale price from a Flipp label string.
    Returns (price, unit) or (None, None).
    """
    label_lower = label.lower()

    # "X for $Y" → Y/X per ea
    m = re.search(r'(\d+)\s+for\s+\$(\d+\.?\d*)', label_lower)
    if m:
        qty, total = int(m.group(1)), float(m.group(2))
        return round(total / qty, 2), "ea"

    # BUY 1 GET 1 FREE — price not extractable as number
    if "buy 1 get 1 free" in label_lower or "b1g1" in label_lower:
        return None, "bogo"

    # "BUY 2 GET 2 FREE" etc.
    if "buy 2 get 2 free" in label_lower:
        return None, "b2g2"

    # "$X.XX lb/ea/gal/oz member price"
    m = re.search(r'\$(\d+\.?\d*)\s*(lb|oz|ea|gal|fl\s*oz)?', label_lower)
    if m:
        price = float(m.group(1))
        unit  = (m.group(2) or "ea").strip().replace(" ", "_")
        return price, unit

    # "X% off" — can't extract absolute price
    if "%" in label_lower:
        return None, "pct_off"

    return None, None


def normalize_name(name: str) -> str:
    """Lowercase, strip brand marks and common filler words for comparison."""
    name = name.lower()
    name = re.sub(r'[®™©]', '', name)
    name = re.sub(r'\b(signature select|waterfront bistro|open nature|'
                  r'o organics|primo taglio|lucerne|member price|'
                  r'value pack|mega pack|sold by the pound|selected varieties)\b', '', name)
    name = re.sub(r'\d+(\.\d+)?\s*(oz|lb|ct|pk|fl oz|gal|ml|liter)', '', name)
    name = re.sub(r'[^a-z0-9\s]', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def best_match(ocr_name: str, flipp_items: list, threshold=0.45):
    """
    Find the best-matching Flipp item for an OCR product name.
    Returns (flipp_item, score) or (None, 0).
    """
    norm_ocr = normalize_name(ocr_name)
    best_item, best_score = None, 0

    for item in flipp_items:
        norm_flipp = normalize_name(item["raw_label"].split(",")[0])
        score = difflib.SequenceMatcher(None, norm_ocr, norm_flipp).ratio()
        if score > best_score:
            best_score = score
            best_item = item

    if best_score >= threshold:
        return best_item, best_score
    return None, 0


# ── Main validation ───────────────────────────────────────────────────────────

def run():
    with open(FLIPP_PATH) as f:
        flipp_data = json.load(f)
    with open(OCR_PATH) as f:
        ocr_data = json.load(f)

    flipp_items = flipp_data["items"]
    ocr_deals   = ocr_data["deals"]

    results = {
        "matched":        [],
        "price_correct":  [],
        "price_wrong":    [],
        "bogo_skipped":   [],
        "no_match":       [],
    }

    for deal in ocr_deals:
        ocr_name  = deal["product_name"]
        ocr_price = deal.get("sale_price")

        item, score = best_match(ocr_name, flipp_items, threshold=1.0)

        if item is None:
            results["no_match"].append(ocr_name)
            continue

        flipp_price, flipp_unit = parse_flipp_price(item["raw_label"])

        if flipp_unit in ("bogo", "b2g2", "pct_off") or flipp_price is None:
            results["bogo_skipped"].append({
                "ocr_name":   ocr_name,
                "flipp_label": item["raw_label"],
                "score":      round(score, 2),
            })
            continue

        results["matched"].append(ocr_name)

        price_match = (ocr_price is not None and abs(ocr_price - flipp_price) < 0.02)

        if price_match:
            results["price_correct"].append({
                "name":        ocr_name,
                "price":       ocr_price,
                "score":       round(score, 2),
            })
        else:
            results["price_wrong"].append({
                "name":        ocr_name,
                "ocr_price":   ocr_price,
                "flipp_price": flipp_price,
                "flipp_label": item["raw_label"],
                "score":       round(score, 2),
            })

    # ── Report ────────────────────────────────────────────────────────────────

    total_ocr      = len(ocr_deals)
    n_matched      = len(results["matched"])
    n_correct      = len(results["price_correct"])
    n_wrong        = len(results["price_wrong"])
    n_bogo         = len(results["bogo_skipped"])
    n_no_match     = len(results["no_match"])
    comparable     = n_correct + n_wrong  # excludes BOGO/pct_off

    print("=" * 60)
    print("OCR ACCURACY REPORT — Safeway 2026-02-18")
    print("=" * 60)
    print(f"\nTotal OCR deals:            {total_ocr}")
    print(f"Matched to Flipp:           {n_matched + n_bogo}  ({round((n_matched+n_bogo)/total_ocr*100,1)}%)")
    print(f"  - With comparable price:  {comparable}")
    print(f"  - BOGO/pct-off (skipped): {n_bogo}")
    print(f"No match found:             {n_no_match}")

    if comparable:
        acc = round(n_correct / comparable * 100, 1)
        print(f"\nPrice accuracy (comparable items):")
        print(f"  Correct:  {n_correct} / {comparable}  →  {acc}%")
        print(f"  Wrong:    {n_wrong}")

    print(f"\n{'─'*60}")
    print(f"PRICE MISMATCHES ({n_wrong}):")
    print(f"{'─'*60}")
    for r in sorted(results["price_wrong"], key=lambda x: x["score"], reverse=True):
        print(f"\n  OCR:   {r['name'][:55]}")
        print(f"  Flipp: {r['flipp_label'][:70]}")
        print(f"  Price: OCR ${r['ocr_price']}  vs  Flipp ${r['flipp_price']}  (match score {r['score']})")

    print(f"\n{'─'*60}")
    print(f"NO MATCH ({n_no_match}) — OCR items not found in Flipp:")
    print(f"{'─'*60}")
    for name in results["no_match"][:20]:
        print(f"  {name}")
    if n_no_match > 20:
        print(f"  ... and {n_no_match - 20} more")


if __name__ == "__main__":
    run()
