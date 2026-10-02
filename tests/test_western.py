import asyncio
import time
from pathlib import Path

from app.codes import normalize_code
from app.config import Settings, _overlay, save_user_config
from app.db import Database
from app.downloader.aria2 import Aria2
from app.downloader.jobs import JobManager
from app.ranking import sort_by_heat
from app.routers.images import _allowed
from app.scrape import list_ready_sources
from app.slug import western_slug
from app.western_archive import list_ready_western
from app.western_magnets import is_western_release_name, parse_release_date, rank_western_magnets, western_search_terms
from app.sources.javbus import latest_page_url
from app.sources.tpdb import duration_minutes, map_item


def test_excluded_orientation_tags():
    from app.sources.tpdb import is_excluded_orientation

    assert is_excluded_orientation(["Gay"])
    assert is_excluded_orientation(["Threesome (Gay)"])
    assert is_excluded_orientation(["Bisexual"])
    assert is_excluded_orientation(["Solo Trans"])
    assert is_excluded_orientation(["Futanari"])
    assert is_excluded_orientation([], "男同作品")
    assert not is_excluded_orientation([], "Gayle and Anna")
    assert not is_excluded_orientation(["Anal", "Big Tits", "Hardcore"])
    assert not is_excluded_orientation([], "School of Cock")


def test_latest_page_urls():
    base = "https://www.javbus.com"
    assert latest_page_url(base, "censored", 1) == "https://www.javbus.com/"
    assert latest_page_url(base, "censored", 2) == "https://www.javbus.com/page/2"
    assert latest_page_url(base + "/", "uncensored", 1) == "https://www.javbus.com/uncensored"
    assert latest_page_url(base, "uncensored", 3) == "https://www.javbus.com/uncensored/page/3"
    assert latest_page_url(base, "censored", 1, "vr") == "https://www.javbus.com/genre/7x"
    assert latest_page_url(base, "censored", 2, "vr") == "https://www.javbus.com/genre/7x/2"
    assert latest_page_url(base, "uncensored", 1, "vr") == "https://www.javbus.com/uncensored/genre/gre162"


def test_map_tpdb_scene_uses_parent_and_minutes():
    raw = {
        "id": "abc",
        "title": "Room",
        "date": "2024-05-06T00:00:00",
        "duration": 3660,
        "description": "hello",
        "url": "https://example.test/abc",
        "site": {"name": "Brazzers"},
        "posters": {"large": "https://cdn.theporndb.net/p.jpg", "small": "https://cdn.theporndb.net/s.jpg"},
        "background": {"full": "https://cdn.theporndb.net/b.jpg"},
        "performers": [
            {"name": "Alias", "parent": {"name": "Riley Reid", "face": "https://cdn.theporndb.net/f.jpg"}},
            {"name": "Other"},
        ],
        "tags": [{"id": 12, "name": "Feature"}, {"name": "Feature"}, "Feature"],
    }
    item = map_item(raw, "scene")
    assert item["tags"] == ["Feature"]
    assert item["tag_refs"] == [{"id": "12", "name": "Feature"}]
    assert item["site"] == "Brazzers"
    assert item["performers"] == ["Riley Reid", "Other"]
    assert item["cover"].endswith("/p.jpg")
    assert item["background"].endswith("/b.jpg")
    assert item["date"] == "2024-05-06"
    assert item["duration"] == "61"
    assert item["vr"] is False
    assert duration_minutes(90) == "1"
    assert duration_minutes(0) == ""
    assert duration_minutes(None) == ""


def test_map_item_uses_image_when_posters_missing():
    item = map_item({
        "id": "vr1",
        "title": "Headset",
        "date": "2024-05-06",
        "site": {"name": "VR Bangers"},
        "image": "https://cdn.theporndb.net/image.jpg",
        "background": "https://cdn.theporndb.net/bg.jpg",
        "tags": [{"name": "Virtual Reality"}],
        "performers": [],
    }, "scene")
    assert item["cover"].endswith("/image.jpg")
    assert item["background"].endswith("/bg.jpg")
    assert item["vr"] is True


def test_heat_sort_ignores_single_letter_tags():
    ranked = sort_by_heat([
        {"title": "Scene U C", "heat": 1, "size": "1 GB"},
        {"title": "Plain scene", "heat": 50, "size": "1 GB"},
        {"title": "Studio 合集", "heat": 999, "size": "1 GB"},
    ])
    assert [it["title"] for it in ranked] == ["Plain scene", "Scene U C", "Studio 合集"]
    assert ranked[0]["rank"] == 1
    assert ranked[-1]["pack"] is True


def test_token_hidden_and_blank_keeps_old(tmp_path):
    settings = Settings(data_dir=tmp_path, download_dir=tmp_path / "dl")
    settings.ensure_dirs()
    saved = save_user_config(settings, {"tpdb_api_key": "secret-token"})
    public = saved.public_dict()
    assert public["tpdb_api_key_set"] is True
    assert "secret-token" not in str(public)
    assert "tpdb_api_key" not in public

    updates = {"tpdb_api_key": "", "javbus_base": "https://www.javbus.com"}
    if updates.get("tpdb_api_key") == "":
        updates.pop("tpdb_api_key")
    current = _overlay(Settings(data_dir=tmp_path, download_dir=tmp_path / "dl"))
    merged = save_user_config(current, updates)
    assert merged.tpdb_api_key == "secret-token"


def test_parse_release_date_from_scene_name():
    assert parse_release_date(
        "BrazzersExxtra.22.09.20.Ella.Reese.School.Of.Cock.XXX.1080p.MP4-WRB"
    ) == "2022-09-20"
    assert parse_release_date("clip.2160p.mp4") is None


def test_western_search_terms_use_filename_date():
    terms = western_search_terms(
        "Brazzers Exxtra",
        "School of Cock",
        ["Ella Reese"],
        "2022-09-20",
    )
    assert terms[0] == "BrazzersExxtra 22.09.20"
    assert "BrazzersExxtra 2022.09.20" in terms
    assert terms[-1] == "BrazzersExxtra"


def test_vr_studio_search_keeps_prefix_and_initials():
    bangers = western_search_terms("VR Bangers", "Office Fantasy", ["Jane Doe"], "2024-05-06")
    assert bangers[0] == "VRBangers 24.05.06"
    assert "VRBangers 2024.05.06" in bangers
    assert "VR.Bangers 24.05.06" in bangers
    assert "BangersVR 24.05.06" in bangers
    assert "VRB 24.05.06" in bangers

    hush = western_search_terms("VR Hush", "From the Vault", ["Dani Daniels"], "2019-12-16")
    assert hush[0] == "VRHush 19.12.16"
    assert "VRHush 2019.12.16" in hush
    assert "VR.Hush 19.12.16" in hush
    assert "HushVR 19.12.16" in hush
    assert "VRH 19.12.16" in hush

    vrp = western_search_terms(
        "Virtual Real Porn",
        "Between Pillows And Whispers",
        [],
        "2026-06-07",
    )
    assert vrp[0] == "VirtualRealPorn 26.06.07"
    assert "VirtualRealPorn 2026.06.07" in vrp
    assert "Virtual.Real.Porn 26.06.07" in vrp
    assert "VRP 26.06.07" in vrp

    cosplay = western_search_terms("VR Cosplay X", "Hero", [], "2023-01-02")
    assert cosplay[0] == "VRCosplayX 23.01.02"
    assert "VRCX 23.01.02" in cosplay


def test_namer_studio_filename_prefixes():
    wankz = western_search_terms("Wankz VR", "Scene", [], "2024-01-02")
    assert wankz[0] == "WankzVR 24.01.02"
    badoink = western_search_terms("Badoink VR", "Scene", [], "2024-01-02")
    assert badoink[0] == "BadoinkVR 24.01.02"
    eighteen = western_search_terms("18 VR", "Scene", [], "2024-01-02")
    assert eighteen[0] == "18VR 24.01.02"
    slr = western_search_terms("Sex Like Real", "Scene", [], "2024-01-02")
    assert slr[0] == "SexLikeReal 24.01.02"
    assert "SLR 24.01.02" in slr
    originals = western_search_terms("SLR Originals", "Scene", [], "2024-01-02")
    assert originals[0] == "SLROriginals 24.01.02"
    assert "SLR 24.01.02" in originals
    luv = western_search_terms("All Her Luv", "Scene", [], "2024-01-02")
    assert luv[0] == "AllHerLuv 24.01.02"
    bex = western_search_terms("Brazzers Exxtra", "School of Cock", [], "2022-09-20")
    assert "BEX 22.09.20" in bex
    na = western_search_terms("Naughty America VR", "Scene", [], "2024-01-02")
    assert na[0] == "NaughtyAmericaVR 24.01.02"
    assert "NAVR 24.01.02" in na
    tiny = western_search_terms("Tiny4K", "Scene", [], "2024-01-02")
    assert tiny[0] == "Tiny4K 24.01.02"
    assert "T4K 24.01.02" in tiny
    digital = western_search_terms("Digital Playground", "Scene", [], "2024-01-02")
    assert digital[0] == "DigitalPlayground 24.01.02"
    assert "Digital.Playground 24.01.02" in digital
    property_sex = western_search_terms("Property Sex", "Scene", [], "2024-01-02")
    assert property_sex[0] == "PropertySex 24.01.02"
    assert "PSE 24.01.02" in property_sex
    doe = western_search_terms("Let's Doe It", "Scene", [], "2024-01-02")
    assert doe[0] == "LetsDoeIt 24.01.02"
    girl = western_search_terms("A Girl Knows", "Scene", [], "2024-01-02")
    assert girl[0] == "AGirlKnows 24.01.02"
    assert "AGK 24.01.02" in girl
    sextury = western_search_terms("21 Sextury", "Scene", [], "2024-01-02")
    assert sextury[0] == "21Sextury 24.01.02"
    nf = western_search_terms("NF Busty", "Scene", [], "2024-01-02")
    assert nf[0] == "NFBusty 24.01.02"
    blacked = western_search_terms("Blacked Raw", "Scene", [], "2024-01-02")
    assert blacked[0] == "BlackedRaw 24.01.02"
    taxi = western_search_terms("Fake Taxi", "Scene", [], "2024-01-02")
    assert taxi[0] == "FakeTaxi 24.01.02"
    jules = western_search_terms("Jules Jordan", "Scene", [], "2024-01-02")
    assert jules[0] == "JulesJordan 24.01.02"
    white = western_search_terms("The White Boxxx", "Scene", [], "2024-01-02")
    assert white[0] == "TheWhiteBoxxx 24.01.02"
    assert "WhiteBoxxx 24.01.02" in white
    aliased = western_search_terms("Brazzers Exxtra", "Scene", [], "2022-09-20", extra_names=["BrazzersExxtra", "BEX"])
    assert aliased[0] == "BrazzersExxtra 22.09.20"
    assert "BEX 22.09.20" in aliased
    compilations = western_search_terms("Vixen Compilations", "Vol 3", [], "2026-09-20")
    assert compilations[0] == "VixenCompilations 26.09.20"
    assert "Vixen 26.09.20" in compilations
    best = western_search_terms("Best Of LetsDoeIt", "Part 8", [], "2026-09-24")
    assert best[0] == "BestOfLetsDoeIt 26.09.24"
    assert "LetsDoeIt 26.09.24" in best
    assert "LDI 26.09.24" in best


def test_rank_keeps_same_release_day_and_prefers_the_scene():
    items = [
        {
            "title": "BrazzersExxtra.22.09.20.Phoenix.Marie.BrideZZilla.Part.2.XXX.1080p",
            "heat": 500,
            "size": "2 GB",
            "info_hash": "a" * 40,
        },
        {
            "title": "BrazzersExxtra.22.09.20.Ella.Reese.School.Of.Cock.XXX.1080p",
            "heat": 20,
            "size": "2 GB",
            "info_hash": "b" * 40,
        },
        {
            "title": "BRAZZERS - Brazzers Exxtra - Veruca James",
            "heat": 9000,
            "size": "1 GB",
            "date": "2017-08-23",
            "info_hash": "c" * 40,
        },
    ]
    ranked, match = rank_western_magnets(
        items,
        "Brazzers Exxtra",
        "School of Cock",
        ["Ella Reese"],
        "2022-09-20",
    )
    assert match == "date"
    assert ranked[0]["info_hash"].startswith("b")
    assert ranked[0]["release_date"] == "2022-09-20"
    assert all(not item["info_hash"].startswith("c") for item in ranked)


def test_rank_western_prefers_title_overlap():
    items = [
        {"title": "Ella Reese interview", "heat": 900, "size": "1 GB", "info_hash": "a" * 40},
        {"title": "ZeroTolerance.Ella.Reese.Hot.Wife.Creampie.Scene.4", "heat": 20, "size": "2 GB", "info_hash": "b" * 40},
        {"title": "Huge pack Hot Wife Creampie 1-50", "heat": 9999, "size": "40 GB", "info_hash": "c" * 40},
        {"title": "Zero Tolerance.Some.Other.Scene", "heat": 9000, "size": "1 GB", "info_hash": "d" * 40},
    ]
    ranked, match = rank_western_magnets(
        items,
        "Zero Tolerance",
        "Hot Wife Creampie 6 - Scene 4",
        ["Ella Reese"],
    )
    assert match == "title"
    assert ranked[0]["info_hash"].startswith("b")
    assert any(item["info_hash"].startswith("d") for item in ranked)
    assert all(not item["info_hash"].startswith("a") for item in ranked)


def test_related_only_drops_unrelated_studio_magnets():
    items = [
        {
            "title": "EvilAngel.22.01.01.Other.Scene.XXX.1080p",
            "heat": 9000,
            "size": "4 GB",
            "info_hash": "a" * 40,
        },
        {
            "title": "EvilAngel.Rocco.Siffredi.Teens.Unleashed.Scene",
            "heat": 10,
            "size": "2 GB",
            "info_hash": "b" * 40,
        },
    ]
    ranked, match = rank_western_magnets(
        items,
        "Evil Angel",
        "Rocco's Teens Unleashed",
        ["Baby Doll"],
        "2026-10-29",
        related_only=True,
    )
    assert match == "title"
    assert [item["info_hash"] for item in ranked] == ["b" * 40]


def test_related_only_drops_old_dated_same_performer():
    items = [
        {
            "title": "Parasited.23.06.02.Ellie.Luna.And.Eve.Sweet.Bedtime.Possession.XXX.1080p",
            "heat": 90,
            "size": "2 GB",
            "info_hash": "a" * 40,
        },
        {
            "title": "[中文字幕]Parasited Eve Sweet The Camp Remaster 1080p",
            "heat": 10,
            "size": "2 GB",
            "info_hash": "b" * 40,
        },
    ]
    ranked, match = rank_western_magnets(
        items,
        "Parasited",
        "The Camp - Remaster",
        ["Alissa Foxy", "Eve Sweet"],
        "2026-09-18",
        related_only=True,
    )
    assert match == "title"
    assert [item["info_hash"] for item in ranked] == ["b" * 40]


def test_related_only_ignores_cast_overlap_on_compilations():
    items = [
        {
            "title": "Hope Heaven, Ashby Winter, Eve Sweet - Club Vixen Summit_2160p.mp4",
            "heat": 90,
            "size": "6 GB",
            "info_hash": "a" * 40,
        },
        {
            "title": "LetsDoeIt.LatinaMILF.Blondie.Fesser.Fuck.on.the.beach.2022.1080p",
            "heat": 80,
            "size": "2 GB",
            "info_hash": "b" * 40,
        },
    ]
    ranked, match = rank_western_magnets(
        items,
        "Vixen Compilations",
        "Vixen 10 Year Anniversary Vol 3",
        [
            "Ashby Winter", "Bella Spark", "Christy White", "Eva Lovia",
            "Eve Sweet", "Gabbie Carter", "Hope Heaven", "Blondie Fesser",
        ],
        "2026-09-20",
        related_only=True,
    )
    assert ranked == []
    assert match == "none"


def test_fallback_terms_use_performer_not_the_whole_studio():
    from app.western_magnets import western_fallback_terms

    terms = western_fallback_terms(
        "Taboo Heat",
        "BTS - Violet Voss And Amiee Cambridge Cheating Step Moms",
        ["Violet Voss", "Amiee Cambridge"],
    )
    assert terms[0] == "TabooHeat Violet Voss"
    assert "TabooHeat" not in terms


def test_vr_fallback_uses_glued_studio():
    from app.western_magnets import western_fallback_terms

    terms = western_fallback_terms("VR Bangers", "Office Fantasy", ["Jane Doe"])
    assert terms[0] == "VRBangers Jane Doe"
    assert "VRBangers JaneDoe" in terms
    assert "SLR VRBangers" in terms
    vrp = western_fallback_terms("Virtual Real Porn", "Between Pillows And Whispers", [])
    assert vrp[0] == "SLR VirtualRealPorn"
    assert "VirtualRealPorn Whispers" in vrp


def test_slr_fallback_uses_glued_names_and_prefix():
    from app.western_magnets import western_fallback_terms

    sexbabes = western_fallback_terms("SexBabesVR", "Romantic Evening", ["Nancy A"])
    assert "SexBabesVR Nancy" in sexbabes
    assert "SexBabesVR NancyA" in sexbabes
    assert "SLR SexBabesVR" in sexbabes
    lust = western_fallback_terms("LustReality", "Wake Up Sex", ["Jenny Doll"])
    assert "LustReality Jenny Doll" in lust
    assert "LustReality JennyDoll" in lust
    hostel = western_fallback_terms("Horny Hostel", "Room Service", ["Jane Doe"])
    assert "HornyHostel JaneDoe" in hostel
    assert "SLR HornyHostel" in hostel
    badoink = western_fallback_terms("Badoink VR", "Office Fantasy", ["Jane Doe"])
    assert "BadoinkVR JaneDoe" in badoink
    assert "SLR BadoinkVR" in badoink
    erotique = western_fallback_terms("Erotique Sex", "Night Walk", ["Jane Doe"])
    assert "ErotiqueSex JaneDoe" in erotique
    assert "SLR ErotiqueSex" in erotique
    tagged = western_fallback_terms("Unknown Indie", "Night Walk", ["Jane Doe"], vr=True)
    assert "SLR UnknownIndie" in tagged
    assert "UnknownIndie JaneDoe" in tagged


def test_fallback_prefers_performers_named_in_the_title():
    from app.western_magnets import western_fallback_terms

    terms = western_fallback_terms(
        "Blacked",
        "Flawless Besties Eve And Kelly Ride BBC",
        ["Jax Slayher", "Eve Sweet", "Kelly Collins"],
    )
    assert terms[0] == "Blacked Eve Sweet"
    assert "Blacked EveSweet" in terms
    assert "Blacked Kelly Collins" in terms
    compilations = western_fallback_terms(
        "Vixen Compilations",
        "Vixen 10 Year Anniversary Vol 3",
        ["Ashby Winter", "Eve Sweet"],
    )
    assert "Vixen Ashby Winter" in compilations
    assert "VixenCompilations Ashby Winter" in compilations


def test_rank_slr_glued_scene_names():
    cases = [
        (
            "SexBabesVR",
            "Romantic Evening",
            ["Nancy A"],
            "SLR_SexBabesVR_NancyA_RomanticEvening_1920p_180_LR_180x180_3dh",
            "SLR_SexBabesVR_OtherGirl_DifferentScene_8K",
        ),
        (
            "LustReality",
            "Wake Up Sex",
            ["Jenny Doll"],
            "SLR_LustReality_JennyDoll_WakeUpSex_2900p_180_LR",
            "SLR_LustReality_OtherGirl_OtherTitle_8K",
        ),
        (
            "perVRt",
            "Horny Hostel Room Service",
            ["Jane Doe"],
            "SLR_perVRt_HornyHostel_JaneDoe_RoomService_8K",
            "SLR_perVRt_OtherSeries_SomeoneElse_8K",
        ),
        (
            "Horny Hostel",
            "Room Service",
            ["Jane Doe"],
            "SLR_perVRt_HornyHostel_JaneDoe_RoomService_8K",
            "SLR_perVRt_OtherSeries_SomeoneElse_8K",
        ),
        (
            "Badoink VR",
            "Office Fantasy",
            ["Jane Doe"],
            "SLR_BadoinkVR_JaneDoe_OfficeFantasy_8K_180_LR",
            "SLR_BadoinkVR_OtherGirl_OtherTitle_8K",
        ),
        (
            "Erotique Sex",
            "Night Walk",
            ["Jane Doe"],
            "SLR_ErotiqueSex_JaneDoe_NightWalk_8K_180_LR",
            "SLR_ErotiqueSex_OtherGirl_OtherTitle_8K",
        ),
    ]
    for site, title, performers, keep, drop in cases:
        ranked, match = rank_western_magnets(
            [
                {"title": keep, "heat": 20, "size": "8 GB", "info_hash": "a" * 40},
                {"title": drop, "heat": 90, "size": "10 GB", "info_hash": "b" * 40},
            ],
            site,
            title,
            performers,
            "2024-03-15",
            related_only=True,
        )
        assert match == "title", site
        assert [item["info_hash"] for item in ranked] == ["a" * 40], site


def test_rank_glued_vr_studio_and_initials():
    items = [
        {
            "title": "VRBangers.24.05.06.Jane.Doe.Office.Fantasy.XXX.VR180.8K",
            "heat": 10,
            "size": "40 GB",
            "info_hash": "a" * 40,
        },
        {
            "title": "Bangers.24.05.06.Wrong.Studio",
            "heat": 90,
            "size": "2 GB",
            "info_hash": "b" * 40,
        },
    ]
    ranked, match = rank_western_magnets(
        items,
        "VR Bangers",
        "Office Fantasy",
        ["Jane Doe"],
        "2024-05-06",
    )
    assert match == "date"
    assert [item["info_hash"] for item in ranked] == ["a" * 40]
    assert ranked[0]["pack"] is False


def test_rank_vr_hush_p2p_name():
    items = [
        {
            "title": "VRHush.19.12.16.From.the.Vault.Dani.Daniels.XXX.VR180.2700p.MP4-VACCiNE",
            "heat": 40,
            "size": "4.2 GB",
            "info_hash": "a" * 40,
        },
        {
            "title": "HushVR.19.12.16.Dani.Daniels.From.the.Vault.8K",
            "heat": 10,
            "size": "8 GB",
            "info_hash": "b" * 40,
        },
        {
            "title": "Hush.19.12.16.Unrelated.Scene",
            "heat": 90,
            "size": "2 GB",
            "info_hash": "c" * 40,
        },
    ]
    ranked, match = rank_western_magnets(
        items,
        "VR Hush",
        "From the Vault",
        ["Dani Daniels"],
        "2019-12-16",
    )
    assert match == "date"
    hashes = [item["info_hash"] for item in ranked]
    assert hashes == ["a" * 40, "b" * 40]


def test_rank_virtual_real_porn_scene():
    items = [
        {
            "title": "VirtualRealPorn.26.06.07.Between.Pillows.And.Whispers.XXX.VR180.8K",
            "heat": 20,
            "size": "35 GB",
            "info_hash": "a" * 40,
        },
        {
            "title": "VRP.26.06.07.Between.Pillows.And.Whispers.8K",
            "heat": 15,
            "size": "30 GB",
            "info_hash": "b" * 40,
        },
        {
            "title": "Porn.26.06.07.Unrelated",
            "heat": 99,
            "size": "2 GB",
            "info_hash": "c" * 40,
        },
    ]
    ranked, match = rank_western_magnets(
        items,
        "Virtual Real Porn",
        "Between Pillows And Whispers",
        [],
        "2026-06-07",
    )
    assert match == "date"
    hashes = [item["info_hash"] for item in ranked]
    assert hashes == ["a" * 40, "b" * 40]
    assert all(not item["pack"] for item in ranked)


def test_site_name_alone_does_not_count_as_the_scene():
    items = [{
        "title": "County Line Rocco Siffredi",
        "heat": 100,
        "size": "1 GB",
        "info_hash": "a" * 40,
    }]
    ranked, match = rank_western_magnets(
        items,
        "Rocco Siffredi",
        "Rocco And Kelly's Prague Adventure",
        ["Andrew A"],
    )
    assert [item["info_hash"] for item in ranked] == ["a" * 40]
    assert match == "site"


def test_western_archive_uses_existing_studio_folder(tmp_path):
    from app.nfo import build_nfo
    from app.western_archive import find_western_videos, studio_dir

    root = tmp_path / "欧美"
    (root / "EvilAngel").mkdir(parents=True)
    assert studio_dir(root, "Evil Angel") == root / "EvilAngel"
    assert studio_dir(root, "New Site").name == "NewSite"

    download = tmp_path / "downloads"
    torrent = download / "Roccos.Teens.Unleashed.6"
    torrent.mkdir(parents=True)
    video = torrent / "scene.mp4"
    video.write_bytes(b"x" * 80)
    src, videos = find_western_videos(
        download,
        download / "western" / "slug",
        "Roccos.Teens.Unleashed.6.XXX",
        min_bytes=50,
    )
    assert src == torrent
    assert videos == [video]

    xml = build_nfo({
        "title": "Rocco's Teens Unleashed",
        "release_date": "2025-08-16",
        "studio": "Evil Angel",
        "actors": ["Baby Doll"],
        "genres": ["Feature"],
        "plot": "hello",
        "uniqueid": "abc",
        "uniqueid_type": "tpdb",
    })
    parsed = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(xml)
    assert parsed.findtext("studio") == "Evil Angel"
    assert parsed.find("uniqueid").attrib["type"] == "tpdb"
    assert parsed.find("uniqueid").text == "abc"
    assert parsed.findtext("plot") == "hello"


def test_scrape_western_moves_file_into_studio(tmp_path):
    import asyncio

    from app.config import Settings
    from app.western_archive import scrape_western_job

    async def run():
        settings = Settings(
            data_dir=tmp_path / "data",
            download_dir=tmp_path / "dl",
            media_dir=tmp_path / "media",
            western_media_dir=str(tmp_path / "欧美"),
            scrape_min_mb=0,
        )
        settings.ensure_dirs()
        (settings.western_root / "Brazzers").mkdir(parents=True)
        torrent = settings.download_dir / "brazzers.scene.title"
        torrent.mkdir()
        (torrent / "clip.mp4").write_bytes(b"x" * 80)
        result = await scrape_western_job(
            settings,
            {"dest": str(settings.download_dir / "western" / "slug"), "title": "brazzers.scene.title.xxx"},
            {"kind": "western", "site": "Brazzers", "title": "Scene Title", "date": "2024-01-02", "performers": ["Ann Example"]},
        )
        folder = settings.western_root / "Brazzers"
        assert Path(result["path"]) == folder
        assert list(folder.glob("*.mp4"))
        assert list(folder.glob("*.nfo"))
        assert not (torrent / "clip.mp4").exists()

    asyncio.run(run())


def test_scrape_vr_moves_file_into_vr_root(tmp_path):
    import asyncio

    from app.config import Settings
    from app.western_archive import scrape_western_job

    async def run():
        settings = Settings(
            data_dir=tmp_path / "data",
            download_dir=tmp_path / "dl",
            media_dir=tmp_path / "media",
            western_media_dir=str(tmp_path / "欧美"),
            vr_media_dir=str(tmp_path / "vrporn"),
            scrape_min_mb=0,
        )
        settings.ensure_dirs()
        settings.vr_root.mkdir(parents=True)
        torrent = settings.download_dir / "vrbangers.scene.title"
        torrent.mkdir()
        (torrent / "clip.mp4").write_bytes(b"x" * 80)
        result = await scrape_western_job(
            settings,
            {"dest": str(settings.download_dir / "western" / "slug"), "title": "vrbangers.scene.title.xxx"},
            {"kind": "western", "site": "VR Bangers", "title": "Office Fantasy", "date": "2024-05-06", "performers": []},
        )
        folder = settings.vr_root / "VRBangers"
        assert Path(result["path"]) == folder
        assert result["entries"][0]["shelf"] == "vr"
        assert result["entries"][0]["path"].startswith("vr/")
        assert list(folder.glob("*.mp4"))
        west = settings.western_root
        assert west is None or not west.exists() or not list(west.rglob("*.mp4"))

    asyncio.run(run())


def test_fill_western_video_writes_sidecars(tmp_path, monkeypatch):
    import asyncio

    from app.config import Settings
    from app.western_archive import fill_western_video

    async def fake_filename(settings, name):
        return {
            "id": "vr1",
            "kind": "scene",
            "site": "VR Bangers",
            "title": "Office Fantasy",
            "date": "2024-05-06",
            "performers": ["Ann"],
            "cover": "https://cdn.theporndb.net/p.jpg",
        }

    async def fake_meta(settings, info):
        return {
            "title": "Office Fantasy",
            "release_date": "2024-05-06",
            "studio": "VR Bangers",
            "actors": ["Ann"],
            "cover": "https://cdn.theporndb.net/p.jpg",
            "uniqueid": "vr1",
            "uniqueid_type": "tpdb",
            "genres": ["Virtual Reality"],
            "plot": "",
            "runtime": "40",
            "url": "",
            "code": "",
        }

    async def fake_cover(settings, url, referer=None):
        return b"vr-poster"

    monkeypatch.setattr("app.western_archive.fetch_by_filename", fake_filename)
    monkeypatch.setattr("app.western_archive.western_metadata", fake_meta)
    monkeypatch.setattr("app.western_archive.fetch_cover_bytes", fake_cover)
    video = tmp_path / "VRBangers" / "headset.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x" * 80)
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "dl",
        media_dir=tmp_path / "media",
        vr_media_dir=str(tmp_path),
        scrape_min_mb=0,
        tpdb_api_key="token",
    )
    settings.ensure_dirs()

    async def run():
        assert await fill_western_video(settings, video) is True
        assert (video.parent / "headset.nfo").is_file()
        assert (video.parent / "headset-poster.jpg").read_bytes() == b"vr-poster"

    asyncio.run(run())


def test_western_slug_dir_is_removed_after_a_loose_file_is_archived(tmp_path):
    import asyncio

    from app.config import Settings
    from app.western_archive import find_western_videos, scrape_western_job

    download = tmp_path / "dl"
    video = download / "brazzers.scene.title.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x" * 80)
    slug = download / "western" / "slug"
    slug.mkdir(parents=True)
    (slug / ".javdl.json").write_text("{}\n", encoding="utf-8")
    src, videos = find_western_videos(download, slug, "brazzers.scene.title.xxx", min_bytes=50)
    assert src == video
    assert videos == [video]

    async def run():
        settings = Settings(
            data_dir=tmp_path / "data",
            download_dir=download,
            media_dir=tmp_path / "media",
            western_media_dir=str(tmp_path / "欧美"),
            scrape_min_mb=0,
        )
        settings.ensure_dirs()
        (settings.western_root / "Brazzers").mkdir(parents=True)
        await scrape_western_job(
            settings,
            {"dest": str(slug), "title": "brazzers.scene.title.xxx"},
            {"kind": "western", "site": "Brazzers", "title": "Scene Title", "date": "2024-01-02", "performers": []},
        )

    asyncio.run(run())
    assert not slug.exists()
    assert (download / "western").is_dir()
    assert not video.exists()
    assert list((tmp_path / "欧美" / "Brazzers").glob("*.mp4"))


def _western_settings(tmp_path: Path, download: Path) -> Settings:
    settings = Settings(
        data_dir=tmp_path / "data",
        download_dir=download,
        media_dir=tmp_path / "media",
        western_media_dir=str(tmp_path / "欧美"),
        scrape_min_mb=0,
    )
    settings.ensure_dirs()
    return settings


def test_same_size_release_is_not_copied_again(tmp_path):
    from app.western_archive import scrape_western_job

    name = "sexart.26.09.13.anabel.busty.look.after.me.mp4"
    download = tmp_path / "dl"
    download.mkdir()
    settings = _western_settings(tmp_path, download)
    folder = settings.western_root / "SexArt"
    folder.mkdir(parents=True)
    payload = b"x" * 80
    kept = folder / name
    kept.write_bytes(payload)
    incoming = download / name
    incoming.write_bytes(payload)

    async def run():
        return await scrape_western_job(
            settings,
            {"dest": str(download / "western" / "slug"), "title": name},
            {
                "kind": "western",
                "site": "SexArt",
                "title": "Look After Me",
                "date": "2026-09-13",
                "performers": [],
            },
        )

    result = asyncio.run(run())
    assert result["duplicate"] is True
    assert result["videos"] == []
    assert not incoming.exists()
    assert [path.name for path in folder.glob("*.mp4")] == [name]
    assert kept.read_bytes() == payload


def test_different_size_release_keeps_a_second_file(tmp_path):
    from app.western_archive import scrape_western_job

    name = "sexart.26.09.13.anabel.busty.look.after.me.mp4"
    download = tmp_path / "dl"
    download.mkdir()
    settings = _western_settings(tmp_path, download)
    folder = settings.western_root / "SexArt"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(b"x" * 80)
    (download / name).write_bytes(b"y" * 120)

    async def run():
        await scrape_western_job(
            settings,
            {"dest": str(download), "title": name},
            {
                "kind": "western",
                "site": "SexArt",
                "title": "Look After Me",
                "date": "2026-09-13",
                "performers": [],
            },
        )

    asyncio.run(run())
    assert sorted(path.name for path in folder.glob("*.mp4")) == [
        "sexart.26.09.13.anabel.busty.look.after.me-2.mp4",
        name,
    ]


def test_western_slug_is_not_a_code():
    slug = western_slug("AB", "", "123", "id")
    assert normalize_code(slug) is None
    named = western_slug("Brazzers", "2024-01-02", "Late Night")
    assert named.startswith("brazzers-2024-01-02")
    assert normalize_code(named) is None


def test_archive_stem_drops_watermark_prefix():
    from app.western_archive import _archive_stem

    ugly = "489155.com@[中文字幕]JulesJordan.26.08.18.Octavia.Red.4K-C.mp4"
    assert _archive_stem(ugly) == "JulesJordan.26.08.18.Octavia.Red.4K-C"
    plain = "sexart.26.08.28.lula.stocch.and.anabel.busty.hot.view.mp4"
    assert _archive_stem(plain).lower().startswith("sexart.26.08.28")


def test_prefixed_release_name_is_western():
    assert is_western_release_name("[中文字幕]JulesJordan.26.08.18.Octavia.Red.4K-C")
    assert is_western_release_name("489155.com@[中文字幕]JulesJordan.26.08.18.Octavia.Red.4K-C.mp4")
    assert not is_western_release_name("[中文字幕]SSIS-001.mp4")
    assert not is_western_release_name("SSIS-001.mp4")


def test_prefixed_folder_is_ready(tmp_path):
    import os

    folder = tmp_path / "[中文字幕]JulesJordan.26.08.18.Octavia.Red.4K-C"
    folder.mkdir()
    video = folder / "489155.com@[中文字幕]JulesJordan.26.08.18.Octavia.Red.4K-C.mp4"
    video.write_bytes(b"x" * 80)
    old = time.time() - 1000
    os.utime(folder, (old, old))
    os.utime(video, (old, old))
    ready = list_ready_western(tmp_path, min_bytes=50, settle=0, now=time.time())
    assert ready == [folder]


def test_list_hides_short_scenes_and_accepts_known_themes():
    from app.sources.tpdb import THEMES, TpdbError, is_too_short, list_params

    params = list_params(1, None, "anal")
    assert params["duration"] == 15 * 60
    assert params["duration_operation"] == ">="
    assert params["orderBy"] == "recently_released"
    assert params["tags[70]"] == "Anal"
    assert "q" not in params
    search = list_params(2, "blake", None)
    assert search["q"] == "blake"
    assert search["page"] == 2
    vr = list_params(1, None, None, "vr")
    assert vr["tags[503]"] == "Virtual Reality"
    assert "tags[70]" not in vr
    assert is_too_short("")
    assert not is_too_short("", allow_unknown=True)
    assert is_too_short("14")
    assert is_too_short("14", allow_unknown=True)
    assert not is_too_short("15")
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    for slug, (_tag_id, _tag_name, label) in THEMES.items():
        assert f'data-theme="{slug}"' in html
        assert f">{label}<" in html
    try:
        list_params(1, None, "nope")
    except TpdbError as exc:
        assert "题材" in str(exc)
    else:
        raise AssertionError("unknown theme should fail")


def test_is_vr_studio_and_work():
    from app.studios import is_vr_studio, is_vr_work

    assert is_vr_studio("VR Bangers")
    assert is_vr_studio("Virtual Real Porn")
    assert is_vr_studio("SexLikeReal")
    assert is_vr_studio("SLR Originals")
    assert is_vr_studio("WankzVR")
    assert is_vr_studio("Naughty America VR")
    assert is_vr_studio("SexBabesVR")
    assert is_vr_studio("LustReality")
    assert is_vr_studio("perVRt")
    assert is_vr_studio("Horny Hostel")
    assert is_vr_studio("Badoink VR")
    assert is_vr_studio("Erotique Sex")
    assert is_vr_studio("RealityLovers")
    assert is_vr_studio("SwallowBay")
    assert is_vr_studio("VirtualTaboo")
    assert is_vr_studio("Dezyred")
    assert is_vr_studio("EmilyBloom")
    assert not is_vr_studio("Naughty America")
    assert not is_vr_studio("Brazzers")
    assert is_vr_work("Brazzers", ["Virtual Reality"], "Room") is True
    assert is_vr_work("Brazzers", ["Anal"], "Room") is False


def test_pick_site_virtual_real_prefers_the_porn_studio():
    from app.sources.tpdb import pick_site

    rows = [
        {"id": "1", "name": "VirtualRealAmateur"},
        {"id": "2", "name": "Virtual Real Porn"},
        {"id": "3", "name": "VirtualRealGay"},
        {"id": "4", "name": "Virtual Taboo"},
        {"id": "5", "name": "Vixen"},
    ]
    picked = pick_site(rows, "Virtual Real")
    assert picked["name"] == "Virtual Real Porn"
    assert pick_site(rows, "Riley") is None
    assert pick_site(rows, "Vixen")["id"] == "5"
    assert pick_site(rows, "VirtualRealPorn")["name"] == "Virtual Real Porn"
    bex = pick_site(
        [{"id": "9", "name": "Brazzers Exxtra", "aliases": ["BEX", "BrazzersExxtra"]}],
        "BrazzersExxtra",
    )
    assert bex["id"] == "9"
    naughty = pick_site(
        [
            {"id": "n", "name": "Naughty America"},
            {"id": "nvr", "name": "Naughty America VR"},
        ],
        "Naughty America",
    )
    assert naughty["id"] == "n"


def test_unpack_list_folds_same_scene():
    from app.sources.tpdb import _unpack_list

    payload = {
        "data": [
            {
                "id": "a",
                "title": "Room",
                "date": "2024-01-02",
                "site": {"name": "Virtual Real Porn"},
                "duration": 1200,
            },
            {
                "id": "a",
                "title": "Room",
                "date": "2024-01-02",
                "site": {"name": "Virtual Real Porn"},
                "duration": 1200,
            },
            {
                "id": "b",
                "title": "Room",
                "date": "2024-01-02",
                "site": {"name": "VirtualRealPorn"},
                "duration": 1800,
            },
            {
                "id": "c",
                "title": "Other",
                "date": "2024-01-02",
                "site": {"name": "Virtual Real Porn"},
                "duration": 1800,
            },
        ],
        "meta": {"last_page": 1},
    }
    items = _unpack_list(payload, "scene", 1)["items"]
    assert [item["id"] for item in items] == ["b", "c"]
    assert items[0]["duration"] == "30"


def test_search_catalog_opens_the_matched_site(monkeypatch):
    import asyncio

    from app.config import Settings
    from app.sources import tpdb

    async def fake_match(settings, query):
        assert query == "Virtual Real"
        return {"id": "site-1", "name": "Virtual Real Porn"}

    async def fake_facet(settings, kind, facet, name, page=1, tag_id=""):
        assert kind == "scene"
        assert facet == "site"
        assert name == "Virtual Real Porn"
        return {"items": [{"id": "1", "title": "Room"}], "page": page, "last_page": 3}

    monkeypatch.setattr(tpdb, "match_site", fake_match)
    monkeypatch.setattr(tpdb, "fetch_facet", fake_facet)

    async def run():
        out = await tpdb.search_catalog(Settings(), "scene", "Virtual Real", 1)
        assert out["matched_site"] == "Virtual Real Porn"
        assert out["items"][0]["id"] == "1"
        assert out["last_page"] == 3

    asyncio.run(run())


def test_search_catalog_opens_the_matched_performer(monkeypatch):
    import asyncio

    from app.config import Settings
    from app.sources import tpdb

    async def fake_site(settings, query):
        return None

    async def fake_person(settings, query):
        assert query == "Eve Sweet"
        return {"id": "perf-1", "name": "Eve Sweet"}

    async def fake_facet(settings, kind, facet, name, page=1, tag_id=""):
        assert kind == "scene"
        assert facet == "performer"
        assert name == "Eve Sweet"
        return {"items": [{"id": "1", "title": "Wedding Guest"}], "page": page, "last_page": 10}

    monkeypatch.setattr(tpdb, "match_site", fake_site)
    monkeypatch.setattr(tpdb, "match_performer", fake_person)
    monkeypatch.setattr(tpdb, "fetch_facet", fake_facet)

    async def run():
        out = await tpdb.search_catalog(Settings(), "scene", "Eve Sweet", 1)
        assert out["matched_performer"] == "Eve Sweet"
        assert out["items"][0]["id"] == "1"
        assert out["last_page"] == 10

    asyncio.run(run())


def test_choose_match_uses_performer_and_release_date():
    from app.sources.tpdb import choose_match

    rows = [
        {
            "id": "1",
            "title": "Other",
            "date": "2026-09-01",
            "site": {"name": "Sweet Sinner"},
            "performers": [{"name": "Someone Else"}],
            "duration": 1800,
        },
        {
            "id": "2",
            "title": "The Wet Spot",
            "date": "2026-09-01",
            "site": {"name": "Sweet Sinner"},
            "performers": [{"name": "Blake Blossom"}],
            "duration": 1754,
        },
    ]
    picked = choose_match(rows, "scene", "SweetSinner.26.09.01.Blake.Blossom.Nasty.At.Night")
    assert picked["id"] == "2"
    old = [{"id": "9", "title": "Old", "date": "2020-09-01", "site": {"name": "Sweet Sinner"}, "duration": 1800}]
    assert choose_match(old, "scene", "SweetSinner.26.09.01.Blake.Blossom") is None


def test_filename_queries_drop_group_and_quality():
    from app.sources.tpdb import filename_queries

    anal = filename_queries("AnalOverdose.17.04.20.Riley.Nixon.XXX.1080p.MP4-KTR[N1C]")
    assert "AnalOverdose.17.04.20.Riley.Nixon.XXX.1080p.MP4-KTR" in anal
    producers = filename_queries("ProducersFun 22.03.23 Blake Blossom XXX 1080p MP4 [SpankHash]")
    assert "ProducersFun.22.03.23.Blake.Blossom" in producers
    sex = filename_queries("SexArt.26.08.28.Lula.Stocch.And.Anabel.Busty.Hot.View.XXX.1080p.MP4-TRB")
    assert "SexArt.26.08.28.Lula.Stocch" in sex
    sweet = filename_queries("SweetSinner.26.09.01.Blake.Blossom.Nasty.At.Night.XXX.720p.MP4-XXX[XC]")
    assert "SweetSinner.26.09.01.Blake.Blossom" in sweet
    jules = filename_queries("489155.com@[中文字幕]JulesJordan.26.08.18.Octavia.Red.4K-C.mp4")
    assert "JulesJordan.26.08.18.Octavia.Red" in jules


def test_western_release_name_is_not_a_jav_code():
    assert is_western_release_name("AnalOverdose.17.04.20.Riley.Nixon.XXX.1080p.MP4-KTR[N1C]")
    assert is_western_release_name("analoverdose.17.04.20.riley.nixon[N1C].mp4")
    assert is_western_release_name("ProducersFun 22.03.23 Blake Blossom XXX 1080p MP4 [SpankHash]")
    assert not is_western_release_name("SSIS-001.mp4")
    assert not is_western_release_name("SSIS-001")


def test_xunlei_western_folder_is_watched_as_western(tmp_path):
    import os

    folder = tmp_path / "AnalOverdose.17.04.20.Riley.Nixon.XXX.1080p.MP4-KTR[N1C]"
    folder.mkdir()
    video = folder / "analoverdose.17.04.20.riley.nixon[N1C].mp4"
    video.write_bytes(b"x" * 80)
    old = time.time() - 1000
    os.utime(folder, (old, old))
    os.utime(video, (old, old))
    jav = tmp_path / "SSIS-001.mp4"
    jav.write_bytes(b"x" * 80)
    os.utime(jav, (old, old))
    now = time.time()
    assert list_ready_sources(tmp_path, min_bytes=50, settle=0, now=now) == [("SSIS-001", jav)]
    assert list_ready_western(tmp_path, min_bytes=50, settle=0, now=now) == [folder]


def test_western_dir_is_not_a_watch_target(tmp_path):
    western = tmp_path / "western"
    western.mkdir()
    video = western / "AB-123.mp4"
    video.write_bytes(b"x" * 80)
    old = time.time() - 1000
    video.touch()
    import os
    os.utime(video, (old, old))
    normal = tmp_path / "SSIS-001.mp4"
    normal.write_bytes(b"x" * 80)
    os.utime(normal, (old, old))
    ready = list_ready_sources(tmp_path, min_bytes=50, settle=0, now=time.time())
    codes = [code for code, _path in ready]
    assert codes == ["SSIS-001"]


def test_non_code_job_skips_scrape(tmp_path):
    async def run():
        settings = Settings(
            data_dir=tmp_path / "data",
            download_dir=tmp_path / "dl",
            media_dir=tmp_path / "media",
            scrape_enabled=True,
        )
        settings.ensure_dirs()
        db = Database(settings)
        await db.init()
        dest = settings.download_dir / "western" / "brazzers-room"
        dest.mkdir(parents=True)
        (dest / "movie.mp4").write_bytes(b"x" * 80)
        now = time.time()
        job = {
            "id": "job1",
            "code": "brazzers-room",
            "info_hash": "a" * 40,
            "title": "magnet name",
            "magnet": "magnet:?xt=urn:btih:" + "a" * 40,
            "gid": "",
            "status": "complete",
            "dest": str(dest),
            "error": None,
            "created_at": now,
            "updated_at": now,
            "cleaned": 0,
        }
        await db.insert_job(job)
        jobs = JobManager(settings, db, Aria2(settings))
        out = await jobs.maybe_scrape(job)
        assert out["scrape_status"] == "skipped"
        stored = await db.get_job("job1")
        assert stored["scrape_status"] == "skipped"

    asyncio.run(run())


def test_tpdb_image_host_allowed():
    assert _allowed("https://cdn.theporndb.net/p.jpg", "https://www.javbus.com")
    assert _allowed("https://api.metadataapi.net/x.jpg", "https://www.javbus.com")
    assert not _allowed("https://evil.example/p.jpg", "https://www.javbus.com")
    assert not _allowed("https://nottheporndb.net.evil.com/p.jpg", "https://www.javbus.com")
