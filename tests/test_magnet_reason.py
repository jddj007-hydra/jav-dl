import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "app" / "static" / "app.js"


def _reason(item: dict, western: bool) -> str:
    script = APP_JS.read_text(encoding="utf-8")
    start = script.index("function magnetReason")
    end = script.index("\nfunction ", start)
    code = script[start:end] + "\nprocess.stdout.write(magnetReason(" + json.dumps(item) + ", " + json.dumps(western) + "));"
    out = subprocess.run(["node", "-e", code], check=True, capture_output=True, text=True)
    return out.stdout


def test_jav_reason_starts_with_uc_and_uses_fields():
    text = _reason(
        {"tags": ["C", "UC"], "heat": 320, "size": "6.2 GB", "pack": False},
        False,
    )
    assert text == "UC · C · 热度 320 · 6.2 GB"


def test_jav_pack_follows_tags():
    text = _reason(
        {"tags": ["U"], "heat": 10, "size": "20 GB", "pack": True},
        False,
    )
    assert text == "U · pack · 热度 10 · 20 GB"


def test_jav_without_tags_is_heat_and_size():
    text = _reason({"tags": [], "heat": 0, "size": ""}, False)
    assert text == "热度 0 · ?"


def test_western_reason_does_not_invent_uc():
    text = _reason(
        {"tags": ["UC", "C"], "heat": 80, "size": "3.1 GB", "pack": False},
        True,
    )
    assert text == "热度 80 · 3.1 GB"
    assert "UC" not in text


def test_western_pack_is_only_the_pack_flag():
    text = _reason(
        {"tags": [], "heat": 4, "size": "18 GB", "pack": True},
        True,
    )
    assert text == "pack · 热度 4 · 18 GB"
