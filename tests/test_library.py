from app.library import attach_library, library_info, scan_media


def test_scan_media_indexes_code_folders(tmp_path):
    folder = tmp_path / "202102" / "SSIS-001"
    folder.mkdir(parents=True)
    (folder / "SSIS-001.mp4").write_bytes(b"x")
    (folder / "SSIS-001.nfo").write_text("<movie/>", encoding="utf-8")
    (folder / "poster.jpg").write_bytes(b"jpg")
    empty = tmp_path / "202103" / "IPX-001"
    empty.mkdir(parents=True)
    (empty / "poster.jpg").write_bytes(b"jpg")
    rows = scan_media(tmp_path)
    by_code = {r["code"]: r for r in rows}
    assert "SSIS-001" in by_code
    assert by_code["SSIS-001"]["path"] == "202102/SSIS-001"
    assert by_code["SSIS-001"]["has_nfo"] == 1
    assert by_code["SSIS-001"]["has_poster"] == 1
    assert "IPX-001" not in by_code


def test_scan_prefers_newer_month(tmp_path):
    old = tmp_path / "202101" / "SSIS-001"
    new = tmp_path / "202102" / "SSIS-001"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "a.mp4").write_bytes(b"x")
    (new / "b.mp4").write_bytes(b"x")
    rows = scan_media(tmp_path)
    assert len(rows) == 1
    assert rows[0]["path"] == "202102/SSIS-001"


def test_attach_library():
    hits = {"SSIS-001": {"path": "202102/SSIS-001", "has_video": 1, "has_nfo": 1, "has_poster": 0}}
    items = attach_library(
        [{"code": "SSIS-001", "title": "a"}, {"code": "IPX-001", "title": "b"}],
        hits,
    )
    assert items[0]["library"]["present"] is True
    assert items[0]["library"]["path"] == "202102/SSIS-001"
    assert items[1]["library"]["present"] is False
    assert library_info(None) == {"present": False}
