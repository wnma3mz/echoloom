"""配置加载。"""
import os

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")
EXAMPLE_PATH = os.path.join(PROJECT_ROOT, "config.example.yaml")

_MOUNT_KEYS = ("path", "record_dir")
_STORAGE_KEYS = ("raw_dir", "split_dir", "text_dir", "summary_dir", "state_file")
_STORAGE_PATHS = _STORAGE_KEYS + ("marks_file",)


def _resolve(value: str) -> str:
    """相对路径按项目根解析；绝对路径与 ~ 一并展开。"""
    return os.path.abspath(os.path.join(PROJECT_ROOT, os.path.expanduser(value)))


def load_config(path: str = None) -> dict:
    path = os.path.expanduser(path or CONFIG_PATH)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"配置文件不存在：{path}\n"
            f"请先复制示例再填写：cp {EXAMPLE_PATH} {path}"
        )
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"配置文件格式错误（应为映射）：{path}")

    for section in ("mount", "storage"):
        if not isinstance(cfg.get(section), dict):
            raise ValueError(f"配置缺少 [{section}] 段：{path}")

    for key in _MOUNT_KEYS:
        if not cfg["mount"].get(key):
            raise ValueError(f"配置缺少 mount.{key}：{path}")
    cfg["mount"]["path"] = _resolve(cfg["mount"]["path"])
    cfg["mount"]["record_dir"] = os.path.join(
        cfg["mount"]["path"], cfg["mount"]["record_dir"]
    )

    for key in _STORAGE_KEYS:
        if not cfg["storage"].get(key):
            raise ValueError(f"配置缺少 storage.{key}：{path}")
    for key in _STORAGE_PATHS:
        if cfg["storage"].get(key):
            cfg["storage"][key] = _resolve(cfg["storage"][key])
    return cfg
