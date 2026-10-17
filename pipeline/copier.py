"""复制录音：从挂载点复制新录音到本地 raw 目录，按状态去重。"""
import json
import logging
import os
import shutil

log = logging.getLogger(__name__)


class StateStore:
    """记录已复制/已切分的文件，防重复处理。"""

    def __init__(self, path: str, split_dir: str | None = None):
        self.path = path
        self.split_dir = split_dir
        self.data = {"copied": {}, "split": {}, "processed": [], "summarized": []}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in self.data.items():
                if k in loaded:
                    self.data[k] = loaded[k]
        if split_dir:
            # 历史数据存的是绝对路径，项目一搬家就全失效了，换成相对路径
            for name, paths in self.data["split"].items():
                self.data["split"][name] = [self._split_rel(p) for p in paths]

    def _split_rel(self, path: str) -> str:
        """切分输出统一存相对 split_dir 的路径。

        历史数据存的是绝对路径，且指向项目搬家前的旧目录，一律作废；
        切分件名以段起始时间开头（YYYYMMDDHHMMSS），日期目录按段起始时间重建，
        跨小时的段才会落到下一天目录（见 splitter 的归档规则）。
        """
        if not self.split_dir or not os.path.isabs(path):
            return path
        root = os.path.abspath(self.split_dir)
        if os.path.abspath(path).startswith(root + os.sep):
            return os.path.relpath(path, root)
        name = os.path.basename(path)
        seg_start = name.split("__")[-1] if "__" in name else name
        return f"{seg_start[:8]}/{name}"

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_copied(self, name: str) -> bool:
        return name in self.data["copied"]

    def mark_copied(self, name: str, src: str):
        self.data["copied"][name] = {"src": src}

    def mark_split(self, name: str, outputs):
        self.data["split"][name] = [self._split_rel(p) for p in outputs]

    def is_processed(self, name: str) -> bool:
        return name in self.data["processed"]

    def mark_processed(self, name: str):
        if name not in self.data["processed"]:
            self.data["processed"].append(name)

    def is_summarized(self, date: str) -> bool:
        return date in self.data.get("summarized", [])

    def mark_summarized(self, date: str):
        if date not in self.data.setdefault("summarized", []):
            self.data["summarized"].append(date)


def _list_recordings(record_dir: str):
    """返回录音文件名列表（仅 .WAV，忽略 macOS 的 ._ 前缀文件）。"""
    out = []
    if not os.path.isdir(record_dir):
        return out
    for name in os.listdir(record_dir):
        if name.endswith(".WAV") and not name.startswith("._"):
            out.append(name)
    return sorted(out)


def copy_one(name: str, record_dir: str, raw_dir: str, delete_after: bool = True) -> str:
    """阻塞地把设备上的一条录音复制到 raw，按需删除源文件，返回本地路径。

    只做文件操作、不碰 state，方便放进线程执行（state 的读写留在事件循环线程）。
    delete_after=True 时复制成功后删除源文件；删除失败只告警，不算复制失败。
    """
    src = os.path.join(record_dir, name)
    dst = os.path.join(raw_dir, name)
    log.info("复制 %s -> %s", src, dst)
    shutil.copy2(src, dst)
    if delete_after:
        try:
            os.remove(src)
            log.info("已删除源文件 %s", src)
        except OSError as e:
            log.warning("删除源文件失败 %s: %s", src, e)
    return dst


def copy_new(record_dir: str, raw_dir: str, state: StateStore, delete_after: bool = True):
    """复制新录音到本地（同步版，供测试/脚本使用）。

    返回 (新复制文件的本地路径列表, 设备上仍未复制的录音文件名列表)。
    第二个列表为空即代表设备上的录音已全部取走，可以安全弹出。
    """
    os.makedirs(raw_dir, exist_ok=True)
    copied = []
    for name in _list_recordings(record_dir):
        if state.is_copied(name):
            continue
        dst = copy_one(name, record_dir, raw_dir, delete_after)
        state.mark_copied(name, os.path.join(record_dir, name))
        state.save()
        copied.append(dst)
    remaining = [n for n in _list_recordings(record_dir) if not state.is_copied(n)]
    return copied, remaining
