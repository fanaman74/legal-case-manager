"""Just enough RTF to read Outlook message bodies.

Outlook often stores a message body only as compressed RTF. That RTF is
either plain RTF or HTML wrapped in RTF ("\\fromhtml1"). This turns the first
into text and unwraps the second back into HTML, following Microsoft's
de-encapsulation rules ([MS-OXRTFEX]) in simplified form."""

import re

_SKIP_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata", "colorschememapping",
    "latentstyles", "datastore", "xmlnstbl", "listtable", "listoverridetable", "rsidtbl", "generator",
    "mmathPr", "pgdsctbl", "header", "footer", "headerl", "headerr", "footerl", "footerr", "fldinst",
    "filetbl", "revtbl", "pnseclvl", "listtext", "pntext", "bkmkstart", "bkmkend", "nonshppict", "shp",
}
_TOKEN = re.compile(r"\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+", re.S)


def decode(rtf: str) -> tuple[str, str]:
    """('html', html) for HTML wrapped in RTF, else ('text', text)."""
    from_html = "\\fromhtml" in rtf[:1000]
    m = re.search(r"\\ansicpg(\d+)", rtf[:2000])
    codepage = f"cp{m.group(1)}" if m else "cp1252"
    try:
        "".encode(codepage)
    except LookupError:
        codepage = "cp1252"

    out: list[str] = []
    pending = bytearray()
    # Per group: skipping this destination, inside \htmlrtf, unicode skip count.
    stack = [[False, False, 1]]
    star = False
    skip_chars = 0

    def flush():
        if pending:
            out.append(pending.decode(codepage, "replace"))
            pending.clear()

    for m in _TOKEN.finditer(rtf):
        word, param, hexbyte, sym, brace, text = m.groups()
        state = stack[-1]
        hidden = state[0] or (from_html and state[1])
        if brace == "{":
            stack.append(list(state))
            star = False
            continue
        if brace == "}":
            flush()
            if len(stack) > 1:
                stack.pop()
            star = False
            continue
        if skip_chars and (text or hexbyte):
            # Characters standing in for a \uN character are dropped.
            if text:
                n = min(skip_chars, len(text))
                skip_chars -= n
                text = text[n:]
                if not text:
                    continue
            else:
                skip_chars -= 1
                continue
        if word:
            if star and word != "htmltag":
                state[0] = True
            star = False
            if word in _SKIP_DESTINATIONS:
                state[0] = True
            elif word == "htmltag":
                state[0] = state[1] = False
            elif word == "htmlrtf":
                state[1] = param != "0"
            elif hidden:
                continue
            elif word in ("par", "line"):
                flush()
                out.append("\n")
            elif word == "tab":
                flush()
                out.append("\t")
            elif word == "u" and param:
                flush()
                n = int(param)
                out.append(chr(n + 65536 if n < 0 else n))
                skip_chars = state[2]
            elif word == "uc" and param:
                state[2] = int(param)
            elif word in ("emdash", "endash"):
                flush()
                out.append("—" if word == "emdash" else "–")
            elif word in ("lquote", "rquote"):
                flush()
                out.append("‘" if word == "lquote" else "’")
            elif word in ("ldblquote", "rdblquote"):
                flush()
                out.append("“" if word == "ldblquote" else "”")
            elif word == "bullet":
                flush()
                out.append("•")
            continue
        if sym is not None:
            if sym == "*":
                star = True
            elif hidden:
                continue
            elif sym in "\\{}":
                flush()
                out.append(sym)
            elif sym == "~":
                flush()
                out.append("\u00a0")
            elif sym in "\r\n":
                flush()
                out.append("\n")
            continue
        if hidden:
            continue
        if hexbyte:
            pending.append(int(hexbyte, 16))
        elif text:
            flush()
            out.append(text)
    flush()
    result = "".join(out)
    if from_html:
        return "html", result
    return "text", re.sub(r"\n{3,}", "\n\n", result).strip()
