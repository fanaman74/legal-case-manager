"""Converting case files to Markdown for reading, searching and AI.

The converters only read the original and write new files; originals are
never changed. They run in a child process (see __main__.py) started by the
worker for each file."""

from pathlib import Path

from . import eml, image, msg, pdf, text, word
from .common import CONVERTER_VERSION, ConversionError, Options, Result

CONVERTERS = {
    ".docx": word.convert,
    ".pdf": pdf.convert,
    ".msg": msg.convert,
    ".eml": eml.convert,
    ".txt": text.convert,
    ".png": image.convert,
    ".jpg": image.convert,
    ".jpeg": image.convert,
    ".tif": image.convert,
    ".tiff": image.convert,
}


def convert(path: Path, ext: str, opts: Options) -> Result:
    fn = CONVERTERS.get(ext)
    if fn is None:
        raise ConversionError(f"{ext or 'Files without an extension'} files can't be converted.")
    return fn(path, opts)


__all__ = ["CONVERTER_VERSION", "ConversionError", "Options", "Result", "convert"]
