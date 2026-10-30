"""配置：书籍目录、数据库路径、删除开关。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _split(value: str) -> List[str]:
    return [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]


@dataclass
class Settings:
    # 挂载进容器的书籍目录，逗号分隔，例如 /books/data1,/books/data2
    roots: List[str] = field(default_factory=list)
    db_path: str = "/data/library.db"
    allow_delete: bool = True
    rg_bin: str = "rg"
    # 单本书内容搜索的最大命中数
    max_hits: int = 50

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            roots=_split(os.environ.get("BOOKS_DIRS", "/books/data1")),
            db_path=os.environ.get("DB_PATH", "/data/library.db"),
            allow_delete=(os.environ.get("ALLOW_DELETE", "1") == "1"),
            rg_bin=os.environ.get("RG_BIN", "rg"),
            max_hits=int(os.environ.get("MAX_HITS", "50")),
        )
