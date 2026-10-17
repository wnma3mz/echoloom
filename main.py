"""主流程：检测挂载 -> 复制 -> 整点切分 -> 弹出设备 -> 转录 -> 润色 -> 清理音频 -> 每日 summary。

转录/润色或 summary 有产出后，若回顾界面没在跑就自动在后台起一个。

用法：
    uv run main.py            # 前台运行，持续轮询
    uv run main.py --once     # 只跑一轮后退出
"""
import argparse
import asyncio
import logging
import os
import socket
import subprocess
import sys

from pipeline.client import build_clients
from pipeline.config import PROJECT_ROOT, load_config
from pipeline.copier import StateStore, _list_recordings, copy_one
from pipeline.process import process_audio, summarize_day
from pipeline.splitter import split_file
from pipeline.watcher import eject, is_mounted, wait_for_mount

log = logging.getLogger("jnn")

_spawned_web = False


def _collect_pending(split_dir: str, state: StateStore):
    pending = []
    for root, _, files in os.walk(split_dir):
        for fname in sorted(files):
            if not fname.endswith(".WAV"):
                continue
            path = os.path.join(root, fname)
            key = os.path.relpath(path, split_dir)
            if not state.is_processed(key):
                pending.append((key, path))
    return pending


def _group_by_source(split_dir: str):
    """按源录音聚合分片，返回 {录音起始时间: [分片路径, ...]}。

    切分输出为 <源名>__<段起始>.WAV，未超段长的保持原名 <源名>.WAV。
    """
    groups: dict[str, list[str]] = {}
    for root, _, files in os.walk(split_dir):
        for fname in files:
            if not fname.endswith(".WAV") or fname.startswith("._"):
                continue
            stem = fname.split("__")[0] if "__" in fname else fname[:-4]
            groups.setdefault(stem, []).append(os.path.join(root, fname))
    return groups


def _cleanup_finished_audio(split_dir: str, raw_dir: str, state: StateStore):
    """某条录音的全部分片都转录完成后，删除原始音频与分片，只保留文本。"""
    for stem, paths in _group_by_source(split_dir).items():
        keys = [os.path.relpath(p, split_dir) for p in paths]
        if not all(state.is_processed(k) for k in keys):
            continue
        for p in paths:
            try:
                os.remove(p)
            except OSError as e:
                log.warning("删除分片失败 %s: %s", p, e)
                return
        raw = os.path.join(raw_dir, f"{stem}.WAV")
        try:
            if os.path.exists(raw):
                os.remove(raw)
        except OSError as e:
            log.warning("删除原始音频失败 %s: %s", raw, e)
            return
        log.info("已删除音频 %s 及 %d 个分片", stem, len(paths))


async def _copy_split_stream(record_dir, raw_dir, split_dir, state, cfg, enqueue):
    """复制并切分设备上的新录音，每切好一段就立刻交给 enqueue，与转录重叠。

    阻塞的复制/ffmpeg 放到线程里跑，事件循环得以同时消费已切好的段。
    返回 (新复制文件的本地路径列表, 设备上仍未取走的录音文件名列表)；
    第二个列表为空即代表可以安全弹出设备。
    """
    delete_after = cfg["mount"].get("delete_after_copy", True)
    segment_seconds = cfg["audio"].get("segment_seconds", 600)
    os.makedirs(raw_dir, exist_ok=True)
    new_files = []
    for name in _list_recordings(record_dir):
        if state.is_copied(name):
            continue
        src = os.path.join(record_dir, name)
        dst = os.path.join(raw_dir, name)
        await asyncio.to_thread(copy_one, name, record_dir, raw_dir, delete_after)
        state.mark_copied(name, src)
        state.save()
        new_files.append(dst)
        outputs = await asyncio.to_thread(
            split_file, cfg["audio"]["ffmpeg"], cfg["audio"]["ffprobe"], dst,
            split_dir, segment_seconds,
        )
        state.mark_split(name, outputs)
        state.save()
        for out in outputs:
            key = os.path.relpath(out, split_dir)
            if not state.is_processed(key):
                enqueue(key, out)
    remaining = [n for n in _list_recordings(record_dir) if not state.is_copied(n)]
    return new_files, remaining


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def ensure_web_server(cfg: dict):
    """在后台拉起回顾界面；已经在跑就什么都不做。"""
    global _spawned_web
    web_cfg = cfg.get("web", {})
    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", 34100))
    if _port_open(host, port):
        return
    if _spawned_web:
        return
    cmd = [sys.executable, os.path.join(PROJECT_ROOT, "web.py"),
           "--host", host, "--port", str(port)]
    if not web_cfg.get("open_browser"):
        cmd.append("--no-browser")
    log_path = os.path.join(PROJECT_ROOT, "data", "web.log")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as out:
            subprocess.Popen(
                cmd, cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, start_new_session=True,
            )
    except OSError as e:
        log.warning("拉起回顾界面失败 %s: %s", log_path, e)
        return
    _spawned_web = True
    log.info("已自动启动回顾界面 http://%s:%d/ （日志 %s）", host, port, log_path)


async def run_once(cfg: dict):
    mount_path = cfg["mount"]["path"]
    record_dir = cfg["mount"]["record_dir"]
    raw_dir = cfg["storage"]["raw_dir"]
    split_dir = cfg["storage"]["split_dir"]
    text_dir = cfg["storage"]["text_dir"]
    summary_dir = cfg["storage"]["summary_dir"]
    state = StateStore(cfg["storage"]["state_file"], split_dir)

    transcriber, llm = build_clients(cfg)
    if transcriber:
        await transcriber.open()
    if llm:
        await llm.open()
    try:
        workers = cfg.get("concurrency", 6)
        can_process = bool(transcriber and llm)
        did_work = False

        queue: asyncio.Queue = asyncio.Queue()

        async def consume():
            nonlocal did_work
            while True:
                item = await queue.get()
                try:
                    if item is None:
                        return
                    key, path = item
                    try:
                        await process_audio(path, transcriber, llm, text_dir)
                    except Exception:
                        log.exception("处理失败 %s", key)
                        continue
                    state.mark_processed(key)
                    state.save()
                    did_work = True
                finally:
                    queue.task_done()

        def enqueue(key, path):
            if can_process:
                queue.put_nowait((key, path))

        worker_tasks = (
            [asyncio.create_task(consume()) for _ in range(workers)]
            if can_process else []
        )

        # 本地已有的待处理音频先进队列，复制新文件期间它就在转录了
        if can_process:
            for key, path in _collect_pending(split_dir, state):
                enqueue(key, path)

        if is_mounted(mount_path):
            if can_process:
                log.info("并发处理音频 (concurrency=%d)", workers)
            # 边复制切分边投递转录，不必等整批复制完
            new_files, remaining = await _copy_split_stream(
                record_dir, raw_dir, split_dir, state, cfg, enqueue
            )
            # 设备上的录音已全部取走并删除源文件，才弹出，避免半途拔掉
            if new_files and not remaining and cfg["mount"].get("eject_after_copy", True):
                await asyncio.to_thread(eject, mount_path)
        else:
            log.info("未检测到挂载点 %s，继续处理本地已有音频", mount_path)

        # 无转录客户端时（占位/仅复制）把音频直接标记为已处理，避免卡住。
        # 收集放到复制之后，本轮新切的也一并覆盖。
        if not can_process:
            for key, _ in _collect_pending(split_dir, state):
                state.mark_processed(key)
            state.save()

        for _ in worker_tasks:
            queue.put_nowait(None)
        if worker_tasks:
            await asyncio.gather(*worker_tasks)

        # 转录+润色已完成的音频不再需要，可清理。
        # 必须有真实客户端：否则上面那个占位分支会把没转录的音频标成已处理。
        if can_process and cfg["storage"].get("delete_audio_after_process", True):
            _cleanup_finished_audio(split_dir, raw_dir, state)

        # 每日 summary：当天音频都处理完后生成
        if llm:
            for root, _, files in os.walk(split_dir):
                date = os.path.basename(root)
                if not date.isdigit() or len(date) != 8:
                    continue
                if state.is_summarized(date):
                    continue
                unprocessed = any(
                    not state.is_processed(
                        os.path.relpath(os.path.join(root, f), split_dir)
                    )
                    for f in files if f.endswith(".WAV")
                )
                if unprocessed:
                    continue
                await summarize_day(date, llm, text_dir, summary_dir)
                state.mark_summarized(date)
                state.save()
                did_work = True

        if did_work and cfg.get("web", {}).get("auto_start", True):
            ensure_web_server(cfg)
    finally:
        if transcriber:
            await transcriber.close()
        if llm:
            await llm.close()


async def _run(cfg: dict, once: bool):
    if once:
        await run_once(cfg)
        return
    while True:
        wait_for_mount(cfg["mount"]["path"], cfg["mount"]["poll_interval"])
        await run_once(cfg)
        await asyncio.sleep(cfg["mount"]["poll_interval"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="只运行一轮")
    parser.add_argument("--config", default=None, help="配置文件路径")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cfg = load_config(args.config)
    try:
        asyncio.run(_run(cfg, args.once))
    except KeyboardInterrupt:
        log.info("已中断")


if __name__ == "__main__":
    main()
