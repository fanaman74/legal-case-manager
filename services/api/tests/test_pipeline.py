"""Upload, background conversion and the converted document, end to end
(the worker's job runs here in the test process; the converter runs in its
own child process as it does on Windows)."""

import os
import shutil
import subprocess

import pytest

from app import jobs, pipeline
from tests import samples
from tests.conftest import RUST_SERVER
from tests.test_convert import needs_ocr


def work(rt, kinds=("convert",)) -> int:
    """Run every waiting job, as the worker would. Returns how many ran."""
    if RUST_SERVER:
        out = subprocess.run([RUST_SERVER, "work", *kinds], env={**os.environ, "DATA_DIR": str(rt.cfg.data_dir)},
                             capture_output=True, check=True, timeout=600)
        return int(out.stdout)
    n = 0
    while (job := jobs.claim(rt.db, kinds)) is not None:
        pipeline.run_convert(rt, job)
        n += 1
    return n


@pytest.fixture
def case(new_case):
    return new_case("Smith v Jones")


def test_upload_converts_to_markdown_with_front_matter(admin, case, rt):
    f = admin.upload(case, "Statements/Jane Doe.docx", samples.docx_bytes()).json()["file"]
    assert f["status"] == "queued"
    assert admin.get(f"/api/cases/{case}/progress").json()["jobs"]["convert"]["queued"] == 1
    assert work(rt) == 1
    f = admin.get(f"/api/cases/{case}/files/{f['id']}").json()
    assert (f["status"], f["page_count"], f["warnings"], f["title"]) == ("converted", 2, [], "Witness statement of Jane Doe")
    md = admin.get(f"/api/cases/{case}/files/{f['id']}/markdown").json()
    fm = md["front_matter"]
    for line in (f'doc_id: "{f["id"]}"', f'case_id: "{case}"', 'source_filename: "Jane Doe.docx"', 'relative_path: "Statements/Jane Doe.docx"',
                 'source_type: "word"', f'sha256: "{f["sha256"]}"', "page_count: 2", "ocr: false", "converted_at: "):
        assert line in fm
    assert "## Background" in md["body"]
    # Reproducible: the Markdown's hash is recorded, and the conversion is in the audit log.
    log = admin.get(f"/api/cases/{case}/activity").json()["items"]
    convert = next(e for e in log if e["action"] == "file.convert")
    assert convert["target"] == "Statements/Jane Doe.docx" and md["markdown_sha256"] in convert["detail"]
    assert any(e["action"] == "file.view" for e in log)


def test_email_attachments_become_linked_files(admin, case, rt):
    email = admin.upload(case, "Correspondence/reply.eml", samples.eml_bytes(attachment=samples.pdf_bytes())).json()["file"]
    work(rt)
    files = admin.get(f"/api/cases/{case}/files", params={"sort": "name", "order": "asc"}).json()["items"]
    assert [f["name"] for f in files] == ["Delivery note.pdf", "reply.eml"]
    note, reply = files
    assert note["parent_id"] == reply["id"] and note["folder"] == "Correspondence"
    assert note["status"] == "converted" and note["page_count"] == 3 and note["previews"] == 3
    assert reply["email"]["subject"] == "RE: Delivery of steel"
    assert reply["attachments"] == [{"name": "Delivery note.pdf", "doc_id": note["id"]}]
    fm = admin.get(f"/api/cases/{case}/files/{email['id']}/markdown").json()["front_matter"]
    assert 'subject: "RE: Delivery of steel"' in fm and 'message_id: "<abc123@claimant.example>"' in fm
    assert f'doc_id: "{note["id"]}"' in fm  # under attachments
    fm = admin.get(f"/api/cases/{case}/files/{note['id']}/markdown").json()["front_matter"]
    assert f'parent_doc_id: "{reply["id"]}"' in fm
    # Page images for the viewer.
    r = admin.get(f"/api/cases/{case}/files/{note['id']}/pages/2")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert admin.get(f"/api/cases/{case}/files/{note['id']}/pages/4").status_code == 404


def test_attachment_already_in_case_is_linked_not_duplicated(admin, case, rt):
    pdf = samples.pdf_bytes()
    first = admin.upload(case, "note.pdf", pdf).json()["file"]
    admin.upload(case, "reply.eml", samples.eml_bytes(attachment=pdf))
    work(rt)
    files = admin.get(f"/api/cases/{case}/files").json()["items"]
    assert len(files) == 2
    reply = next(f for f in files if f["type"] == "email")
    assert reply["attachments"] == [{"name": "Delivery note.pdf", "doc_id": first["id"], "duplicate": True}]


def test_failed_conversion_is_reported_and_retried(admin, case, rt):
    f = admin.upload(case, "broken.pdf", b"%PDF-1.4 broken").json()["file"]
    work(rt)
    f = admin.get(f"/api/cases/{case}/files/{f['id']}").json()
    assert f["status"] == "failed" and "can't be opened" in f["error"]
    assert admin.get(f"/api/cases/{case}/progress").json()["files"] == {"failed": 1}
    failed = admin.get("/api/jobs", params={"state": "failed"}).json()["items"]
    assert failed[0]["file"] == "broken.pdf" and "can't be opened" in failed[0]["error"]
    assert admin.post(f"/api/cases/{case}/convert", json={"what": "retry_failed"}).json() == {"queued": 1}
    assert admin.get(f"/api/cases/{case}/files/{f['id']}").json()["status"] == "queued"


@pytest.mark.python_only  # replaces Python internals
def test_converter_crash_retried_then_failed(admin, case, rt, monkeypatch):
    f = admin.upload(case, "letter.txt", b"hello").json()["file"]
    monkeypatch.setattr(pipeline.sys, "executable", shutil.which("false") or "/bin/false")
    work(rt)
    f = admin.get(f"/api/cases/{case}/files/{f['id']}").json()
    assert f["status"] == "failed" and "stopped unexpectedly" in f["error"]
    with rt.db.connect() as db:
        assert db.execute("SELECT attempts FROM jobs WHERE file_id = ?", (f["id"],)).fetchone()[0] == pipeline.MAX_ATTEMPTS


@pytest.fixture
def no_ocr(monkeypatch):
    """OCR not installed yet (a new computer, before the Control Center has installed it)."""
    monkeypatch.setattr(pipeline, "ocr_available", lambda: False)


@pytest.mark.python_only  # replaces Python internals
def test_scanned_pdf_waits_for_ocr_to_be_installed(admin, case, rt, no_ocr, monkeypatch):
    f = admin.upload(case, "scan.pdf", samples.scanned_pdf_bytes()).json()["file"]
    work(rt)
    f = admin.get(f"/api/cases/{case}/files/{f['id']}").json()
    assert f["status"] == "needs_ocr" and f["scanned_pages"] == [1] and f["ocr_state"] == "unavailable"
    # The scanned-page message is a note: the file isn't flagged as having warnings.
    assert f["warnings"] == [] and "scan with no text layer" in f["notes"][0]
    p = admin.get(f"/api/cases/{case}/progress").json()
    assert p["scanned"] == 1 and p["warnings"] == 0 and p["ocr"] == {"unavailable": 1}
    assert pipeline.ocr_catch_up(rt) == 0
    # Once OCR is installed the worker queues it by itself.
    monkeypatch.setattr(pipeline, "ocr_available", lambda: True)
    assert pipeline.ocr_catch_up(rt) == 1
    assert admin.get(f"/api/cases/{case}/files/{f['id']}").json()["ocr_state"] == "waiting"
    assert pipeline.ocr_catch_up(rt) == 0  # not queued twice
    assert admin.get(f"/api/cases/{case}/progress").json()["jobs"]["ocr"]["queued"] == 1


@needs_ocr
def test_scanned_pdf_is_read_with_ocr_by_itself(admin, case, rt):
    f = admin.upload(case, "scan.pdf", samples.scanned_pdf_bytes()).json()["file"]
    assert work(rt) == 1
    f = admin.get(f"/api/cases/{case}/files/{f['id']}").json()
    # The quick pass is done and the file is usable; OCR follows in its own lane.
    assert f["status"] == "needs_ocr" and f["ocr_state"] == "waiting"
    assert work(rt) == 0  # the convert lane doesn't take OCR jobs
    assert work(rt, ("ocr",)) == 1
    f = admin.get(f"/api/cases/{case}/files/{f['id']}").json()
    assert f["status"] == "converted" and f["ocr"] and f["ocr_state"] == "done" and f["scanned_pages"] == []
    assert f["warnings"] == [] and "read with OCR" in f["notes"][0]
    assert "steel never arrived" in admin.get(f"/api/cases/{case}/files/{f['id']}/markdown").json()["body"]
    assert work(rt, ("ocr",)) == 0  # nothing queued again


@needs_ocr
@pytest.mark.python_only  # replaces Python internals
def test_run_ocr_again_keeps_the_file_usable(admin, case, rt, no_ocr, monkeypatch):
    f = admin.upload(case, "scan.pdf", samples.scanned_pdf_bytes()).json()["file"]
    work(rt)
    monkeypatch.setattr(pipeline, "ocr_available", lambda: True)
    r = admin.post(f"/api/cases/{case}/files/{f['id']}/convert", json={"ocr": True})
    assert r.status_code == 200 and (r.json()["status"], r.json()["ocr_state"]) == ("needs_ocr", "waiting")
    work(rt, ("ocr",))
    f = admin.get(f"/api/cases/{case}/files/{f['id']}").json()
    assert f["status"] == "converted" and f["ocr"] and f["scanned_pages"] == []
    assert any(e["action"] == "file.ocr" for e in admin.get(f"/api/cases/{case}/activity").json()["items"])


@pytest.mark.python_only  # replaces Python internals
def test_failed_ocr_keeps_the_earlier_conversion(admin, case, rt, no_ocr, monkeypatch):
    f = admin.upload(case, "scan.pdf", samples.scanned_pdf_bytes()).json()["file"]
    work(rt)
    monkeypatch.setattr(pipeline, "ocr_available", lambda: True)
    pipeline.ocr_catch_up(rt)
    # Tesseract reports an error on the page (the converter's result has an error).
    monkeypatch.setattr(pipeline.subprocess, "run", _converter_says({"error": "OCR couldn't read a scanned page."}))
    pipeline.run_convert(rt, jobs.claim(rt.db, ("ocr",)))
    f = admin.get(f"/api/cases/{case}/files/{f['id']}").json()
    assert (f["status"], f["ocr_state"], f["ocr_error"]) == ("needs_ocr", "failed", "OCR couldn't read a scanned page.")
    assert f["converted_at"] and f["error"] == ""
    assert pipeline.ocr_catch_up(rt) == 0  # a failure isn't retried over and over
    status = admin.get(f"/api/cases/{case}/status").json()
    assert status["summary"]["ocr"]["failed"] == 1 and status["items"][0]["stage"] == "attention"


def _converter_says(result: dict):
    import json as _json

    def run(args, **kw):
        (pipeline.Path(args[5]) / "result.json").write_text(_json.dumps(result))

        class Done:
            returncode, stderr = 0, b""
        return Done()
    return run


def test_permissions(admin, case, rt, people, new_case):
    f = admin.upload(case, "note.txt", b"a note").json()["file"]
    work(rt)
    reader = people("rita", "readonly", [case])
    outsider = people("otto", "editor", [new_case("Other case")])
    assert reader.get(f"/api/cases/{case}/files/{f['id']}/markdown").status_code == 200
    assert reader.post(f"/api/cases/{case}/files/{f['id']}/convert", json={}).status_code == 403
    assert reader.post(f"/api/cases/{case}/convert", json={"what": "retry_failed"}).status_code == 403
    for url in (f"/api/cases/{case}/files/{f['id']}/markdown", f"/api/cases/{case}/progress", f"/api/cases/{case}/files/{f['id']}/pages/1"):
        assert outsider.get(url).status_code == 404
    assert outsider.get("/api/jobs").status_code == 403
    # The status banner only counts work in the person's own cases.
    admin.upload(case, "another.txt", b"queued")
    assert outsider.get("/api/status").json()["jobs"] == {}
    assert reader.get("/api/status").json()["jobs"]["convert"]["queued"] == 1


def test_markdown_before_conversion(admin, case):
    f = admin.upload(case, "note.txt", b"a note").json()["file"]
    r = admin.get(f"/api/cases/{case}/files/{f['id']}/markdown")
    assert r.status_code == 409 and "hasn't been converted yet" in r.json()["detail"]


def test_worker_recovers_jobs_after_a_crash(admin, case, rt):
    admin.upload(case, "note.txt", b"a note")
    job = jobs.claim(rt.db, ("convert",))
    assert job is not None and jobs.claim(rt.db, ("convert",)) is None
    assert jobs.recover(rt.db) == 1
    assert work(rt) == 1


@pytest.mark.python_only  # replaces Python internals
def test_old_files_are_queued_by_the_upgrade(tmp_path):
    """Files stored before conversion existed are converted after upgrading."""
    import sqlite3

    from app.db import MIGRATIONS, Database

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.executescript(MIGRATIONS[0] + "PRAGMA user_version = 1;")
    db.execute("INSERT INTO cases (id, name, created_at) VALUES ('c' , 'Case', 'now')")
    db.execute("INSERT INTO files (id, case_id, name, ext, source_type, size, sha256, added_at) VALUES ('f', 'c', 'a.pdf', '.pdf', 'pdf', 1, 'x', 'now')")
    db.commit()
    db.close()
    database = Database(path)
    with database.connect() as c:
        assert c.execute("SELECT status FROM files").fetchone()[0] == "queued"
        assert tuple(c.execute("SELECT kind, file_id, state FROM jobs").fetchone()) == ("convert", "f", "queued")


@pytest.mark.python_only  # replaces Python internals
def test_upgrade_moves_notes_out_of_warnings(tmp_path):
    """Files converted before notes existed: informational messages become
    notes, and scanned PDFs are picked up for OCR by the worker."""
    import json
    import sqlite3

    from app.db import MIGRATIONS, Database

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    for i, script in enumerate(MIGRATIONS[:6], start=1):
        db.executescript(f"{script}\nPRAGMA user_version = {i};")
    db.execute("INSERT INTO cases (id, name, created_at) VALUES ('c' , 'Case', 'now')")
    msgs = ["This document has tracked changes. The Markdown shows the text with all changes accepted.", "Page 3 couldn't be read (ValueError)."]
    db.execute("INSERT INTO files (id, case_id, name, ext, source_type, size, sha256, added_at, status, warnings) VALUES ('f', 'c', 'a.docx', '.docx', 'word', 1, 'x', 'now', 'converted', ?)", (json.dumps(msgs),))
    db.execute("INSERT INTO files (id, case_id, name, ext, source_type, size, sha256, added_at, status, ocr) VALUES ('g', 'c', 'b.pdf', '.pdf', 'pdf', 1, 'y', 'now', 'converted', 1)")
    db.commit()
    db.close()
    database = Database(path)
    with database.connect() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        row = c.execute("SELECT warnings, notes FROM files WHERE id = 'f'").fetchone()
        assert json.loads(row[0]) == msgs[1:] and json.loads(row[1]) == msgs[:1]
        assert c.execute("SELECT ocr_state FROM files WHERE id = 'g'").fetchone()[0] == "done"
