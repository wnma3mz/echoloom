"""本地可视化界面：回顾每日 summary，并勾掉已完成的条目。

只读 summary_dir 下的 <YYYYMMDD>.md，用 aiohttp.web 起服务（无新增依赖）。
summary 是 LLM 产出的裸 Markdown，格式不统一，渲染交给前端；
待办的勾选状态另存 marks_file（只有「完成」一种，取消了就不记），不回写 summary 原文。
"""
import json
import logging
import os
import re
import webbrowser

from aiohttp import web

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^(\d{8})\.md$")
MARK_DONE = "done"
_MARK_NAMES = {True: "完成", False: "取消"}
_MAX_KEY_LEN = 200
_PREVIEW_CHARS = 48
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

_TITLE_RE = re.compile(r"(每日|结构化每日|每日结构化)?总结[。！!]?$")
_LABEL_RE = re.compile(r"^\*\*.*\*\*\s*[：:]?$")
_HR_RE = re.compile(r"^[-*_]{3,}$")
_SECTION_RE = re.compile(r"^\d+[.、]")


def _read_summary(summary_dir: str, date: str):
    path = os.path.join(summary_dir, f"{date}.md")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _preview(content: str) -> str:
    """取一条正文做侧栏预览。

    跳过三类没区分度的行：LLM 的客套开场、文档标题（每篇都叫「每日总结」）、
    以及「**分类名**：」这种只有类目的空壳条目。每天的小节标题都是固定四段，
    也没区分度，只在实在挑不出别的行时兜底。
    """
    fallback = ""
    for line in content.splitlines():
        stripped = line.strip()
        if _HR_RE.match(stripped):
            continue
        text = re.sub(r"^[*\-]\s+", "", re.sub(r"^#{1,6}\s+", "", stripped)).lstrip("> ").strip()
        if not text or _LABEL_RE.match(text):
            continue
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).strip()
        if _TITLE_RE.match(text) or re.match(r"^(基于|以下|根据|好的|当然|这是)", text):
            continue
        if _SECTION_RE.match(text):
            fallback = fallback or text
            continue
        return text[:_PREVIEW_CHARS] + "…" if len(text) > _PREVIEW_CHARS else text
    return fallback


def list_summaries(summary_dir: str):
    """按日期倒序列出 summary，附带预览与字数。"""
    if not os.path.isdir(summary_dir):
        return []
    items = []
    for name in os.listdir(summary_dir):
        m = _DATE_RE.match(name)
        if not m:
            continue
        content = _read_summary(summary_dir, m.group(1)) or ""
        items.append({
            "date": m.group(1),
            "chars": len(content),
            "preview": _preview(content),
        })
    items.sort(key=lambda it: it["date"], reverse=True)
    return items


def search_summaries(summary_dir: str, q: str):
    """全文搜索 summary 正文，返回命中的日期（倒序）与命中次数。

    侧栏只带一条预览，跨几年翻找时预览不够用，所以搜索走服务端全文。
    """
    q = q.strip()
    if not q or not os.path.isdir(summary_dir):
        return []
    needle = q.lower()
    hits = []
    for name in os.listdir(summary_dir):
        m = _DATE_RE.match(name)
        if not m:
            continue
        content = _read_summary(summary_dir, m.group(1)) or ""
        count = content.lower().count(needle)
        if count:
            hits.append({"date": m.group(1), "hits": count})
    hits.sort(key=lambda it: it["date"], reverse=True)
    return hits


def load_marks(path: str) -> dict:
    """读取勾选状态：{日期: {已完成条目键: "done"}}。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log.warning("标记文件损坏，按空处理 %s: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def save_marks(path: str, marks: dict):
    """原子写入，避免中途崩溃留下半个文件。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(marks, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def set_mark(marks: dict, date: str, key: str, done: bool) -> dict:
    """勾上/取消一条待办，返回该日期的标记；取消后不留痕迹。"""
    day = marks.setdefault(date, {})
    if done:
        day[key] = MARK_DONE
    else:
        day.pop(key, None)
    if not day:
        marks.pop(date, None)
        day = {}
    return day


def create_app(summary_dir: str, marks_file: str, open_browser: bool = False,
               host: str = "127.0.0.1", port: int = 34100) -> web.Application:
    async def index(_request):
        return web.FileResponse(os.path.join(_STATIC_DIR, "index.html"))

    async def logo(_request):
        return web.FileResponse(os.path.join(_STATIC_DIR, "logo.svg"))

    async def api_list(_request):
        return web.json_response(list_summaries(summary_dir))

    async def api_summary(request):
        date = request.match_info["date"]
        if not re.fullmatch(r"\d{8}", date):
            raise web.HTTPBadRequest(text="日期格式应为 YYYYMMDD")
        content = _read_summary(summary_dir, date)
        if content is None:
            raise web.HTTPNotFound(text=f"没有 {date} 的 summary")
        return web.json_response({"date": date, "content": content})

    async def api_search(request):
        return web.json_response(search_summaries(summary_dir, request.query.get("q", "")))

    async def api_marks(_request):
        return web.json_response(load_marks(marks_file))

    async def api_mark(request):
        payload = await request.json()
        date, key, done = payload.get("date"), payload.get("key"), payload.get("done")
        if not isinstance(date, str) or not re.fullmatch(r"\d{8}", date):
            raise web.HTTPBadRequest(text="date 必须是 YYYYMMDD")
        if not isinstance(key, str) or not key.strip() or len(key) > _MAX_KEY_LEN:
            raise web.HTTPBadRequest(text=f"key 必须是 1~{_MAX_KEY_LEN} 字的非空字符串")
        if not isinstance(done, bool):
            raise web.HTTPBadRequest(text="done 必须是 true 或 false")
        marks = load_marks(marks_file)
        day = set_mark(marks, date, key, done)
        save_marks(marks_file, marks)
        log.info("%s %s：%s", date, _MARK_NAMES[done], key[:24])
        return web.json_response({"date": date, "marks": day})

    async def on_startup(_app):
        url = f"http://{host}:{port}/"
        log.info("界面地址 %s", url)
        if open_browser:
            webbrowser.open(url)

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/static/logo.svg", logo)
    app.router.add_get("/api/summaries", api_list)
    app.router.add_get("/api/summary/{date}", api_summary)
    app.router.add_get("/api/search", api_search)
    app.router.add_get("/api/marks", api_marks)
    app.router.add_post("/api/mark", api_mark)
    app.on_startup.append(on_startup)
    return app


def run(summary_dir: str, marks_file: str, host: str = "127.0.0.1",
        port: int = 34100, open_browser: bool = False):
    app = create_app(summary_dir, marks_file, open_browser, host, port)
    web.run_app(app, host=host, port=port, print=None, access_log=None)
