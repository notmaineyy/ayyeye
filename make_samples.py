"""Generate obviously fake clearance documents for safe demos.

Creates samples/fake_clearance.png and samples/fake_clearance.pdf using the
"Republic of Pineapple" data from app.py. Run:

    .venv/bin/python make_samples.py
"""

import io
from pathlib import Path

import pymupdf
from PIL import Image

import app

OUT_DIR = Path(__file__).with_name("samples")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    png_bytes = app.generate_sample_document()

    png_path = OUT_DIR / "fake_clearance.png"
    png_path.write_bytes(png_bytes)

    # Embed a JPEG (much smaller) so the committed PDF stays light.
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    jpeg_buffer = io.BytesIO()
    image.save(jpeg_buffer, format="JPEG", quality=80)

    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=jpeg_buffer.getvalue())
    pdf_path = OUT_DIR / "fake_clearance.pdf"
    document.save(pdf_path, deflate=True, garbage=4)
    document.close()

    print(f"wrote {png_path} ({png_path.stat().st_size // 1024} KB)")
    print(f"wrote {pdf_path} ({pdf_path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
