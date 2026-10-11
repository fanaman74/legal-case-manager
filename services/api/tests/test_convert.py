"""Converters, on generated files and on real Outlook messages."""

import os
import shutil

import pytest

from app.convert import ConversionError, Options, convert, emailmd, html, rtf
from app.convert.markdown import front_matter, split_front_matter, table
from tests import samples

needs_ocr = pytest.mark.skipif(
    not shutil.which("tesseract") and not os.environ.get("REQUIRE_OCR"),
    reason="Tesseract isn't installed (CI installs it and sets REQUIRE_OCR)",
)


def run(tmp_path, name: str, data: bytes, **opts):
    path = tmp_path / name
    path.write_bytes(data)
    return convert(path, path.suffix.lower(), Options(**opts))


# --- Word -----------------------------------------------------------------------


def test_word_structure(tmp_path):
    r = run(tmp_path, "statement.docx", samples.docx_bytes())
    body = r.body
    assert body.startswith("<!-- page: 1 -->")
    assert "# Witness statement of Jane Doe" in body
    assert "## Background" in body
    assert "I am the Claimant. **The agreement** was signed on 1 March 2024." in body
    # Word's automatic numbers are written into the text.
    assert "1. The goods were not delivered.\n2. I chased the Defendant twice." in body
    assert "| 15 April 2024 | Delivery due \\| missed |" in body
    # The page break becomes a page marker before the next heading.
    assert body.index("<!-- page: 2 -->") < body.index("## Statement of truth")
    assert r.page_count == 2
    assert r.title == "Witness statement of Jane Doe"
    assert r.warnings == []


def test_word_damaged(tmp_path):
    with pytest.raises(ConversionError, match="password-protected or damaged"):
        run(tmp_path, "broken.docx", b"not a zip at all")


# --- PDF ------------------------------------------------------------------------


def test_pdf_text_headers_footers_tables(tmp_path):
    r = run(tmp_path, "claim.pdf", samples.pdf_bytes(), previews=tmp_path / "previews")
    body = r.body
    assert r.page_count == 3
    for n in (1, 2, 3):
        assert f"<!-- page: {n} -->" in body
        assert f"# Part {n}: Particulars of Claim" in body
    # Running header and "Page n of 3" footer are gone; body text stays.
    assert "CONFIDENTIAL" not in body
    assert "of 3" not in body
    assert "Paragraph 2. The Claimant entered into a written agreement with the Defendant on 1 March 2024" in body
    assert "| 15 April 2024 | Delivery due | GBP 125,000 |" in body
    assert body.index("<!-- page: 2 -->") < body.index("| Date | Event | Amount |") < body.index("<!-- page: 3 -->")
    assert sorted(p.name for p in (tmp_path / "previews").iterdir()) == ["p0001.jpg", "p0002.jpg", "p0003.jpg"]
    assert r.previews == 3 and not r.scanned_pages


def test_pdf_scan_is_flagged_not_read_until_asked(tmp_path):
    r = run(tmp_path, "scan.pdf", samples.scanned_pdf_bytes())
    assert r.scanned_pages == [1]
    assert not r.ocr
    assert "Run OCR" in r.warnings[0]


@needs_ocr
def test_pdf_scan_ocr(tmp_path):
    r = run(tmp_path, "scan.pdf", samples.scanned_pdf_bytes(), ocr=True)
    assert r.ocr and r.scanned_pages == []
    assert "WITNESS STATEMENT" in r.body
    assert "steel never arrived" in r.body
    assert any("read with OCR" in w for w in r.warnings)


def test_pdf_damaged(tmp_path):
    with pytest.raises(ConversionError, match="can't be opened"):
        run(tmp_path, "bad.pdf", b"%PDF-1.4\nnot really")


@needs_ocr
def test_tiff_pages_ocr(tmp_path):
    r = run(tmp_path, "invoice.tif", samples.tiff_bytes(), previews=tmp_path / "p")
    assert r.page_count == 2 and r.ocr
    assert r.body.index("<!-- page: 1 -->") < r.body.index("Invoice number 4471") < r.body.index("<!-- page: 2 -->") < r.body.index("125,000")
    assert r.previews == 2


def test_not_an_image(tmp_path):
    with pytest.raises(ConversionError, match="can't be opened"):
        run(tmp_path, "photo.jpg", b"definitely not a jpeg")


# --- Email ----------------------------------------------------------------------


def test_eml_headers_body_quotes_attachments(tmp_path):
    r = run(tmp_path, "reply.eml", samples.eml_bytes(attachment=samples.pdf_bytes()))
    assert r.meta["from"] == "Jane Doe <jane@claimant.example>"
    assert r.meta["to"] == ["Bob Smith <bob@defendant.example>"]
    assert r.meta["cc"] == ["legal@claimant.example"]
    assert r.meta["subject"] == "RE: Delivery of steel"
    assert r.meta["message_id"] == "<abc123@claimant.example>"
    assert r.meta["thread"] == "Delivery of steel"
    assert r.meta["date"] == "2024-04-16T09:30:00+01:00"
    body = r.body
    assert body.startswith("# RE: Delivery of steel")
    assert "**From:** Jane Doe \\<jane@claimant.example\\>" in body
    assert "The steel did not arrive yesterday." in body
    # The earlier message is marked as quoted, after the new text.
    quoted = body.index("## Earlier messages in this thread (quoted)")
    assert body.index("The steel did not arrive") < quoted < body.index("> We are on track")
    assert "> From: Bob Smith  \n> Sent: 10 April 2024 16:00  \n" in body  # quoted header lines stay apart
    assert [a.name for a in r.attachments] == ["Delivery note.pdf"]
    assert "photos.zip (.zip files aren't converted" in body


def test_msg_real_outlook_file(tmp_path):
    r = convert(samples.SAMPLES / "EmailWithAttachments.msg", ".msg", Options())
    assert r.meta["subject"] == "This is the subject"
    assert r.meta["from"] == "Kees <peterpan@neverland.com>"
    assert r.meta["to"] == ["crocodile@neverland.com"]
    assert r.meta["cc"] == ["tinkerbel@neverland.com"]
    assert r.meta["date"] == "2016-12-03T18:08:54+00:00"
    assert "Hello Neverland text" in r.body
    assert [(a.name, len(a.data)) for a in r.attachments] == [("peterpan.jpg", 39150)]


def test_msg_message_id_and_unsent(tmp_path):
    r = convert(samples.SAMPLES / "plain_jpeg_attached.msg", ".msg", Options())
    assert r.meta["message_id"] == "<20070924132803.GB10141@matijs.net>"
    assert r.meta["from"] == "Matijs van Zuijlen <Matijs.van.Zuijlen@xs4all.nl>"
    r = convert(samples.SAMPLES / "plain_uc_unsent.msg", ".msg", Options())
    assert r.meta["from"] == ""  # never sent
    assert "This is a test  \nThe body is in plain text" in r.body


def test_msg_not_outlook(tmp_path):
    with pytest.raises(ConversionError, match="isn't an Outlook message"):
        run(tmp_path, "x.msg", b"hello")


def test_reply_separators():
    for sep in ("-----Original Message-----", "On Tue, 5 Mar 2024 at 14:22, Jane Doe <j@x.com> wrote:", "________________________________"):
        new, quoted = emailmd.split_quoted(f"Thanks.\n\n{sep}\nolder text")
        assert new.strip() == "Thanks." and "older text" in quoted
    new, quoted = emailmd.split_quoted("Agreed.\n\nFrom: Bob\nSent: Monday\nTo: Jane\nSubject: x\n\nold")
    assert new.strip() == "Agreed." and quoted.startswith("From: Bob")
    assert emailmd.split_quoted("No quotes here.\nFrom: the start.") == ("No quotes here.\nFrom: the start.", "")


def test_rtf_plain_and_html_bodies():
    unicode_euro = "\\" + "u8364?"  # RTF's \uN escape, written out so nothing decodes it early
    assert rtf.decode(r"{\rtf1\ansi\ansicpg1252 {\fonttbl{\f0 Arial;}}Hello\par Caf\'e9 " + unicode_euro + " done}") == ("text", "Hello\nCafé € done")
    kind, content = rtf.decode(r"{\rtf1\ansi\fromhtml1 {\*\htmltag19 <p>}\htmlrtf {\htmlrtf0 Hi there{\*\htmltag84 </p>}}")
    assert kind == "html" and "<p>" in content and "Hi there" in content and "</p>" in content


# --- Text and helpers ----------------------------------------------------------------


def test_text_keeps_lines_and_escapes(tmp_path):
    r = run(tmp_path, "note.txt", "# not a heading\nline two\n\n* not a list".encode())
    assert r.body == "\\# not a heading  \nline two\n\n\\* not a list\n"
    r = run(tmp_path, "old.txt", "Caf\xe9".encode("cp1252"))
    assert "Café" in r.body and r.warnings


def test_html_to_markdown():
    md = html.to_markdown("<style>x{}</style><h2>Terms</h2><p>See <a href='https://example.com'>here</a>.</p><ul><li>a<ul><li>b</li></ul></li></ul><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert "## Terms" in md and "[here](https://example.com)" in md
    assert "- a\n  - b" in md
    assert "| A | B |\n|---|---|\n| 1 | 2 |" in md
    assert "x{}" not in md
    assert html.to_markdown("<a href='javascript:alert(1)'>click</a>").strip() == "click"


def test_front_matter_round_trip():
    fm = front_matter({"doc_id": "abc", "source_filename": 'He said: "no" #1', "page_count": 3, "ocr": False, "to": ["a <a@x>", "b"], "empty": None})
    head, body = split_front_matter(fm + "body")
    assert body == "body"
    assert 'source_filename: "He said: \\"no\\" #1"' in head and "page_count: 3" in head and "ocr: false" in head
    assert '  - "a <a@x>"' in head and "empty" not in head


def test_table_pipes_and_breaks():
    assert table([["a|b", "c\nd"], ["1", ""]]) == "| a\\|b | c<br>d |\n|---|---|\n| 1 |  |"
