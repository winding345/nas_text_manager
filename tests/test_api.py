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


def test_delete_removes_file(client, tmp_path):
    book = client.get("/api/books", params={"q": "红楼"}).json()["books"][0]
    path = tmp_path / "books" / "红楼梦.txt"
    assert path.exists()
    r = client.delete(f"/api/books/{book['id']}").json()
    assert r["deleted_file"] is True
    assert not path.exists()
    assert client.get(f"/api/books/{book['id']}").status_code == 404
