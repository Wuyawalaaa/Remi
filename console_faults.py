"""Fault detection over already-parsed deals. Read-only.

This does not fix anything. It looks at what the parser produced next to the raw
text it produced it from, and says where the two disagree. Every code carries the
rule that produced it, because a code with no visible rule reads as a fact when
it is often a guess.

Nothing here needs a price benchmark. Every check is either mechanical (a field
is empty, a number was ignored) or internal (the flyer states a unit price and
the computed one differs). A genuine clearance passes all of them — the text and
the computed value agree, the number is just small.
"""
import re

# Weight/volume units. If the flyer priced by one of these and no package size
# came along, the normaliser had nothing to divide by and assumed one unit.
MEASURED_UNITS = {"oz", "fl oz", "ml", "g", "kg", "l", "lb", "gal"}

# Units where "one unit" is a real answer, so a missing package size is fine.
COUNTED_UNITS = {"ea", "ct", "pk", "each"}

CONDITIONAL_PATTERNS = [
    (r"when you buy\s*(\d+)", "买 N 件才是这个价"),
    (r"buy\s*\d+\s*get", "买 N 送 M"),
    (r"member price", "会员价"),
    (r"digital", "需要剪电子券"),
    (r"coupon", "需要券"),
    (r"\bfree\b", "含赠品"),
    (r"\d+%\s*off", "百分比折扣"),
]

# "16.9 FL OZ X 6 EA/BOX", "5.29 OZ X 4 EA/PKG", "12 PK", "24 CT"
COUNT_PATTERNS = [
    r"x\s*(\d+)\s*(?:ea|pc|pcs|ct)\b",
    r"\b(\d+)\s*(?:pk|pack)\b",
    r"\b(\d+)\s*ct\b",
]

# A unit price written out in the flyer itself: "$4.99/lb", "$1.29 per oz"
STATED_UNIT_PRICE = re.compile(
    r"\$\s*(\d+(?:\.\d+)?)\s*(?:/|\s*per\s*)\s*(lb|oz|ea|ct|gal|l|kg|g)\b", re.I
)

LB_PER_OZ = 16.0


class Fault:
    __slots__ = ("code", "label", "rule", "confidence", "detail")

    def __init__(self, code, label, rule, confidence, detail=""):
        self.code = code
        self.label = label
        self.rule = rule              # shown in the UI next to the code
        self.confidence = confidence  # "机械可判" | "启发式"
        self.detail = detail

    def as_dict(self):
        return {"code": self.code, "label": self.label, "rule": self.rule,
                "confidence": self.confidence, "detail": self.detail}


def _text(deal):
    """Everything the parser had to read, joined."""
    return " ".join(filter(None, [
        deal.get("product_name") or "",
        deal.get("deal_conditions") or "",
        deal.get("per_unit_volume") or "",
    ]))


def _looks_measured(unit):
    return (unit or "").strip().lower() in MEASURED_UNITS


def detect(deal):
    """Return a list of Fault for one deal row (a dict-like)."""
    faults = []

    price = deal.get("price_flyer")
    unit_flyer = (deal.get("unit_flyer") or "").strip()
    pkg_size = deal.get("per_unit_volume")
    norm_price = deal.get("normalized_price")
    unit_norm = (deal.get("unit_norm") or "").strip()
    conditions = deal.get("deal_conditions") or ""
    deal_type = deal.get("deal_type") or ""
    name = deal.get("product_name") or ""

    # ── 1. no price at all ───────────────────────────────────────────────────
    if price is None:
        faults.append(Fault(
            "NO_PRICE", "无价格",
            "price_flyer 为空", "机械可判"))

    # ── 2. no unit ───────────────────────────────────────────────────────────
    if not unit_flyer:
        faults.append(Fault(
            "NO_UNIT", "无单位",
            "unit_flyer 为空", "机械可判"))

    # ── 3. priced by weight/volume but no package size ───────────────────────
    # The single biggest source of absurd unit prices: potato chips at $2.97
    # marked "oz" with no size, so the normaliser divided by one ounce and
    # published $47.52 per pound.
    if _looks_measured(unit_flyer) and not pkg_size and price is not None:
        faults.append(Fault(
            "SIZE_MISSING", "规格缺失",
            f"按 {unit_flyer} 计价，但 per_unit_volume 为空 —— 单价按「1 {unit_flyer}」算的",
            "机械可判",
            f"算出 {norm_price} / {unit_norm}" if norm_price else ""))

    # ── 4. a count in the text that the parser did not use ───────────────────
    if (deal.get("package_amount") or 1) == 1:
        for pat in COUNT_PATTERNS:
            m = re.search(pat, conditions, re.I)
            if m and int(m.group(1)) > 1:
                faults.append(Fault(
                    "COUNT_IGNORED", "数量被忽略",
                    f"原文含「{m.group(0).strip()}」，但 package_amount = 1",
                    "机械可判",
                    f"数量应为 {m.group(1)}"))
                break

    # ── 5. the flyer states a unit price and ours disagrees ──────────────────
    # Benchmark-free: both numbers come from this one row.
    m = STATED_UNIT_PRICE.search(_text(deal))
    if m and norm_price:
        stated, stated_unit = float(m.group(1)), m.group(2).lower()
        ours = norm_price
        # only compare when the units line up, converting oz→lb where needed
        comparable = None
        if stated_unit == unit_norm:
            comparable = ours
        elif stated_unit == "lb" and unit_norm == "oz":
            comparable = ours * LB_PER_OZ
        elif stated_unit == "oz" and unit_norm == "lb":
            comparable = ours / LB_PER_OZ
        if comparable and stated > 0:
            ratio = comparable / stated
            if ratio < 0.9 or ratio > 1.1:
                faults.append(Fault(
                    "CONTRADICTION", "自相矛盾",
                    f"原文写着 ${stated}/{stated_unit}，我们算出 ${round(comparable, 2)}/{stated_unit}",
                    "机械可判",
                    f"差 {round(ratio, 2)} 倍"))

    # ── 6. the price is conditional, so it is not a per-item price ───────────
    for pat, why in CONDITIONAL_PATTERNS:
        if re.search(pat, deal_type, re.I):
            faults.append(Fault(
                "CONDITIONAL_PRICE", "价格带条件",
                f"deal_type 含「{why}」——这个价格不是单件价", "机械可判",
                deal_type[:60]))
            break

    # ── 7. more than one product sharing one price ───────────────────────────
    # Deliberately weak and labelled as such. " or " between product names is a
    # real signal; "&" is mostly brand names (Head & Shoulders, Bee & Flower).
    if re.search(r"\s+or\s+", name, re.I):
        size_lines = [ln for ln in conditions.splitlines() if re.search(r"\d", ln)]
        strong = len(size_lines) >= 2
        faults.append(Fault(
            "MULTI_PRODUCT", "疑似多商品同格",
            "商品名含「 or 」" + ("，且原文有 2 行以上带数字的规格" if strong else ""),
            "启发式（含假阳性）",
            f"原文规格 {len(size_lines)} 行"))

    return faults


def summarise(rows):
    """Count each code across rows. Returns [(code, label, rule, confidence, n)]."""
    seen, counts = {}, {}
    for r in rows:
        for f in r["faults"]:
            counts[f["code"]] = counts.get(f["code"], 0) + 1
            seen.setdefault(f["code"], f)
    out = [(c, seen[c]["label"], seen[c]["rule"], seen[c]["confidence"], n)
           for c, n in counts.items()]
    out.sort(key=lambda t: -t[4])
    return out
