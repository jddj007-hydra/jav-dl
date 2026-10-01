"""Move VR titles that landed in the flat JAV / western trees into vrporn."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.codes import jav_vr_maker, normalize_code
from app.config import Settings
from app.library import _drop_empty_dir
from app.scrape import is_video
from app.studios import is_vr_studio
from app.western_archive import _place_name, studio_dir

log = logging.getLogger("app.rehome")


def _move_file(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src.name
    if target.exists():
        try:
            if target.stat().st_size == src.stat().st_size and target.stat().st_size > 0:
                src.unlink()
                return target
        except OSError:
            pass
        target = dest_dir / _place_name(dest_dir, src.stem, src.suffix or "")
    shutil.move(str(src), str(target))
    return target


def _merge_dir(src: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    for item in list(src.iterdir()):
        if item.is_dir():
            moved += _merge_dir(item, dest / item.name)
            continue
        if not item.is_file():
            continue
        _move_file(item, dest)
        moved += 1
    try:
        src.rmdir()
    except OSError:
        pass
    return moved


def rehome_western_vr(settings: Settings) -> dict:
    src_root = settings.western_root
    dest_root = settings.vr_root
    if src_root is None or dest_root is None or src_root.resolve() == dest_root.resolve():
        return {"moved": 0, "studios": []}
    if not src_root.is_dir():
        return {"moved": 0, "studios": []}
    studios: list[str] = []
    moved = 0
    for studio in sorted(path for path in src_root.iterdir() if path.is_dir()):
        if not is_vr_studio(studio.name):
            continue
        dest = studio_dir(dest_root, studio.name)
        count = 0
        for item in list(studio.iterdir()):
            if item.name.lower() == "movie.nfo":
                try:
                    item.unlink()
                except OSError:
                    pass
                continue
            if item.is_file():
                _move_file(item, dest)
                count += 1
            elif item.is_dir():
                count += _merge_dir(item, dest / item.name)
        _drop_empty_dir(studio, src_root.resolve())
        if count:
            studios.append(studio.name)
            moved += count
            log.info("欧美 VR 已迁到 %s (%s 个文件)", dest, count)
    return {"moved": moved, "studios": studios}


def rehome_jav_vr(settings: Settings) -> dict:
    src_root = settings.media_dir
    dest_root = settings.jav_vr_root
    if dest_root is None or not src_root.is_dir():
        return {"moved": 0, "codes": []}
    if src_root.resolve() == dest_root.resolve():
        return {"moved": 0, "codes": []}
    codes: list[str] = []
    moved = 0
    for month in sorted(path for path in src_root.iterdir() if path.is_dir()):
        for code_dir in list(month.iterdir()):
            if not code_dir.is_dir():
                continue
            code = normalize_code(code_dir.name) or code_dir.name.strip().upper()
            maker = jav_vr_maker(code)
            if not maker:
                continue
            if not any(is_video(path) for path in code_dir.iterdir() if path.is_file()):
                continue
            dest = dest_root / maker / code
            count = _merge_dir(code_dir, dest)
            _drop_empty_dir(month, src_root.resolve())
            if count:
                codes.append(code)
                moved += count
                log.info("番号 VR 已迁到 %s (%s 个文件)", dest, count)
    return {"moved": moved, "codes": codes}


def rehome_misplaced_vr(settings: Settings) -> dict:
    western = rehome_western_vr(settings)
    jav = rehome_jav_vr(settings)
    return {"western": western, "jav": jav}
