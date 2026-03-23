import os
import time
import logging
import base64
from pathlib import Path
from dotenv import load_dotenv
import fitz  # PyMuPDF

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

FLYERS_DIR  = os.path.join(os.path.dirname(__file__), "flyers", "safeway")
TEMP_DIR    = os.path.join(os.path.dirname(__file__), "temp", "safeway_pages")
MAX_RETRIES = 3

OCR_PROMPT = (
    "You are processing a supermarket flyer image. Extract all deals and return a JSON array.\n\n"
    "For each item extract:\n"
    "- brand: string or null (manufacturer or store brand label e.g. \"Driscoll's\", \"Signature Select\", \"O Organics\" — null only if no brand is shown)\n"
    "- product: string (generic item name only, no brand, no descriptors e.g. \"Eggs\", \"Strawberries\", \"Chicken Thighs\")\n"
    "- quality_tags: string[] (all variant/grade descriptors e.g. [\"Organic\", \"Free Range\", \"Large\", \"Brown\", \"USDA Choice\", \"Wild-caught\"] — empty array if none)\n"
    "- category: one of: produce, meat, seafood, dairy, bakery, deli, frozen, pantry, beverages, snacks, prepared foods, household, personal care, health, baby, alcohol, floral, condiments\n"
    "- price_flyer: number or null (the price number exactly as printed on the flyer — do not compute, do not divide)\n"
    "- original_price: number or null (pre-deal price if shown)\n"
    "- unit_flyer: string (the pricing unit as shown on flyer: lb, oz, ea, gal, fl_oz, ml, g, kg, doz, ct)\n"
    "- package_amount: integer (total number of individual units included in price_flyer. For plain items = 1. For \"2 for $5\" = 2. For a 6-pack = 6.)\n"
    "- per_unit_volume: string or null (volume or weight of each individual unit as a string with unit e.g. \"750ml\", \"16oz\", \"1lb\", \"355ml\" — null if no size info or if unit_flyer is already a weight/volume)\n"
    "- deal_type: string or null (exact deal format as stated on flyer e.g. \"2 for $5\", \"Buy 1 Get 1 Free\", \"Mix & Match BOGO\")\n"
    "- deal_conditions: string or null (purchase conditions e.g. \"must buy 2\", \"equal or lesser value\", \"membership required\", \"Mix & Match eligible\" — combine multiple with semicolons)\n"
    "- promotion_group_id: string or null (same id for all items sharing one promotion block)\n\n"
    "Rules:\n\n"
    "PRICE READING:\n"
    "- Superscript or visually smaller digits after a larger number = decimal cents. \"799\" where \"99\" is smaller = $7.99\n"
    "- All digits same visual size = face value. \"799\" all same size = $799\n"
    "- price_flyer is always the raw printed number, never divided or computed\n\n"
    "PACKAGE AMOUNT:\n"
    "- \"2 for $5\" → package_amount=2, price_flyer=5.00, unit_flyer=ea\n"
    "- \"6-pack 355ml cans\" → package_amount=6, per_unit_volume=\"355ml\", unit_flyer=ea\n"
    "- \"$2.99 ea, must buy 2\" → package_amount=1, price_flyer=2.99 (price is per unit, buy condition is not deal quantity)\n"
    "- single item, no pack → package_amount=1\n\n"
    "PER UNIT VOLUME:\n"
    "- Only fill if unit_flyer=ea and the individual unit has a known size\n"
    "- \"6-pack 750ml bottles\" → per_unit_volume=\"750ml\"\n"
    "- \"16oz tub\" → per_unit_volume=\"16oz\"\n"
    "- If unit_flyer is already lb/oz/g/ml etc., set per_unit_volume=null\n\n"
    "\"Buy X Get Y Free / BOGO\" TYPE:\n"
    "- price_flyer = original_price if shown, else null\n"
    "- package_amount = 1\n"
    "- deal_type = exact wording on flyer\n"
    "- deal_conditions = \"must buy X\" plus any other stated conditions\n\n"
    "SPLITTING ITEMS:\n"
    "- If multiple distinct food items share one deal block, create one entry per item\n"
    "- Link them with the same promotion_group_id\n"
    "- Do not split compound product names (e.g. \"strawberry waffle\" stays as one item)\n"
    "- BOGO and Mix & Match are signals that splitting may be needed — apply judgment\n\n"
    "GENERAL:\n"
    "- Do not invent prices not visible in the image\n"
    "- If a field is not determinable, use null\n"
    "- Return only the JSON array. No explanation, no markdown fences.\n\n"
    'Format: [{"brand":"Driscoll\'s","product":"Strawberries","quality_tags":[],"category":"produce",'
    '"price_flyer":5.00,"original_price":null,"unit_flyer":"ea","package_amount":2,"per_unit_volume":"16oz",'
    '"deal_type":"2 for $5","deal_conditions":"must buy 2","promotion_group_id":null}]'
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


# ── Provider implementations ──────────────────────────────────────────────────

def ocr_with_gemini(image_bytes: bytes, page_num: int) -> str:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            OCR_PROMPT,
        ],
        config=types.GenerateContentConfig(max_output_tokens=8000),
    )
    return response.text


def ocr_with_deepseek(image_bytes: bytes, page_num: int) -> str:
    from openai import OpenAI

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set in .env")

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = client.chat.completions.create(
        model="deepseek-vl2",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }],
        max_tokens=8000,
    )
    return response.choices[0].message.content


def ocr_with_openai(image_bytes: bytes, page_num: int) -> str:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in .env")

    client = OpenAI(api_key=api_key)
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }],
        max_tokens=8000,
    )
    return response.choices[0].message.content


def ocr_with_anthropic(image_bytes: bytes, page_num: int) -> str:
    import anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=api_key)
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }],
    )
    return response.content[0].text


def ocr_with_openrouter(image_bytes: bytes, page_num: int) -> str:
    from openai import OpenAI
    import io
    from PIL import Image

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")

    # Compress to JPEG to stay within size limits
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    response = client.chat.completions.create(
        model="google/gemini-2.0-flash-001",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": OCR_PROMPT},
            ],
        }],
        max_tokens=8000,
    )
    return response.choices[0].message.content


def ocr_with_novita(image_bytes: bytes, page_num: int) -> str:
    from openai import OpenAI
    import io
    from PIL import Image

    api_key = os.getenv("NOVITA_API_KEY")
    if not api_key:
        raise RuntimeError("NOVITA_API_KEY not set in .env")

    # Compress to JPEG to stay within size limits
    img = Image.open(io.BytesIO(image_bytes))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    client = OpenAI(api_key=api_key, base_url="https://api.novita.ai/openai")
    response = client.chat.completions.create(
        model="deepseek/deepseek-ocr-2",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": "<|grounding|>Convert the document to markdown."},
            ],
        }],
        max_tokens=8000,
    )
    return response.choices[0].message.content


PROVIDERS = {
    "gemini":      ocr_with_gemini,
    "deepseek":    ocr_with_deepseek,
    "openai":      ocr_with_openai,
    "anthropic":   ocr_with_anthropic,
    "openrouter":  ocr_with_openrouter,
    "novita":      ocr_with_novita,
}


# ── Core logic ────────────────────────────────────────────────────────────────

def get_provider():
    name = os.getenv("OCR_PROVIDER", "gemini").lower()
    if name not in PROVIDERS:
        raise RuntimeError(f"Unknown OCR_PROVIDER '{name}'. Choose from: {list(PROVIDERS)}")
    log.info(f"Using OCR provider: {name}")
    return name, PROVIDERS[name]


def find_latest_pdf():
    pdfs = sorted(Path(FLYERS_DIR).glob("safeway_*.pdf"), reverse=True)
    if not pdfs:
        raise FileNotFoundError(f"No Safeway PDF found in {FLYERS_DIR}")
    return pdfs[0]


def pdf_to_images(pdf_path):
    os.makedirs(TEMP_DIR, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    image_paths = []
    log.info(f"Converting {len(doc)} pages to images...")

    for i in range(len(doc)):
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
        img_path = os.path.join(TEMP_DIR, f"page_{i + 1:02d}.png")
        pix.save(img_path)
        image_paths.append(img_path)

    doc.close()
    return image_paths


def ocr_page(ocr_fn, image_path, page_num):
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"  OCR page {page_num} (attempt {attempt})...")
            return ocr_fn(image_bytes, page_num)
        except Exception as e:
            log.warning(f"  Page {page_num} attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
            else:
                log.error(f"  Page {page_num} failed after {MAX_RETRIES} attempts. Skipping.")
                return f"[OCR FAILED FOR PAGE {page_num}]"


def run():
    provider_name, ocr_fn = get_provider()
    pdf_path = find_latest_pdf()
    log.info(f"Using PDF: {pdf_path}")

    image_paths = pdf_to_images(pdf_path)
    page_texts = []

    for i, image_path in enumerate(image_paths, start=1):
        log.info(f"Processing page {i}/{len(image_paths)}...")
        text = ocr_page(ocr_fn, image_path, i)
        page_texts.append((i, text))

    date_str = pdf_path.stem.replace("safeway_", "")
    output_path = os.path.join(FLYERS_DIR, f"safeway_{date_str}_raw.txt")

    with open(output_path, "w", encoding="utf-8") as f:
        for page_num, text in page_texts:
            f.write(f"\n--- PAGE {page_num} ---\n\n")
            f.write(text)
            f.write("\n")

    log.info(f"OCR complete. Output: {output_path}")
    print(f"\n=== OCR complete ===")
    print(f"Provider:        {provider_name}")
    print(f"Pages processed: {len(page_texts)}")
    print(f"Output:          {output_path}")


if __name__ == "__main__":
    run()
