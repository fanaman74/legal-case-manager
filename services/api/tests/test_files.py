"""Uploading, listing and downloading files."""

import hashlib
import os
import stat

import pytest

from app.routes.files import split_path


@pytest.fixture
def case(new_case):
    return new_case("Smith v Jones")


def test_upload_stores_original_unchanged(admin, case, cfg):
    data = b"%PDF-1.7 letter before action\x00\xff"
    r = admin.upload(case, "Correspondence/2024/Letter before action.pdf", data)
    assert r.status_code == 201
    f = r.json()["file"]
    assert r.json()["result"] == "stored"
    assert (f["name"], f["folder"], f["type"], f["size"], f["status"], f["added_by"]) == ("Letter before action.pdf", "Correspondence/2024", "pdf", len(data), "stored", "fred")
    assert f["sha256"] == hashlib.sha256(data).hexdigest()
    stored = cfg.cases_dir / case / "original" / f"{f['id']}.pdf"
    assert stored.read_bytes() == data
    assert not os.stat(stored).st_mode & stat.S_IWUSR  # read-only once stored
    assert not list((cfg.cases_dir / case / "original").glob(".incoming-*"))


def test_duplicate_flagged_not_reimported(admin, case):
    first = admin.upload(case, "a/contract.docx", b"PK contract").json()["file"]
    r = admin.upload(case, "b/contract copy.docx", b"PK contract")
    assert r.status_code == 409
    assert r.json()["result"] == "duplicate"
    assert r.json()["existing"]["id"] == first["id"]
    assert admin.get(f"/api/cases/{case}/files").json()["total"] == 1


def test_same_file_in_two_cases_is_fine(admin, case, new_case):
    other = new_case("Other")
    assert admin.upload(case, "x.pdf", b"%PDF same").status_code == 201
    assert admin.upload(other, "x.pdf", b"%PDF same").status_code == 201


@pytest.mark.parametrize("name", ["archive.zip", "README", "notes.PST", "photo.gif"])
def test_unsupported_types_reported(admin, case, name):
    r = admin.upload(case, name, b"data")
    assert r.status_code == 415
    assert r.json()["result"] == "unsupported"
    assert ".docx" in r.json()["detail"]


def test_extensions_ignore_case(admin, case):
    r = admin.upload(case, "SCAN.TIFF", b"II*\x00")
    assert r.status_code == 201 and r.json()["file"]["type"] == "image"


@pytest.mark.parametrize("path", ["../outside.pdf", "a/../../b.pdf", "a//b.pdf", "", "a/", "bad\x01name.pdf", "./x.pdf"])
def test_unsafe_paths_refused(admin, case, path):
    assert admin.upload(case, path, b"%PDF").status_code == 400


def test_split_path():
    assert split_path("/Top/Sub/file.pdf") == ("Top/Sub", "file.pdf")
    assert split_path("Top\\Sub\\file.pdf") == ("Top/Sub", "file.pdf")
    assert split_path("file.pdf") == ("", "file.pdf")


def test_size_limit(admin, case, cfg, monkeypatch, rt):
    monkeypatch.setattr(rt, "cfg", cfg.__class__(**{**cfg.__dict__, "max_upload_bytes": 10}))
    r = admin.upload(case, "big.pdf", b"x" * 11)
    assert r.status_code == 413
    assert "10 B" not in r.json()["detail"]
    assert admin.get(f"/api/cases/{case}/files").json()["total"] == 0
    assert not list((cfg.cases_dir / case / "original").glob(".incoming-*"))


def test_size_limit_without_content_length(admin, case, cfg, monkeypatch, rt):
    monkeypatch.setattr(rt, "cfg", cfg.__class__(**{**cfg.__dict__, "max_upload_bytes": 10}))

    def body():
        yield b"x" * 6
        yield b"x" * 6

    r = admin.put(f"/api/cases/{case}/files", params={"path": "big.pdf"}, content=body())
    assert r.status_code == 413
    assert not list((cfg.cases_dir / case / "original").glob(".incoming-*"))


def test_list_filter_sort(admin, case):
    for path, data in [
        ("Pleadings/claim.pdf", b"1"),
        ("Pleadings/defence.docx", b"2"),
        ("Pleadings/Exhibits/ex1.jpg", b"3"),
        ("Email/re offer.msg", b"4"),
        ("PleadingsOld/old.pdf", b"5"),
        ("index.txt", b"6"),
    ]:
        assert admin.upload(case, path, data).status_code == 201
    names = lambda **p: [f["name"] for f in admin.get(f"/api/cases/{case}/files", params={"sort": "name", "order": "asc", **p}).json()["items"]]
    assert names() == ["claim.pdf", "defence.docx", "ex1.jpg", "index.txt", "old.pdf", "re offer.msg"]
    assert names(folder="Pleadings") == ["claim.pdf", "defence.docx", "ex1.jpg"]  # not PleadingsOld
    assert names(type="pdf") == ["claim.pdf", "old.pdf"]
    assert names(q="OFF") == ["re offer.msg"]
    assert names(q="%") == []
    r = admin.get(f"/api/cases/{case}/files", params={"limit": 2, "sort": "size"}).json()
    assert r["total"] == 6 and len(r["items"]) == 2
    folders = admin.get(f"/api/cases/{case}/folders").json()["items"]
    assert folders == [
        {"path": "", "files": 1},
        {"path": "Email", "files": 1},
        {"path": "Pleadings", "files": 2},
        {"path": "Pleadings/Exhibits", "files": 1},
        {"path": "PleadingsOld", "files": 1},
    ]
    assert admin.get(f"/api/cases/{case}/files", params={"sort": "sha256"}).status_code == 422


def test_tags(admin, case):
    fid = admin.upload(case, "a.pdf", b"%PDF").json()["file"]["id"]
    r = admin.patch(f"/api/cases/{case}/files/{fid}", json={"tags": ["  key   document ", "privileged", "privileged"]})
    assert r.json()["tags"] == ["key document", "privileged"]
    assert [f["id"] for f in admin.get(f"/api/cases/{case}/files", params={"tag": "privileged"}).json()["items"]] == [fid]
    assert admin.patch(f"/api/cases/{case}/files/{fid}", json={"tags": [" "]}).status_code == 400


def test_download_is_always_an_attachment(admin, case):
    fid = admin.upload(case, "page.txt", b"<script>alert(1)</script>").json()["file"]["id"]
    r = admin.get(f"/api/cases/{case}/files/{fid}/original")
    assert r.status_code == 200
    assert r.content == b"<script>alert(1)</script>"
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_missing_original_reported(admin, case, cfg):
    f = admin.upload(case, "a.pdf", b"%PDF").json()["file"]
    path = cfg.cases_dir / case / "original" / f"{f['id']}.pdf"
    path.chmod(0o600)
    path.unlink()
    assert admin.get(f"/api/cases/{case}/files/{f['id']}/original").status_code == 410
