import os
import time
import json
import logging
import base64
from pathlib import Path

import fitz  # PyMuPDF
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "safeway")
TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp", "safeway_pages")
MAX_RETRIES = 3

OCR_PROMPT = (
    "Extract supermarket flyer deals and return raw JSON only.\n"
    "Return exactly one complete JSON array.\n"
    "No markdown fences. No prose. No comments. Close the array before stopping.\n"
    "Keep strings compact. Prefer null over explanation. Do not repeat marketing copy unless it changes the deal.\n\n"
    "Fields per item:\n"
    "- brand: string or null\n"
    "- product: short generic item name only, no brand\n"
    "- quality_tags: short string[] only for useful variant descriptors\n"
    "- category: one of produce, meat, seafood, dairy, bakery, deli, frozen, pantry, beverages, snacks, prepared foods, household, personal care, health, baby, alcohol, floral, condiments\n"
    "- price_flyer: number or null, raw printed price only\n"
    "- original_price: number or null\n"
    "- unit_flyer: one of lb, oz, ea, gal, fl_oz, ml, g, kg, doz, ct, null\n"
    "- package_amount: integer\n"
    "- per_unit_volume: short size string or null\n"
    "- deal_type: short exact deal text or null\n"
    "- deal_conditions: short semicolon-separated fragments or null\n"
    "- promotion_group_id: short string or null\n\n"
    "Rules:\n"
    "- '799' with smaller trailing digits means 7.99; same-size digits mean face value.\n"
    "- price_flyer is the printed price, never computed or divided.\n"
    "- '2 for $5' => price_flyer=5.00, package_amount=2, unit_flyer='ea'.\n"
    "- '$2.99 ea, must buy 2' => price_flyer=2.99, package_amount=1, deal_conditions='must buy 2'.\n"
    "- If unit_flyer is already lb/oz/g/ml etc., set per_unit_volume=null.\n"
    "- For BOGO, use deal_type for the promo text and keep package_amount=1.\n"
    "- If multiple distinct food items share one promo block, emit one item per product with the same promotion_group_id.\n"
    "- If a field is not determinable, use null.\n"
    '- Example item: {"brand":"Driscoll\'s","product":"Strawberries","quality_tags":[],"category":"produce","price_flyer":5.0,"original_price":null,"unit_flyer":"ea","package_amount":2,"per_unit_volume":"16oz","deal_type":"2 for $5","deal_conditions":"must buy 2","promotion_group_id":null}'
)

OCR_RESCUE_PROMPT = (
    "Return one complete JSON array only.\n"
    "No markdown fences. No prose. No comments.\n"
    "Output fewer items if needed, but every returned object must be complete valid JSON.\n"
    "Keep strings very short. Use null instead of explanation. Close all objects and close the array.\n"
    "Fields: brand, product, quality_tags, category, price_flyer, original_price, unit_flyer, "
    "package_amount, per_unit_volume, deal_type, deal_conditions, promotion_group_id."
)

DEAL_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "brand",
            "product",
            "quality_tags",
            "category",
            "price_flyer",
            "original_price",
            "unit_flyer",
            "package_amount",
            "per_unit_volume",
            "deal_type",
            "deal_conditions",
            "promotion_group_id",
        ],
        "properties": {
            "brand": {"type": ["string", "null"]},
            "product": {"type": "string"},
            "quality_tags": {"type": "array", "items": {"type": "string"}},
            "category": {
                "type": ["string", "null"],
                "enum": [
                    "produce",
                    "meat",
                    "seafood",
                    "dairy",
                    "bakery",
                    "deli",
                    "frozen",
                    "pantry",
                    "beverages",
                    "snacks",
                    "prepared foods",
                    "household",
                    "personal care",
                    "health",
                    "baby",
                    "alcohol",
                    "floral",
                    "condiments",
                    None,
                ],
            },
            "price_flyer": {"type": ["number", "null"]},
            "original_price": {"type": ["number", "null"]},
            "unit_flyer": {
                "type": ["string", "null"],
                "enum": ["lb", "oz", "ea", "gal", "fl_oz", "ml", "g", "kg", "doz", "ct", None],
            },
            "package_amount": {"type": "integer"},
            "per_unit_volume": {"type": ["string", "null"]},
            "deal_type": {"type": ["string", "null"]},
            "deal_conditions": {"type": ["string", "null"]},
            "promotion_group_id": {"type": ["string", "null"]},
        },
    },
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def _coerce_json_array_text(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None

    candidate = text[start:end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return json.dumps(parsed, ensure_ascii=False)


def _normalize_prompt(prompt: str | None) -> str:
    return prompt or OCR_PROMPT


def ocr_with_gemini(image_bytes: bytes, page_num: int, prompt: str = OCR_PROMPT) -> str:
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
            _normalize_prompt(prompt),
        ],
        config=types.GenerateContentConfig(maxOutputTokens=8000),
    )
    return response.text


def ocr_with_vertex(image_bytes: bytes, page_num: int, prompt: str = OCR_PROMPT, rescue: bool = False) -> str:
    from google import genai
    from google.genai import types
    from google.oauth2 import service_account

    service_account_file = os.getenv("VERTEX_SERVICE_ACCOUNT_FILE")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

    if not service_account_file:
        raise RuntimeError("VERTEX_SERVICE_ACCOUNT_FILE not set in .env")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT not set in .env")

    cred_path = service_account_file
    if not os.path.isabs(cred_path):
        cred_path = os.path.join(os.path.dirname(__file__), cred_path)

    credentials = service_account.Credentials.from_service_account_file(
        cred_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )

    client = genai.Client(
        vertexai=True,
        project=project,
        location=location,
        credentials=credentials,
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            _normalize_prompt(prompt),
        ],
        config=types.GenerateContentConfig(
            temperature=0,
            candidateCount=1,
            maxOutputTokens=4096 if rescue else 8192,
            responseMimeType="application/json",
            responseJsonSchema=DEAL_RESPONSE_SCHEMA,
            thinkingConfig=types.ThinkingConfig(thinkingBudget=0),
        ),
    )
    return response.text


def ocr_with_deepseek(image_bytes: bytes, page_num: int, prompt: str = OCR_PROMPT) -> str:
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
                {"type": "text", "text": _normalize_prompt(prompt)},
            ],
        }],
        max_tokens=8000,
    )
    return response.choices[0].message.content


def ocr_with_openai(image_bytes: bytes, page_num: int, prompt: str = OCR_PROMPT) -> str:
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
                {"type": "text", "text": _normalize_prompt(prompt)},
            ],
        }],
        max_tokens=8000,
    )
    return response.choices[0].message.content


def ocr_with_anthropic(image_bytes: bytes, page_num: int, prompt: str = OCR_PROMPT) -> str:
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
                {"type": "text", "text": _normalize_prompt(prompt)},
            ],
        }],
    )
    return response.content[0].text


def ocr_with_openrouter(image_bytes: bytes, page_num: int, prompt: str = OCR_PROMPT) -> str:
    import io

    from openai import OpenAI
    from PIL import Image

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")

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
                {"type": "text", "text": _normalize_prompt(prompt)},
            ],
        }],
        max_tokens=8000,
    )
    return response.choices[0].message.content


PROVIDERS = {
    "gemini": ocr_with_gemini,
    "vertex": ocr_with_vertex,
    "deepseek": ocr_with_deepseek,
    "openai": ocr_with_openai,
    "anthropic": ocr_with_anthropic,
    "openrouter": ocr_with_openrouter,
}


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
            text = ocr_fn(image_bytes, page_num, OCR_PROMPT)
            normalized = _coerce_json_array_text(text)
            if normalized is not None:
                return normalized

            log.warning(f"  Page {page_num} attempt {attempt} returned malformed or truncated JSON.")

            if ocr_fn is ocr_with_vertex:
                log.info(f"  OCR page {page_num} rescue attempt...")
                rescue_text = ocr_fn(image_bytes, page_num, OCR_RESCUE_PROMPT, rescue=True)
                normalized = _coerce_json_array_text(rescue_text)
                if normalized is not None:
                    return normalized
                raise ValueError("malformed rescue output")

            raise ValueError("malformed output")
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
    print("\n=== OCR complete ===")
    print(f"Provider:        {provider_name}")
    print(f"Pages processed: {len(page_texts)}")
    print(f"Output:          {output_path}")


if __name__ == "__main__":
    run()
