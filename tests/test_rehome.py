from pathlib import Path

from app.config import Settings
from app.library import scan_media, scan_western
from app.rehome import rehome_jav_vr, rehome_western_vr


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        western_media_dir=str(tmp_path / "欧美"),
        vr_media_dir=str(tmp_path / "vrporn" / "western"),
        scrape_min_mb=0,
    )
    settings.ensure_dirs()
    settings.western_root.mkdir(parents=True)
    settings.vr_root.mkdir(parents=True)
    settings.jav_vr_root.mkdir(parents=True)
    return settings


def test_rehome_western_vr_moves_studio_folder(tmp_path):
    settings = _settings(tmp_path)
    src = settings.western_root / "SqueezeVR"
    src.mkdir()
    video = src / "SqueezeVR_Fist_Time.mp4"
    video.write_bytes(b"x" * 80)
    (src / "SqueezeVR_Fist_Time.nfo").write_text("<movie/>", encoding="utf-8")
    (src / "SqueezeVR_Fist_Time-poster.jpg").write_bytes(b"j")
    (src / "movie.nfo").write_text("<movie/>", encoding="utf-8")
    (settings.western_root / "Brazzers").mkdir()
    (settings.western_root / "Brazzers" / "clip.mp4").write_bytes(b"y" * 80)

    result = rehome_western_vr(settings)
    dest = settings.vr_root / "SqueezeVR" / "SqueezeVR_Fist_Time.mp4"
    assert result["studios"] == ["SqueezeVR"]
    assert dest.is_file()
    assert dest.read_bytes() == b"x" * 80
    assert (settings.vr_root / "SqueezeVR" / "SqueezeVR_Fist_Time.nfo").is_file()
    assert not src.exists()
    assert (settings.western_root / "Brazzers" / "clip.mp4").is_file()
    rows = scan_western(settings.vr_root, "vr")
    assert rows[0]["studio"] == "SqueezeVR"
    assert rows[0]["shelf"] == "vr"


def test_rehome_jav_vr_moves_code_folder_and_merges(tmp_path):
    settings = _settings(tmp_path)
    src = settings.media_dir / "202205" / "DSVR-1124"
    src.mkdir(parents=True)
    (src / "DSVR-1124.mp4").write_bytes(b"d" * 80)
    (src / "poster.jpg").write_bytes(b"p")
    (src / "DSVR-1124.nfo").write_text("<movie/>", encoding="utf-8")
    keep = settings.media_dir / "202205" / "SSIS-001"
    keep.mkdir()
    (keep / "SSIS-001.mp4").write_bytes(b"s" * 80)

    existing = settings.jav_vr_root / "PPVR" / "PPVR-002"
    existing.mkdir(parents=True)
    (existing / "PPVR-002.part1.mp4").write_bytes(b"a" * 80)
    ppvr = settings.media_dir / "202012" / "PPVR-002"
    ppvr.mkdir(parents=True)
    (ppvr / "PPVR-002.mp4").write_bytes(b"b" * 80)
    (ppvr / "poster.jpg").write_bytes(b"q")

    result = rehome_jav_vr(settings)
    assert "DSVR-1124" in result["codes"]
    assert "PPVR-002" in result["codes"]
    dest = settings.jav_vr_root / "DSVR" / "DSVR-1124"
    assert (dest / "DSVR-1124.mp4").is_file()
    assert (dest / "poster.jpg").is_file()
    assert not src.exists()
    assert (keep / "SSIS-001.mp4").is_file()
    merged = settings.jav_vr_root / "PPVR" / "PPVR-002"
    assert (merged / "PPVR-002.part1.mp4").is_file()
    assert (merged / "PPVR-002.mp4").is_file()
    assert not ppvr.exists()
    rows = {row["code"]: row for row in scan_media(settings.jav_vr_root)}
    assert rows["DSVR-1124"]["path"] == "DSVR/DSVR-1124"
    assert rows["PPVR-002"]["path"] == "PPVR/PPVR-002"
