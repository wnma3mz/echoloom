import json
import os

from pipeline.copier import StateStore, copy_new
from main import _cleanup_finished_audio, _group_by_source


def _touch(path: str, content: str = "x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_legacy_absolute_split_paths_become_relative(tmp_path):
    split = str(tmp_path / "split")
    state_file = tmp_path / "state.json"
    seg = "20260924/20260924085914__20260924085914.WAV"
    state_file.write_text(json.dumps({"split": {
        "20260924085914.WAV": [os.path.join(split, seg)],
    }}), encoding="utf-8")

    state = StateStore(str(state_file), split)

    assert state.data["split"]["20260924085914.WAV"] == [seg]


def test_legacy_paths_from_old_project_dir_are_rebuilt_from_filename(tmp_path):
    state_file = tmp_path / "state.json"
    old = "/Users/someone/Downloads/echoloom/data/split"
    state_file.write_text(json.dumps({"split": {
        "20260930222934.WAV": [
            f"{old}/20260930/20260930222934__20260930222934.WAV",
            f"{old}/20261001/20260930222934__20261001002934.WAV",
        ],
    }}), encoding="utf-8")

    state = StateStore(str(state_file), str(tmp_path / "split"))

    assert state.data["split"]["20260930222934.WAV"] == [
        "20260930/20260930222934__20260930222934.WAV",
        "20261001/20260930222934__20261001002934.WAV",
    ]


def test_mark_split_stores_relative_paths(tmp_path):
    split = str(tmp_path / "split")
    state = StateStore(str(tmp_path / "state.json"), split)

    state.mark_split("20261004092136.WAV", [
        os.path.join(split, "20261004/20261004092136__20261004092136.WAV"),
    ])

    assert state.data["split"]["20261004092136.WAV"] == [
        "20261004/20261004092136__20261004092136.WAV",
    ]


def test_copy_new_reports_remaining_source_files(tmp_path):
    record = tmp_path / "RECORD"
    raw = tmp_path / "raw"
    _touch(str(record / "20261004092136.WAV"))
    _touch(str(record / "20261004093000.WAV"))
    state = StateStore(str(tmp_path / "state.json"))

    copied, remaining = copy_new(str(record), str(raw), state, delete_after=True)

    assert [os.path.basename(p) for p in copied] == [
        "20261004092136.WAV", "20261004093000.WAV",
    ]
    assert remaining == []
    assert os.listdir(record) == []


def test_copy_new_already_copied_source_is_not_remaining(tmp_path):
    """上一轮已复制但源文件还在（删除失败）时，不算未取走，可弹出。"""
    record = tmp_path / "RECORD"
    raw = tmp_path / "raw"
    _touch(str(record / "20261004092136.WAV"))
    state = StateStore(str(tmp_path / "state.json"))
    state.mark_copied("20261004092136.WAV", "src")

    copied, remaining = copy_new(str(record), str(raw), state, delete_after=True)

    assert copied == []
    assert remaining == []


def test_group_by_source_merges_segments_and_plain_names(tmp_path):
    split = str(tmp_path)
    _touch(f"{split}/20261003/20261003092136__20261003092136.WAV")
    _touch(f"{split}/20261003/20261003092136__20261003093136.WAV")
    _touch(f"{split}/20261003/20261004093000.WAV")

    groups = _group_by_source(split)

    assert sorted(groups) == ["20261003092136", "20261004093000"]
    assert len(groups["20261003092136"]) == 2


def test_cleanup_keeps_audio_until_all_segments_processed(tmp_path):
    split = str(tmp_path / "split")
    raw = str(tmp_path / "raw")
    segs = [
        f"{split}/20261003/20261003092136__20261003092136.WAV",
        f"{split}/20261003/20261003092136__20261003093136.WAV",
    ]
    for p in segs:
        _touch(p)
    raw_file = _touch(f"{raw}/20261003092136.WAV")
    state = StateStore(str(tmp_path / "state.json"))
    state.mark_processed("20261003/20261003092136__20261003092136.WAV")

    _cleanup_finished_audio(split, raw, state)
    assert all(os.path.exists(p) for p in segs)
    assert os.path.exists(raw_file)

    state.mark_processed("20261003/20261003092136__20261003093136.WAV")
    _cleanup_finished_audio(split, raw, state)
    assert not any(os.path.exists(p) for p in segs)
    assert not os.path.exists(raw_file)


def test_port_open_detects_listener():
    import socket
    from main import _port_open

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert _port_open("127.0.0.1", port) is True
    finally:
        srv.close()
    assert _port_open("127.0.0.1", port) is False


def test_ensure_web_server_skips_when_already_running(tmp_path, monkeypatch):
    import socket
    import main

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    called = []
    monkeypatch.setattr(main.subprocess, "Popen", lambda *a, **k: called.append(a))
    monkeypatch.setattr(main, "_spawned_web", False)
    try:
        main.ensure_web_server({"web": {"port": port}})
    finally:
        srv.close()
    assert called == []


def test_ensure_web_server_spawns_detached(tmp_path, monkeypatch):
    import main

    calls = {}

    def fake_popen(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs

    monkeypatch.setattr(main.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(main, "_spawned_web", False)
    monkeypatch.setattr(main, "PROJECT_ROOT", str(tmp_path))
    # 找一个确定没人监听的端口
    import socket
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()

    main.ensure_web_server({"web": {"port": free, "host": "127.0.0.1"}})

    assert calls["cmd"][1].endswith("web.py")
    assert "--no-browser" in calls["cmd"]
    assert calls["kwargs"]["start_new_session"] is True
    assert main._spawned_web is True

    # 同一进程内不再重复拉起
    calls.clear()
    main.ensure_web_server({"web": {"port": free}})
    assert calls == {}


def test_ensure_web_server_opens_browser_when_configured(tmp_path, monkeypatch):
    import socket
    import main

    calls = {}
    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **kw: calls.update(cmd=cmd))
    monkeypatch.setattr(main, "_spawned_web", False)
    monkeypatch.setattr(main, "PROJECT_ROOT", str(tmp_path))
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()

    main.ensure_web_server({"web": {"port": free, "open_browser": True}})

    assert "--no-browser" not in calls["cmd"]


def test_run_once_starts_web_after_new_summary(tmp_path, monkeypatch):
    """当天 summary 生成后应自动拉起回顾界面。"""
    import asyncio
    import main

    split = tmp_path / "split"
    (split / "20261004").mkdir(parents=True)
    text = tmp_path / "text" / "20261004"
    text.mkdir(parents=True)
    (text / "a.txt").write_text("今天做了什么", encoding="utf-8")

    class FakeLLM:
        async def open(self): pass
        async def close(self): pass

    started = []
    summarized = []

    async def fake_summarize(date, llm, text_dir, summary_dir):
        summarized.append(date)

    monkeypatch.setattr(main, "build_clients", lambda cfg: (None, FakeLLM()))
    monkeypatch.setattr(main, "summarize_day", fake_summarize)
    monkeypatch.setattr(main, "ensure_web_server", lambda cfg: started.append(cfg))

    cfg = {
        "mount": {"path": str(tmp_path / "nope"), "record_dir": "RECORD",
                  "poll_interval": 1, "delete_after_copy": True, "eject_after_copy": True},
        "storage": {"raw_dir": str(tmp_path / "raw"), "split_dir": str(split),
                    "text_dir": str(tmp_path / "text"), "summary_dir": str(tmp_path / "summary"),
                    "state_file": str(tmp_path / "state.json"),
                    "delete_audio_after_process": True},
        "audio": {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "segment_seconds": 600},
        "web": {"auto_start": True},
    }
    asyncio.run(main.run_once(cfg))

    assert summarized == ["20261004"]
    assert started == [cfg]


def test_run_once_skips_web_when_nothing_to_do(tmp_path, monkeypatch):
    """没有新产出时不拉起界面。"""
    import asyncio
    import main

    started = []
    monkeypatch.setattr(main, "build_clients", lambda cfg: (None, None))
    monkeypatch.setattr(main, "ensure_web_server", lambda cfg: started.append(cfg))

    cfg = {
        "mount": {"path": str(tmp_path / "nope"), "record_dir": "RECORD", "poll_interval": 1},
        "storage": {"raw_dir": str(tmp_path / "raw"), "split_dir": str(tmp_path / "split"),
                    "text_dir": str(tmp_path / "text"), "summary_dir": str(tmp_path / "summary"),
                    "state_file": str(tmp_path / "state.json"),
                    "delete_audio_after_process": True},
        "audio": {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "segment_seconds": 600},
        "web": {"auto_start": True},
    }
    asyncio.run(main.run_once(cfg))

    assert started == []
