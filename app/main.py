"""FastAPI 入口：书库、阅读、进度、搜索、删除。"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional

from concurrent.futures import ThreadPoolExecutor
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import indexer
from .config import Settings
from .store import Store

settings = Settings.from_env()
store = Store(settings.db_path)
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="nas_text_manager", docs_url=None, redoc_url=None)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """静态文件禁缓存：改完前端刷新即生效，避免旧 JS 卡在浏览器里。"""
    resp = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html", ".json")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


def _split_terms(value: str) -> List[str]:
    return [t for t in value.replace("　", " ").split() if t]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "books": store.count_books(), "roots": settings.roots}


@app.post("/api/scan")
def rescan() -> dict:
    return indexer.scan(store, settings)


@app.get("/api/books")
def list_books(q: str = "", limit: int = Query(200, ge=1, le=1000)) -> dict:
    books = store.list_books(_split_terms(q), limit=limit)
    return {"books": books, "total": len(books)}


@app.get("/api/books/{book_id}")
def get_book(book_id: int) -> dict:
    book = store.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    return {**book, "progress": store.get_progress(book_id)}


@app.get("/api/books/{book_id}/text")
def get_text(book_id: int, offset: int = 0, limit: int = 0) -> dict:
    book = store.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    if book["ext"] != "txt":
        raise HTTPException(status_code=400, detail="只有 txt 支持在线阅读")
    text, total = indexer.read_text(book["path"])   # 全量读入，total 才是真实字数
    if not book["char_count"]:
        store.set_char_count(book_id, total)
    offset = max(0, min(offset, total))
    chunk = text[offset:] if not limit else text[offset:offset + limit]
    return {"text": chunk, "offset": offset, "total": total}


@app.get("/api/books/{book_id}/progress")
def get_progress(book_id: int) -> dict:
    return {"book_id": book_id, "char_offset": store.get_progress(book_id)}


@app.put("/api/books/{book_id}/progress")
def put_progress(book_id: int, payload: dict = Body(...)) -> dict:
    offset = int(payload.get("char_offset") or 0)
    store.set_progress(book_id, offset)
    return {"ok": True, "char_offset": offset}


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int) -> dict:
    if not settings.allow_delete:
        raise HTTPException(status_code=403, detail="当前未开启删除")
    book = store.delete_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    try:
        os.remove(book["path"])
    except OSError as exc:
        return {"ok": True, "deleted_db": True, "deleted_file": False, "error": str(exc)}
    return {"ok": True, "deleted_db": True, "deleted_file": True}


@app.get("/api/books/{book_id}/search")
def search_in_book(book_id: int, q: str = "", limit: int = 0) -> dict:
    book = store.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    terms = _split_terms(q)
    hits = indexer.search_book(book["path"], terms, settings, max_hits=limit)
    return {"book_id": book_id, "query": q, "hits": hits}


_search_cache: dict = {}


@app.get("/api/search")
def search_all(q: str = "", limit: int = 200) -> dict:
    """全库内容搜索：并发跑 ripgrep，带 60 秒缓存。"""
    terms = _split_terms(q)
    if not terms:
        return {"query": q, "results": []}

    key = (q.strip(), limit)
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < 60:
        return cached[1]

    # 内容搜索要覆盖全部书，不能只扫前 limit 本
    books = [b for b in store.list_books((), limit=100000) if b["ext"] == "txt"]
    results = []
    # 并发搜索，命中上限 30 本；rg 是独立进程，线程池足够
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(indexer.search_book, b["path"], terms, settings, 5): b
                   for b in books}
        for fut in futures:
            try:
                hits = fut.result(timeout=25)
            except Exception:  # noqa: BLE001 单本失败不影响整体
                hits = []
            if hits:
                results.append({"book": futures[fut], "hits": hits})
                if len(results) >= 30:
                    break

    payload = {"query": q, "results": results, "scanned": len(books)}
    _search_cache[key] = (time.time(), payload)
    if len(_search_cache) > 50:
        _search_cache.clear()
    return payload


@app.get("/api/settings")
def get_settings() -> dict:
    return store.get_settings()


@app.put("/api/settings")
def put_settings(payload: dict = Body(...)) -> dict:
    store.put_settings({str(k): str(v) for k, v in (payload or {}).items()})
    return {"ok": True, "settings": store.get_settings()}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
