"""Images (PNG, JPEG, TIFF including multi-page scans): read with OCR."""

from pathlib import Path

from PIL import Image, ImageSequence, UnidentifiedImageError

from . import ocr, previews
from .common import ConversionError, Options, Result
from .markdown import page_marker, tidy

# A 600 dpi A3 scan is about 70 million pixels; refuse anything far bigger
# rather than run out of memory.
Image.MAX_IMAGE_PIXELS = 200_000_000
MAX_PAGES = 2000


def convert(path: Path, opts: Options) -> Result:
    try:
        img = Image.open(path)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise ConversionError("This image can't be opened. It may be damaged, or not really an image. Open it on your computer to check it.") from exc
    parts, count, warnings = [], 0, []
    with img:
        for count, frame in enumerate(ImageSequence.Iterator(img), start=1):
            if count > MAX_PAGES:
                warnings.append(f"Only the first {MAX_PAGES} pages were read.")
                count -= 1
                break
            frame = frame.copy()
            previews.save(frame, opts, count)
            text = ocr.read_image(frame, opts)
            parts += [page_marker(count), "", text or "*(No text found on this page.)*", ""]
    if not any(p for p in parts if p and not p.startswith(("<!--", "*("))):
        warnings.append("OCR found no text in this image.")
    return Result(body=tidy("\n".join(parts)), page_count=count, ocr=True, warnings=warnings, previews=count if opts.previews else 0)
