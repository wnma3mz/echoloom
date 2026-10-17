"""挂载检测：轮询 JNN 挂载点，触发复制与切分；录音取完后弹出设备。"""
import logging
import os
import subprocess
import time

log = logging.getLogger(__name__)


def is_mounted(path: str) -> bool:
    return os.path.isdir(path)


def wait_for_mount(path: str, poll_interval: float):
    """阻塞直到挂载点出现。返回后挂载点可用。"""
    log.info("等待挂载点出现: %s", path)
    while not is_mounted(path):
        time.sleep(poll_interval)
    log.info("检测到挂载点: %s", path)


def eject(path: str) -> bool:
    """弹出挂载点（macOS diskutil eject）。成功返回 True。"""
    try:
        proc = subprocess.run(
            ["diskutil", "eject", path],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("弹出设备失败 %s: %s", path, e)
        return False
    if proc.returncode != 0:
        log.warning("弹出设备失败 %s: %s", path, proc.stderr.strip() or proc.stdout.strip())
        return False
    log.info("已弹出设备 %s", path)
    return True
