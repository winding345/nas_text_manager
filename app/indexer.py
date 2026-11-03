"""扫描书籍目录 + 内容搜索。"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import Settings
from .store import Store

TEXT_EXTS = {".txt"}
ALL_EXTS = {".txt", ".pdf", ".epub", ".mobi", ".azw3", ".md"}


def scan(store: Store, settings: Settings) -> Dict[str, int]:
    """增量扫描：新增/更新入库，返回统计。"""
    added = updated = total = 0
    for root in settings.roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in ALL_EXTS:
                    continue
                full = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                total += 1
                name = os.path.splitext(filename)[0]
                # 分组：相对 root 的一级子目录；直接躺在 root 下的用 root 目录名
                rel_dir = os.path.relpath(dirpath, base)
                if rel_dir in (".", ""):
                    folder = base.name
                else:
                    folder = rel_dir.split(os.sep)[0]
                is_new = store.upsert_book(full, name, ext.lstrip("."), stat.st_size,
                                           stat.st_mtime, root, folder)
                if is_new:
                    added += 1
                else:
                    updated += 1
    return {"total": total, "added": added, "updated": updated}


def read_text(path: str, limit: int = 0) -> Tuple[str, int]:
    """读取文本，尽量容忍编码问题；返回 (文本, 字符数)。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - 兜底
        text = raw.decode("utf-8", "ignore")
    if limit and len(text) > limit:
        text = text[:limit]
    return text, len(text)


def _rg_available(binary: str) -> bool:
    return shutil.which(binary) is not None


def search_book(path: str, terms: List[str], settings: Settings,
                max_hits: int = 0) -> List[Dict[str, object]]:
    """在单本书里搜内容，返回 [{line_no, text}]。

    优先用 ripgrep（快、内存占用低）；没有 rg 时退化为逐行扫描。
    """
    terms = [t for t in terms if t]
    if not terms:
        return []
    max_hits = max_hits or settings.max_hits

    if _rg_available(settings.rg_bin):
        cmd = [settings.rg_bin, "--no-heading", "--line-number", "--color", "never",
               "--ignore-case", "--max-count", str(max_hits)]
        for term in terms:
            cmd += ["-e", term]
        cmd.append(path)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            return []
        hits: List[Dict[str, object]] = []
        for line in out.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            try:
                line_no = int(parts[1])
            except ValueError:
                continue
            hits.append({"line_no": line_no, "text": parts[2][:200]})
        return hits

    # 退化路径：逐行扫描
    text, _ = read_text(path)
    hits = []
    lowered = [t.lower() for t in terms]
    for idx, line in enumerate(text.splitlines(), start=1):
        low = line.lower()
        if all(t in low for t in lowered):
            hits.append({"line_no": idx, "text": line[:200]})
            if len(hits) >= max_hits:
                break
    return hits


def search_text_at(path: str, char_offset: int, span: int = 400) -> str:
    """取某个字符位置附近的一段文本，用于定位上下文。"""
    text, _ = read_text(path)
    start = max(0, char_offset - span // 2)
    return text[start:start + span]
