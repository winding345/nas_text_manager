"""FastAPI 入口：书库、阅读、进度、搜索、删除。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import List, Optional

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import indexer
from .config import Settings
from .store import Store

settings = Settings.from_env()
store = Store(settings.db_path)
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

@asynccontextmanager
async def lifespan(_app):
    """启动时扫一次：避免"新部署打开是空书库"。"""
    try:
        result = indexer.scan(store, settings)
        print("[scan] 启动扫描：共 %d，新增 %d" % (result["total"], result["added"]), flush=True)
    except Exception as exc:  # noqa: BLE001  扫描失败不该阻止服务启动
        print("[scan] 启动扫描失败：%s" % exc, flush=True)
    yield


app = FastAPI(title="nas_text_manager", docs_url=None, redoc_url=None, lifespan=lifespan)


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


_search_cache: dict = {}


@app.post("/api/scan")
def rescan() -> dict:
    result = indexer.scan(store, settings)
    _search_cache.clear()          # 书库变了，旧的搜索结果立即作废
    return result


@app.get("/api/browse")
def browse(path: str = "", q: str = "") -> dict:
    """目录式浏览：返回当前目录下的子目录、书籍，以及面包屑。"""
    books = store.list_books((), limit=100000)          # 全量（只取元数据）
    all_books = [b for b in books if b["ext"]]

    # 当前目录（含子目录）的书
    if path:
        scoped = [b for b in all_books
                  if b["folder"] == path or b["folder"].startswith(path + "/")]
    else:
        scoped = all_books

    # 子目录：当前目录下所有书 folder 的第一段
    counts: dict = {}
    for b in scoped:
        rest = b["folder"][len(path):].lstrip("/")
        if not rest:
            continue                                   # 直接躺在当前目录的书
        child = rest.split("/")[0]
        counts[child] = counts.get(child, 0) + 1
    folders = [{"name": k, "path": (path + "/" + k) if path else k, "count": v}
               for k, v in sorted(counts.items())]

    # 书名搜索：覆盖当前目录 + 所有子目录；不搜索时只看当前目录直属的书
    terms = _split_terms(q)
    if terms:
        direct = [b for b in scoped
                  if all(t.lower() in b["name"].lower() for t in terms)]
        folders = []                        # 搜索时不再展示目录，直接给结果
    else:
        direct = [b for b in scoped if b["folder"] == path]

    crumbs = []
    acc = ""
    for seg in [p for p in path.split("/") if p]:
        acc = (acc + "/" + seg) if acc else seg
        crumbs.append({"name": seg, "path": acc})

    return {"path": path, "breadcrumb": crumbs, "folders": folders,
            "books": direct, "total": len(direct), "scoped": len(scoped)}


@app.get("/api/groups")
def list_groups() -> dict:
    groups = store.groups()
    return {"groups": groups, "total": store.count_books()}


@app.get("/api/books")
def list_books(q: str = "", group: str = "", subtree: str = "",
               limit: int = Query(200, ge=1, le=1000)) -> dict:
    if subtree:
        books = store.list_books(_split_terms(q), limit=limit, subtree=subtree)
    else:
        books = store.list_books(_split_terms(q), limit=limit, folder=group or None)
    return {"books": books, "total": len(books), "group": group}


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
    book = store.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")

    # 先删文件：失败就不动数据库，避免"DB 没了、文件还在"的孤儿
    file_removed = False
    try:
        os.remove(book["path"])
        file_removed = True
    except FileNotFoundError:
        file_removed = True          # 文件本来就不在，按成功处理
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="文件删除失败，数据库记录已保留：%s" % exc)

    store.delete_book(book_id)
    _search_cache.clear()
    return {"ok": True, "deleted_file": file_removed, "deleted_db": True}


@app.get("/api/books/{book_id}/search")
def search_in_book(book_id: int, q: str = "", limit: int = 0) -> dict:
    book = store.get_book(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在")
    terms = _split_terms(q)
    hits = indexer.search_book(book["path"], terms, settings, max_hits=limit)
    return {"book_id": book_id, "query": q, "hits": hits}


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


@app.get("/api/search/stream")
def search_stream(q: str = "", group: str = "", path: str = "") -> StreamingResponse:
    """全库/分组内容搜索：每找到一本就推一条，前端可以边收边显示。"""
    terms = _split_terms(q)
    if not terms:
        def empty():
            yield b'data: {"type":"done","scanned":0,"found":0}\n\n'
        return StreamingResponse(empty(), media_type="text/event-stream")

    if path:
        books = [b for b in store.list_books((), limit=100000, subtree=path) if b["ext"] == "txt"]
    else:
        books = [b for b in store.list_books((), limit=100000, folder=group or None)
                 if b["ext"] == "txt"]

    def gen():
        yield ("data: " + json.dumps(
            {"type": "start", "total": len(books)}, ensure_ascii=False) + "\n\n").encode()
        found = 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(indexer.search_book, b["path"], terms, settings, 5): b
                       for b in books}
            for fut in as_completed(futures):
                book = futures[fut]
                try:
                    hits = fut.result(timeout=30)
                except Exception:  # noqa: BLE001
                    hits = []
                if not hits:
                    continue
                found += 1
                yield ("data: " + json.dumps(
                    {"type": "hit", "book": book, "hits": hits}, ensure_ascii=False) + "\n\n").encode()
        yield ("data: " + json.dumps(
            {"type": "done", "scanned": len(books), "found": found},
            ensure_ascii=False) + "\n\n").encode()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/settings")
def get_settings() -> dict:
    return store.get_settings()


@app.put("/api/settings")
def put_settings(payload: dict = Body(...)) -> dict:
    store.put_settings({str(k): str(v) for k, v in (payload or {}).items()})
    return {"ok": True, "settings": store.get_settings()}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
