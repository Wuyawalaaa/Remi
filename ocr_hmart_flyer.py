import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Re-use OCR providers and prompt defined in ocr_safeway_flyer
sys.path.insert(0, os.path.dirname(__file__))
from ocr_safeway_flyer import get_provider, ocr_page

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "hmart")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def find_latest_jpg():
    jpgs = sorted(Path(FLYERS_DIR).glob("hmart_*.jpg"), reverse=True)
    if not jpgs:
        raise FileNotFoundError(f"No H Mart JPG found in {FLYERS_DIR}")
    return jpgs[0]


def run():
    provider_name, ocr_fn = get_provider()
    jpg_path = find_latest_jpg()
    log.info(f"Using image: {jpg_path}")

    log.info("Processing page 1/1...")
    text = ocr_page(ocr_fn, str(jpg_path), 1)

    # Derive date string from filename: hmart_YYYY-MM-DD.jpg
    date_str = jpg_path.stem.replace("hmart_", "")
    output_path = os.path.join(FLYERS_DIR, f"hmart_{date_str}_raw.txt")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n--- PAGE 1 ---\n\n")
        f.write(text)
        f.write("\n")

    log.info(f"OCR complete. Output: {output_path}")
    print(f"\n=== OCR complete ===")
    print(f"Provider:        {provider_name}")
    print(f"Pages processed: 1")
    print(f"Output:          {output_path}")


if __name__ == "__main__":
    run()
