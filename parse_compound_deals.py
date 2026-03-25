import copy
import re
import uuid


_CONNECTOR_RE = re.compile(r"\s+(?:or|o)\s+", re.IGNORECASE)
_PACKAGE_ONLY_RE = re.compile(
    r"^[\d\s\.,\-–]*(oz|lb|ct|pk|ml|liter|liters|kg|g|fl\.?\s*oz|pack|bag|bottles?|cans?|tetra|inch|in)\b.*$",
    re.IGNORECASE,
)
_TRAILING_SIZE_RE = re.compile(
    r"[\s,]+\d+(?:[\.\-]\d+)?[-\s]*(?:oz|lb|ct|pk|ml|liter|liters|kg|g|fl\.?\s*oz|pack|bag|bottles?|cans?|tetra|inch|in)\.?$",
    re.IGNORECASE,
)
_WEAK_LABELS = {
    "bottle", "bottles", "can", "cans", "tetra", "selected varieties", "assorted varieties",
}


def _clean(text):
    text = str(text or "").replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip(" ,;-")
    return text


def _is_package_fragment(text):
    text = _clean(text).lower()
    if not text:
        return False
    if text in _WEAK_LABELS:
        return True
    return bool(_PACKAGE_ONLY_RE.match(text))


def _strip_package_tail(text):
    text = _clean(text)
    if not text:
        return text
    tokens = [tok.strip() for tok in text.split(",")]
    while len(tokens) > 1 and _is_package_fragment(tokens[-1]):
        tokens.pop()
    text = _clean(", ".join(tokens))
    text = _TRAILING_SIZE_RE.sub("", text).strip(" ,;-")
    return _clean(text)


def _looks_like_split_label(text):
    text = _strip_package_tail(text)
    words = re.findall(r"[A-Za-z]+", text.lower())
    if not text or not words:
        return False
    if _is_package_fragment(text):
        return False
    if len(words) == 1 and words[0] in _WEAK_LABELS:
        return False
    return True


def _split_commas(segment):
    parts = [_clean(part) for part in segment.split(",") if _clean(part)]
    if len(parts) <= 1:
        return [_clean(segment)] if _clean(segment) else []

    merged = []
    current = parts[0]
    for part in parts[1:]:
        if _is_package_fragment(part):
            current = _clean(f"{current}, {part}")
        elif _is_package_fragment(current):
            current = _clean(f"{current}, {part}")
        else:
            merged.append(current)
            current = part
    merged.append(current)
    return [_clean(part) for part in merged if _clean(part)]


def split_compound_product_name(name):
    text = _clean(name)
    if not text:
        return []

    segments = _CONNECTOR_RE.split(text)
    if len(segments) == 1 and "," not in text:
        return [text]

    candidates = []
    for segment in segments:
        candidates.extend(_split_commas(segment))

    cleaned = []
    seen = set()
    for candidate in candidates:
        label = _strip_package_tail(candidate)
        if not _looks_like_split_label(label):
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(label)

    return cleaned if len(cleaned) >= 2 else [text]


def expand_compound_deal(deal, store_name):
    names = split_compound_product_name(deal.get("product_name"))
    if len(names) <= 1:
        return [deal]

    group_seed = "|".join([
        store_name or "",
        str(deal.get("valid_from") or ""),
        str(deal.get("valid_to") or ""),
        str(deal.get("product_name") or ""),
        str(deal.get("price_flyer") or deal.get("sale_price") or ""),
        str(deal.get("deal_type") or ""),
        str(deal.get("deal_conditions") or ""),
    ])
    group_id = deal.get("promotion_group_id") or f"promo_{uuid.uuid5(uuid.NAMESPACE_URL, group_seed).hex[:12]}"

    expanded = []
    for name in names:
        child = copy.deepcopy(deal)
        child["product_name"] = name
        child["promotion_group_id"] = group_id
        expanded.append(child)
    return expanded
