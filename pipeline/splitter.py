"""定时长切分：把长录音按固定时长切成若干段。

录音文件名格式为 YYYYMMDDHHMMSS.WAV，即起始时间。
用 ffprobe 读取时长，按 max_len 秒一段切开，输出按每段起始时间命名。
段长上限由调用方给定：omni 转录受服务端单次音频时长限制，whisper 则宽松得多。
"""
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^(\d{14})\.WAV$")
_TIME_FMT = "%Y%m%d%H%M%S"


def _probe_duration(ffprobe: str, path: str) -> float:
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _parse_start_time(name: str):
    m = _NAME_RE.match(name)
    if not m:
        return None
    return datetime.strptime(m.group(1), _TIME_FMT)


def segment_label(name: str) -> str:
    """把切分件名转成可读时间标签，供日志阅读。

    '20261017015344.WAV'                 -> '2026-10-17 01:53:44'
    '20261017015344__20261017022344.WAV' -> '2026-10-17 01:53:44（段 02:23:44）'
    无法解析时返回去掉扩展名的原名。
    """
    stem = os.path.splitext(os.path.basename(name))[0]
    parts = stem.split("__")
    if len(parts[0]) != 14 or not parts[0].isdigit():
        return stem
    try:
        start = datetime.strptime(parts[0], _TIME_FMT)
    except ValueError:
        return stem
    label = start.strftime("%Y-%m-%d %H:%M:%S")
    if len(parts) > 1 and parts[1] != parts[0] and len(parts[1]) == 14 and parts[1].isdigit():
        try:
            seg = datetime.strptime(parts[1], _TIME_FMT)
        except ValueError:
            return label
        label += f"（段 {seg.strftime('%H:%M:%S')}）"
    return label


def _plan_segments(start: datetime, duration: float, max_len: float):
    """把 [start, start+duration] 按 max_len 秒切成若干段。

    返回 [(段起始偏移秒, 段时长秒, 段起始时间 datetime), ...]。
    """
    segments = []
    offset = 0.0
    while offset < duration - 1e-6:
        seg_len = min(max_len, duration - offset)
        segments.append((offset, seg_len, start + timedelta(seconds=offset)))
        offset += seg_len
    return segments


def _cut_segment(ffmpeg: str, src: str, dst: str, start_sec: float, seg_len: float):
    cmd = [
        ffmpeg, "-y", "-v", "error",
        "-ss", f"{start_sec:.3f}",
        "-i", src,
        "-t", f"{seg_len:.3f}",
        "-c", "copy", dst,
    ]
    subprocess.run(cmd, check=True)


def split_file(ffmpeg: str, ffprobe: str, src: str, split_dir: str,
               max_len: float = 600.0):
    """切分单个文件为每段不超过 max_len 秒，返回输出的本地路径列表。"""
    name = os.path.basename(src)
    start = _parse_start_time(name)
    if start is None:
        log.warning("无法解析文件名时间，跳过: %s", name)
        return []

    duration = _probe_duration(ffprobe, src)
    segments = _plan_segments(start, duration, max_len)

    if len(segments) <= 1:
        date_dir = os.path.join(split_dir, start.strftime("%Y%m%d"))
        os.makedirs(date_dir, exist_ok=True)
        dst = os.path.join(date_dir, name)
        log.info("未超过段长上限，保留原文件: %s -> %s", name, dst)
        shutil.copy2(src, dst)
        return [dst]

    stem = name[:-4]
    outputs = []
    for offset, seg_len, seg_start in segments:
        date_dir = os.path.join(split_dir, seg_start.strftime("%Y%m%d"))
        os.makedirs(date_dir, exist_ok=True)
        out_name = f"{stem}__{seg_start.strftime(_TIME_FMT)}.WAV"
        dst = os.path.join(date_dir, out_name)
        log.info("切分 %s [%.1f, %.1f) -> %s", name, offset, offset + seg_len, out_name)
        _cut_segment(ffmpeg, src, dst, offset, seg_len)
        outputs.append(dst)
    return outputs
