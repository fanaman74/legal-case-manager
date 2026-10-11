"""Page images for the document viewer. Made by the worker from the
original, so the browser only ever shows plain JPEGs the app created, never
the uploaded file itself."""

from PIL import Image

from .common import PREVIEW_WIDTH, Options


def save(img: Image.Image, opts: Options, page: int) -> bool:
    if opts.previews is None:
        return False
    opts.previews.mkdir(parents=True, exist_ok=True)
    img = img.convert("RGB")
    if img.width > PREVIEW_WIDTH:
        img = img.resize((PREVIEW_WIDTH, max(1, round(img.height * PREVIEW_WIDTH / img.width))), Image.Resampling.LANCZOS)
    img.save(opts.previews / f"p{page:04d}.jpg", "JPEG", quality=72, optimize=True)
    return True
