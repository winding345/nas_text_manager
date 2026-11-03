"""接口冒烟测试：扫描、列表、搜索、进度、删除。"""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    books = tmp_path / "books"
    books.mkdir()
    (books / "三国演义.txt").write_text("第一回 宴桃园豪杰三结义\n曹操 刘备 孙权\n", encoding="utf-8")
    (books / "红楼梦.txt").write_text("甄士隐梦幻识通灵\n贾宝玉 林黛玉\n", encoding="utf-8")
    (books / "somebook.epub").write_bytes(b"PK\x03\x04")

    monkeypatch.setenv("BOOKS_DIRS", str(books))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "lib.db"))
    monkeypatch.setenv("ALLOW_DELETE", "1")

    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]

    from app.main import app  # noqa: PLC0415
    with TestClient(app) as c:
        r = c.post("/api/scan")
        assert r.status_code == 200
        assert r.json()["total"] == 3
        yield c


def test_list_and_multiterm_search(client):
    assert len(client.get("/api/books").json()["books"]) == 3
    hits = client.get("/api/books", params={"q": "三国 演义"}).json()["books"]
    assert [b["name"] for b in hits] == ["三国演义"]
    assert client.get("/api/books", params={"q": "三国 不存在"}).json()["books"] == []


def test_read_text_and_progress(client):
    book = client.get("/api/books", params={"q": "三国"}).json()["books"][0]
    data = client.get(f"/api/books/{book['id']}/text").json()
    assert "曹操" in data["text"]

    client.put(f"/api/books/{book['id']}/progress", json={"char_offset": 12})
    assert client.get(f"/api/books/{book['id']}").json()["progress"] == 12


def test_search_inside_book(client):
    book = client.get("/api/books", params={"q": "三国"}).json()["books"][0]
    r = client.get(f"/api/books/{book['id']}/search", params={"q": "曹操"}).json()
    assert r["hits"] and "曹操" in r["hits"][0]["text"]


def test_search_all_books(client):
    r = client.get("/api/search", params={"q": "曹操"}).json()
    assert r["results"], "应命中"
    r2 = client.get("/api/search", params={"q": "曹操 刘备"}).json()
    assert len(r2["results"]) == 1
    assert r2["results"][0]["book"]["name"] == "三国演义"


def test_delete_removes_file(client, tmp_path):
    book = client.get("/api/books", params={"q": "红楼"}).json()["books"][0]
    path = tmp_path / "books" / "红楼梦.txt"
    assert path.exists()
    r = client.delete(f"/api/books/{book['id']}").json()
    assert r["deleted_file"] is True
    assert not path.exists()
    assert client.get(f"/api/books/{book['id']}").status_code == 404


# ---------- 回归缺口补齐（对应 docs/DEV-KNOWLEDGE.md §3/§4.3/§4.10） ----------

def test_non_txt_cannot_be_opened(client):
    """非 txt 会入库（§4.10），但 /text 必须 400，而不是崩或返回乱码。"""
    epub = next(b for b in client.get("/api/books").json()["books"] if b["ext"] == "epub")
    r = client.get(f"/api/books/{epub['id']}/text")
    assert r.status_code == 400
    assert "txt" in r.json()["detail"]


def test_delete_file_failure_keeps_db(client, monkeypatch, tmp_path):
    """删除顺序：文件删失败时返回 500，且数据库记录保留（不产生孤儿）。"""
    import app.main as main_module

    book = client.get("/api/books", params={"q": "红楼"}).json()["books"][0]
    path = tmp_path / "books" / "红楼梦.txt"

    def boom(_path):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(main_module.os, "remove", boom)
    r = client.delete(f"/api/books/{book['id']}")
    assert r.status_code == 500
    assert "数据库记录已保留" in r.json()["detail"]
    # DB 还在、文件也在
    assert client.get(f"/api/books/{book['id']}").status_code == 200
    assert path.exists()


def test_delete_missing_file_still_removes_record(client, tmp_path):
    """文件已经不在时，删除应当成功（幂等）。"""
    import os as _os
    book = client.get("/api/books", params={"q": "红楼"}).json()["books"][0]
    _os.remove(tmp_path / "books" / "红楼梦.txt")
    r = client.delete(f"/api/books/{book['id']}")
    assert r.status_code == 200
    assert r.json()["deleted_db"] is True
    assert client.get(f"/api/books/{book['id']}").status_code == 404


def test_search_cache_invalidated_after_delete(client):
    """删书后立刻再搜，不能返回已删的书（旧缺陷）。"""
    first = client.get("/api/search", params={"q": "曹操"}).json()
    assert first["results"], "第一次应命中"
    book = first["results"][0]["book"]
    assert client.delete(f"/api/books/{book['id']}").status_code == 200
    again = client.get("/api/search", params={"q": "曹操"}).json()
    names = [r["book"]["name"] for r in again["results"]]
    assert book["name"] not in names, "已删的书不应再出现在搜索结果里"
