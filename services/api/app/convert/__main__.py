"""Child process that converts one file:

    python -m app.convert <original> <ext> <out-folder> [--ocr] [--lang eng]

Writes body.md, result.json, previews/pNNNN.jpg and attachments/N into the
out folder. A file that can't be converted gives result.json with "error"
(the plain-language reason) and exit code 0; any other exit means the
converter itself failed (a crash, out of memory), which the worker reports
and may retry. Running each file in its own process means a parser crash or
hang can't take the worker down, and memory is returned after each file."""

import json
import os
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

from . import ConversionError, Options, convert


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: python -m app.convert <original> <ext> <out-folder> [--ocr] [--lang eng] [--previews]", file=sys.stderr)
        return 2
    src, ext, out = Path(argv[0]), argv[1], Path(argv[2])
    flags = argv[3:]
    lang = flags[flags.index("--lang") + 1] if "--lang" in flags and flags.index("--lang") + 1 < len(flags) else "eng"
    opts = Options(
        ocr="--ocr" in flags,
        ocr_lang=lang,
        tesseract=os.environ.get("TESSERACT_CMD") or "tesseract",
        previews=out / "previews" if "--previews" in flags else None,
    )
    out.mkdir(parents=True, exist_ok=True)
    try:
        result = convert(src, ext, opts)
    except ConversionError as exc:
        (out / "result.json").write_text(json.dumps({"error": str(exc)}), encoding="utf-8")
        return 0
    except MemoryError:
        (out / "result.json").write_text(json.dumps({"error": "This file is too large to convert on this computer."}), encoding="utf-8")
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    (out / "body.md").write_text(result.body, encoding="utf-8")
    att_dir = out / "attachments"
    names = []
    for i, a in enumerate(result.attachments):
        att_dir.mkdir(exist_ok=True)
        (att_dir / str(i)).write_bytes(a.data)
        names.append(a.name)
    info = asdict(result)
    info.pop("body")
    info["attachments"] = names
    (out / "result.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
