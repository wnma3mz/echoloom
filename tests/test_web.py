from pipeline.web import list_summaries, load_marks, save_marks, search_summaries, set_mark


def _write(summary_dir, name, content):
    path = summary_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def test_list_summaries_sorted_by_date_desc(tmp_path):
    _write(tmp_path, "20260930.md", "内容")
    _write(tmp_path, "20261003.md", "内容")
    _write(tmp_path, "20261001.md", "内容")

    items = list_summaries(str(tmp_path))

    assert [it["date"] for it in items] == ["20261003", "20261001", "20260930"]


def test_list_summaries_ignores_non_dated_files(tmp_path):
    _write(tmp_path, "20261003.md", "内容")
    _write(tmp_path, "notes.md", "内容")
    _write(tmp_path, "2026103.md", "内容")
    _write(tmp_path, "20261003.md.bak", "内容")

    assert [it["date"] for it in list_summaries(str(tmp_path))] == ["20261003"]


def test_list_summaries_missing_dir_is_empty(tmp_path):
    assert list_summaries(str(tmp_path / "nope")) == []


def test_list_summaries_reports_char_count(tmp_path):
    _write(tmp_path, "20261003.md", "每日总结")

    assert list_summaries(str(tmp_path))[0]["chars"] == 4


def test_preview_skips_preamble_and_title(tmp_path):
    _write(tmp_path, "20260930.md", "\n".join([
        "基于您提供的语音转文字内容，整理出的每日结构化总结如下：",
        "",
        "### 每日总结",
        "",
        "### 1. 事件与任务",
        "*   **工作/面试相关沟通**：",
        "    *   回复了关于看机会的询问。",
    ]))

    assert list_summaries(str(tmp_path))[0]["preview"] == "回复了关于看机会的询问。"


def test_preview_truncates_long_line(tmp_path):
    _write(tmp_path, "20261003.md", "- " + "长" * 80)

    preview = list_summaries(str(tmp_path))[0]["preview"]
    assert preview.endswith("…")
    assert len(preview) == 49


def test_marks_roundtrip(tmp_path):
    path = str(tmp_path / "marks.json")
    marks = {}
    set_mark(marks, "20261003", "买酱油", True)
    set_mark(marks, "20261004", "遛狗", True)
    save_marks(path, marks)

    assert load_marks(path) == {
        "20261003": {"买酱油": "done"},
        "20261004": {"遛狗": "done"},
    }


def test_set_mark_is_idempotent(tmp_path):
    marks = {}
    set_mark(marks, "20261003", "买酱油", True)
    set_mark(marks, "20261003", "买酱油", True)

    assert marks == {"20261003": {"买酱油": "done"}}


def test_unchecking_removes_entry(tmp_path):
    marks = {}
    set_mark(marks, "20261003", "买酱油", True)

    set_mark(marks, "20261003", "买酱油", False)

    assert marks == {}


def test_unchecking_drops_empty_day(tmp_path):
    marks = {"20261003": {"买酱油": "done"}, "20261002": {"遛狗": "done"}}

    set_mark(marks, "20261003", "买酱油", False)

    assert "20261003" not in marks
    assert "20261002" in marks


def test_load_marks_missing_file_is_empty(tmp_path):
    assert load_marks(str(tmp_path / "nope.json")) == {}


def test_load_marks_broken_file_is_empty(tmp_path):
    path = tmp_path / "marks.json"
    path.write_text("{ 坏掉的 json", encoding="utf-8")

    assert load_marks(str(path)) == {}


def test_save_marks_creates_parent_dir(tmp_path):
    path = str(tmp_path / "deep" / "marks.json")

    save_marks(path, {"20261003": {"买酱油": "done"}})

    assert load_marks(path) == {"20261003": {"买酱油": "done"}}


def test_search_summaries_matches_full_text(tmp_path):
    _write(tmp_path, "20261002.md", "- 空调遥控器很好用\n- 买了空调")
    _write(tmp_path, "20261001.md", "- 提到外骨骼")
    _write(tmp_path, "20260930.md", "- 讨论求职")

    hits = search_summaries(str(tmp_path), "空调")

    assert hits == [{"date": "20261002", "hits": 2}]


def test_search_summaries_sorted_by_date_desc(tmp_path):
    _write(tmp_path, "20260930.md", "- 空调")
    _write(tmp_path, "20261003.md", "- 空调")

    assert [h["date"] for h in search_summaries(str(tmp_path), "空调")] == ["20261003", "20260930"]


def test_search_summaries_empty_query_returns_nothing(tmp_path):
    _write(tmp_path, "20261003.md", "- 空调")

    assert search_summaries(str(tmp_path), "   ") == []
