"""转录 + LLM 润色 + 每日 summary。"""
import logging
import os

from pipeline.client import LLMClient, Transcriber
from pipeline.splitter import segment_label

log = logging.getLogger(__name__)

POLISH_PROMPT = (
    "你是语音转写整理助手。下面是一段语音识别（ASR）得到的文本，"
    "可能包含错别字、重复、语气词、断句不自然等问题。"
    "请在不改变原意的前提下，整理成通顺、准确、标点规范的中文文本。"
    "只输出整理后的文本，不要添加任何说明或标题。\n\n"
    "待整理文本：\n{text}"
)

SUMMARY_PROMPT = (
    "以下是我一天中随手录制的多条语音转成的文字，按时间顺序排列。"
    "请把它们整理成一份结构化的每日总结，包含："
    "1. 事件与任务（当天做了/要做什么）\n"
    "2. 想法与灵感\n"
    "3. 待办事项\n"
    "4. 其他值得记录的内容\n"
    "用简洁的要点列出，忠实于原文，不要编造。\n\n"
    "当天录音内容：\n{text}"
)


def _date_of(audio_path: str) -> str:
    """从 split/<YYYYMMDD>/xxx.WAV 路径中取出日期。"""
    return os.path.basename(os.path.dirname(audio_path))


async def process_audio(
    audio_path: str,
    transcriber: Transcriber,
    llm: LLMClient,
    text_dir: str,
):
    """转录单个音频并用 LLM 润色，写入 text/<YYYYMMDD>/<name>.txt。"""
    name = os.path.splitext(os.path.basename(audio_path))[0]
    date = _date_of(audio_path)
    label = segment_label(name)
    raw_text = await transcriber.transcribe(audio_path)
    log.info("转录完成 %s: %s", label, raw_text[:50])

    if not raw_text.strip():
        log.warning("转录为空，跳过 LLM: %s", label)
        return raw_text, ""

    polished = await llm.call(POLISH_PROMPT.format(text=raw_text))
    log.info("润色完成 %s: %s", label, polished[:50])

    date_dir = os.path.join(text_dir, date)
    os.makedirs(date_dir, exist_ok=True)
    with open(os.path.join(date_dir, name + ".txt"), "w", encoding="utf-8") as f:
        f.write(polished)
    return raw_text, polished


def _list_texts(text_dir: str, date: str):
    """按文件名排序，返回 date 目录下所有 txt 内容列表。"""
    date_dir = os.path.join(text_dir, date)
    if not os.path.isdir(date_dir):
        return []
    contents = []
    for fname in sorted(os.listdir(date_dir)):
        if fname.endswith(".txt"):
            with open(os.path.join(date_dir, fname), "r", encoding="utf-8") as f:
                contents.append(f.read().strip())
    return contents


def _count_texts(text_dir: str, date: str) -> int:
    date_dir = os.path.join(text_dir, date)
    if not os.path.isdir(date_dir):
        return 0
    return len([f for f in os.listdir(date_dir) if f.endswith(".txt")])


async def summarize_day(
    date: str,
    llm: LLMClient,
    text_dir: str,
    summary_dir: str,
) -> str:
    """汇总某一天的所有润色文本，生成每日 summary，返回 summary 文本。"""
    contents = _list_texts(text_dir, date)
    if not contents:
        log.info("日期 %s 无文本，跳过 summary", date)
        return ""
    body = "\n\n---\n\n".join(contents)
    summary = await llm.call(SUMMARY_PROMPT.format(text=body))
    os.makedirs(summary_dir, exist_ok=True)
    path = os.path.join(summary_dir, date + ".md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(summary)
    log.info("生成 summary: %s", path)
    return summary
