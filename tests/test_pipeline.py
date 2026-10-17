"""异步流水线：复制/切分与转录重叠、弹出时机。"""
import asyncio
import json
import os
import threading
from pathlib import Path

import main


def _base_cfg(tmp_path):
    return {
        "mount": {"path": str(tmp_path / "JNN"), "record_dir": str(tmp_path / "RECORD"),
                  "delete_after_copy": True, "eject_after_copy": True},
        "storage": {"raw_dir": str(tmp_path / "raw"), "split_dir": str(tmp_path / "split"),
                    "text_dir": str(tmp_path / "text"), "summary_dir": str(tmp_path / "summary"),
                    "state_file": str(tmp_path / "state.json"),
                    "delete_audio_after_process": True},
        "audio": {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "segment_seconds": 600},
        "concurrency": 2,
        "web": {"auto_start": False},
    }


class _FakeLLM:
    async def open(self): pass
    async def close(self): pass
    async def call(self, text): return "润色"


def _fake_split(ffmpeg, ffprobe, src, split_dir, max_len):
    date_dir = Path(split_dir) / "20260101"
    date_dir.mkdir(parents=True, exist_ok=True)
    out = date_dir / Path(src).name
    out.write_text("x", encoding="utf-8")
    return [str(out)]


def test_transcribes_while_still_copying(tmp_path, monkeypatch):
    """第二条录音还在复制时，第一条切好的段应已开始转录（两方在栅栏会合）。"""
    record = tmp_path / "RECORD"
    record.mkdir()
    (record / "20260101090000.WAV").write_text("x", encoding="utf-8")
    (record / "20260101091000.WAV").write_text("x", encoding="utf-8")

    barrier = threading.Barrier(2, timeout=5)
    seen = {"copies": 0, "tx": 0}

    def fake_copy_one(name, record_dir, raw_dir, delete_after=True):
        os.makedirs(raw_dir, exist_ok=True)
        seen["copies"] += 1
        if seen["copies"] == 2:
            barrier.wait()  # 必须与某次转录同时到达，否则超时
        dst = Path(raw_dir) / name
        dst.write_text("x", encoding="utf-8")
        return str(dst)

    class FakeASR:
        async def open(self): pass
        async def close(self): pass
        async def transcribe(self, path):
            seen["tx"] += 1
            if seen["tx"] == 1:
                await asyncio.to_thread(barrier.wait)
            return "文字"

    monkeypatch.setattr(main, "build_clients", lambda cfg: (FakeASR(), _FakeLLM()))
    monkeypatch.setattr(main, "copy_one", fake_copy_one)
    monkeypatch.setattr(main, "split_file", _fake_split)
    monkeypatch.setattr(main, "is_mounted", lambda path: True)
    monkeypatch.setattr(main, "eject", lambda path: True)

    cfg = _base_cfg(tmp_path)
    asyncio.run(main.run_once(cfg))

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert len(state["processed"]) == 2


def test_ejects_after_all_sources_consumed(tmp_path, monkeypatch):
    """源文件全部取走（remaining 为空）后弹出设备。"""
    record = tmp_path / "RECORD"
    record.mkdir()
    (record / "20260101090000.WAV").write_text("x", encoding="utf-8")

    def fake_copy_one(name, record_dir, raw_dir, delete_after=True):
        os.makedirs(raw_dir, exist_ok=True)
        dst = Path(raw_dir) / name
        dst.write_text("x", encoding="utf-8")
        if delete_after:
            os.remove(os.path.join(record_dir, name))
        return str(dst)

    ejected = []
    monkeypatch.setattr(main, "build_clients", lambda cfg: (None, None))
    monkeypatch.setattr(main, "copy_one", fake_copy_one)
    monkeypatch.setattr(main, "split_file", _fake_split)
    monkeypatch.setattr(main, "is_mounted", lambda path: True)
    monkeypatch.setattr(main, "eject", lambda path: ejected.append(path) or True)

    cfg = _base_cfg(tmp_path)
    asyncio.run(main.run_once(cfg))

    assert ejected == [cfg["mount"]["path"]]
