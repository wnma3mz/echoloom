"""可视化界面入口：在浏览器里回顾每日 summary、勾掉已完成的条目。

用法：
    uv run web.py                      # 起服务并自动打开浏览器
    uv run web.py --port 34101
    uv run web.py --no-browser         # 只起服务，不开浏览器
"""
import argparse
import logging
import os

from pipeline.config import load_config
from pipeline.web import run

log = logging.getLogger("jnn")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument("--host", default=None, help="监听地址，默认取 config.yaml 的 web.host")
    parser.add_argument("--port", type=int, default=None, help="监听端口，默认取 config.yaml 的 web.port")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cfg = load_config(args.config)
    storage = cfg["storage"]
    web_cfg = cfg.get("web", {})
    host = args.host or web_cfg.get("host", "127.0.0.1")
    port = args.port or int(web_cfg.get("port", 34100))
    marks_file = storage.get("marks_file") or os.path.join(
        os.path.dirname(storage["summary_dir"]), "marks.json"
    )
    log.info("summary 目录: %s", storage["summary_dir"])
    log.info("标记文件: %s", marks_file)
    run(storage["summary_dir"], marks_file, host, port,
        open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
