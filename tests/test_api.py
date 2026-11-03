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


def test_delete_file_failure_leaves_orphan(client, monkeypatch, tmp_path):
    """删除非原子（§3）：文件删失败时仍返回 ok:true，DB 记录已消失，磁盘文件成孤儿。"""
    import app.main as main_module

    book = client.get("/api/books", params={"q": "红楼"}).json()["books"][0]
    path = tmp_path / "books" / "红楼梦.txt"

    def boom(_path):
        raise OSError("simulated permission denied")

    monkeypatch.setattr(main_module.os, "remove", boom)
    r = client.delete(f"/api/books/{book['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted_db"] is True
    assert body["deleted_file"] is False
    assert "error" in body

    # DB 已查不到，但文件还在 => 孤儿
    assert client.get(f"/api/books/{book['id']}").status_code == 404
    assert path.exists(), "文件应仍在磁盘上，证明删除不是原子的"


def test_search_cache_still_hits_deleted_book(client):
    """特征测试（§4.3）：`_search_cache` 不按 key 失效，删书后 60s 内仍返回该书。

    这是**已知缺陷**的锁定测试：若将来改成删书/扫描时清缓存，本断言会失败，
    请把它改成 `assert not again["results"]`，并同步更新 DEV-KNOWLEDGE §4.3/§9。
    """
    first = client.get("/api/search", params={"q": "曹操"}).json()
    assert first["results"]
    book_id = first["results"][0]["book"]["id"]

    assert client.delete(f"/api/books/{book_id}").status_code == 200
    assert client.get(f"/api/books/{book_id}").status_code == 404

    again = client.get("/api/search", params={"q": "曹操"}).json()
    assert [r["book"]["id"] for r in again["results"]] == [book_id], \
        "缓存未失效：仍应命中已删除的书（已知缺陷）"


def test_progress_negative_clamped_in_db_but_echoed(client):
    """§3：入库压到 >=0，但 PUT 响应回显请求原值（已知小坑）。"""
    book = client.get("/api/books", params={"q": "三国"}).json()["books"][0]

    r = client.put(f"/api/books/{book['id']}/progress", json={"char_offset": -5})
    assert r.status_code == 200
    assert r.json()["char_offset"] == -5, "响应应回显原值（已知坑）"
    assert client.get(f"/api/books/{book['id']}/progress").json()["char_offset"] == 0, \
        "库里必须被压到 0"
