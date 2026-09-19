from app.config import Settings, _overlay, normalize_downloader, save_user_config
from app.models import SettingsUpdate


def test_normalize_downloader():
    assert normalize_downloader("Xunlei") == "xunlei"
    assert normalize_downloader("aria2") == "aria2"
    assert normalize_downloader("nope") == "aria2"
    assert normalize_downloader(None) == "aria2"


def test_user_config_persists_downloader(tmp_path):
    s = Settings(data_dir=tmp_path, download_dir=tmp_path / "dl")
    s.ensure_dirs()
    merged = save_user_config(
        s,
        {
            "downloader": "Xunlei",
            "xunlei_url": "http://192.168.1.2:2345",
            "xunlei_username": "admin",
            "xunlei_password": "secret",
            "xunlei_device_name": "群晖-xunlei",
        },
    )
    assert merged.downloader == "xunlei"
    pub = merged.public_dict()
    assert pub["xunlei_password_set"] is True
    assert "xunlei_password" not in pub
    assert pub["xunlei_username"] == "admin"

    over = _overlay(Settings(data_dir=tmp_path, download_dir=tmp_path / "dl"))
    assert over.downloader == "xunlei"
    assert over.xunlei_url == "http://192.168.1.2:2345"
    assert over.xunlei_password == "secret"


def test_omitting_password_keeps_old(tmp_path):
    s = Settings(data_dir=tmp_path, download_dir=tmp_path / "dl")
    s.ensure_dirs()
    save_user_config(s, {"xunlei_password": "secret", "downloader": "xunlei"})
    current = _overlay(Settings(data_dir=tmp_path, download_dir=tmp_path / "dl"))
    merged = save_user_config(current, {"downloader": "xunlei", "xunlei_url": "http://nas:2345"})
    assert merged.xunlei_password == "secret"
    assert merged.xunlei_url == "http://nas:2345"


def test_settings_update_accepts_downloader():
    body = SettingsUpdate(downloader="xunlei", xunlei_password="")
    dumped = body.model_dump(exclude_none=True)
    assert dumped["downloader"] == "xunlei"
    assert dumped["xunlei_password"] == ""
