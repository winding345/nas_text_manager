# nas_text_manager

在 NAS 上读 txt 的小服务：手机/PC 双端、PWA 可装到桌面、按字符数记进度、多目录书库、文件名与内容搜索。

## 功能

- 书库：扫描挂载目录里的 txt/pdf/epub/mobi/azw3/md
- 文件名搜索：实时、空格分隔多词（AND）
- 阅读：字号 / 行距 / 4 套配色（日间、羊皮纸、夜间、护眼绿）
- 进度：按**字符偏移**保存（换字号不会错位），5 秒自动保存，切后台/关闭也会存
- 内容搜索：书内搜索 + 全库搜索（后端优先用 ripgrep，无 rg 自动退化）
- 删除：直接从磁盘删文件
- PWA：iOS/Android 添加到主屏后全屏运行

## 部署（NAS / Docker）

```bash
git clone git@github.com:winding345/nas_text_manager.git
cd nas_text_manager
cp .env.example .env      # 改成你的书籍目录
docker compose up -d --build
```

访问 `http://<nas-ip>:8686`。首次进入点「扫描」建立索引。

### 多目录

`.env` 里每个变量对应 compose 的一个挂载点：

```env
BOOKS_1=/vol3/1000/books/武侠
BOOKS_2=/vol3/1000/books/科幻
BOOKS_3=/vol3/1000/books/杂项
```

要加更多目录：compose 里加一行 `- ${BOOKS_4}:/books/data4`，并把 `BOOKS_DIRS` 改成 `/books/data1,/books/data2,/books/data3,/books/data4`。

## 手机安装

- iOS Safari 打开 → 分享 → 添加到主屏幕
- Android Chrome → 菜单 → 安装应用

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/scan` | 增量扫描目录 |
| GET | `/api/books?q=` | 书列表，多词 AND |
| GET | `/api/books/{id}/text` | 正文 |
| GET/PUT | `/api/books/{id}/progress` | 字符偏移进度 |
| GET | `/api/books/{id}/search?q=` | 书内搜索 |
| GET | `/api/search?q=` | 全库内容搜索 |
| DELETE | `/api/books/{id}` | 删书（含文件） |

## 开发

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
./.venv/bin/python -m pytest -q
./.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```
