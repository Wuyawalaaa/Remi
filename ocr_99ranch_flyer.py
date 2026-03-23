import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Re-use OCR providers and prompt defined in ocr_safeway_flyer
sys.path.insert(0, os.path.dirname(__file__))
from ocr_safeway_flyer import get_provider, ocr_page

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "99ranch")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)


def find_latest_images():
    """Return sorted list of JPEGs for the most recent weekly ad date."""
    all_jpgs = sorted(Path(FLYERS_DIR).glob("99ranch_*_p*.jpg"), reverse=True)
    if not all_jpgs:
        raise FileNotFoundError(f"No 99 Ranch JPEGs found in {FLYERS_DIR}")

    # Derive date from filename: 99ranch_YYYY-MM-DD_p01.jpg
    latest_date = all_jpgs[0].stem.split("_")[1]  # YYYY-MM-DD

    # Collect all pages for that date, sorted by page number
    pages = sorted(
        [p for p in all_jpgs if f"99ranch_{latest_date}_p" in p.name]
    )
    log.info(f"Found {len(pages)} pages for {latest_date}")
    return pages, latest_date


def run():
    provider_name, ocr_fn = get_provider()
    image_paths, date_str = find_latest_images()
    output_path = os.path.join(FLYERS_DIR, f"99ranch_{date_str}_raw.txt")

    page_texts = []
    for i, img_path in enumerate(image_paths, start=1):
        log.info(f"Processing page {i}/{len(image_paths)}: {img_path.name}")
        text = ocr_page(ocr_fn, str(img_path), i)
        page_texts.append((i, text))

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
