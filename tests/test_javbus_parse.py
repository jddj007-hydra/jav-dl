from pathlib import Path

from app.sources.javbus import parse_javbus

HTML = (Path(__file__).parent / "fixtures" / "javbus_ssis001.html").read_text(
    encoding="utf-8"
)


def test_parse_ssis001():
    meta = parse_javbus(HTML, "https://www.javbus.com", "SSIS-001")
    assert meta["code"] == "SSIS-001"
    assert "禁欲" in meta["title"] or "禁慾" in meta["title"]
    names = [a["name"] if isinstance(a, dict) else a for a in meta["actors"]]
    assert "葵つかさ" in names
    assert meta["actors"][0]["photo"]
    assert meta["release_date"] == "2021-02-18"
    assert meta["cover"]
    assert "javbus.com" in meta["cover"]
    assert meta["studio"]
    assert meta["label"] == "S1 NO.1 STYLE"
    assert len(meta["samples"]) >= 6
    assert meta["samples"][0]["thumb"]
    assert "多P" in meta["genres"]


def test_parse_search_boxes():
    from app.sources.javbus import parse_search
    html = """
    <html><body>
    <a class="movie-box" href="https://www.javbus.com/SSIS-001_2021-02-18">
      <div class="photo-frame"><img src="/pics/thumb/x.jpg" title="SSIS-001 禁欲生活"></div>
      <div class="photo-info"><span>SSIS-001 禁欲生活<br>
        <date>SSIS-001</date> / <date>2021-02-18</date>
      </span></div>
    </a>
    <a class="movie-box" href="/MTNDV-1396_2022-04-22">
      <div class="photo-frame"><img src="/pics/thumb/y.jpg" title="葵つかさ 测试"></div>
      <div class="photo-info"><span>葵つかさ 测试<br>
        <date>MTNDV-1396</date> / <date>2022-04-22</date>
      </span></div>
    </a>
    </body></html>
    """
    items = parse_search(html, "https://www.javbus.com")
    assert items[0]["code"] == "SSIS-001"
    assert items[0]["release_date"] == "2021-02-18"
    assert "禁欲" in items[0]["title"]
    assert items[0]["cover"].endswith("/pics/thumb/x.jpg")
    assert items[1]["code"] == "MTNDV-1396"


def test_parse_series_field():
    html = """
    <html><body>
    <h3>IPX-001 测试标题</h3>
    <a class="bigImage" href="/pics/cover/x.jpg"><img src="/pics/cover/x.jpg"></a>
    <div class="col-md-3 info">
      <p><span class="header">製作商:</span> <a href="/studio/1">Studio A</a></p>
      <p><span class="header">系列:</span> <a href="/series/9">某系列</a></p>
    </div>
    </body></html>
    """
    meta = parse_javbus(html, "https://www.javbus.com", "IPX-001")
    assert meta["series"] == "某系列"
    assert meta["studio"] == "Studio A"
