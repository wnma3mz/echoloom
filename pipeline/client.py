"""转录与 LLM HTTP 客户端（aiohttp）。

转录后端有两种：
- omni:    多模态大模型的 chat/completions 接口，音频走 input_audio
- whisper: OpenAI 兼容的 audio/transcriptions 接口

配置里若有顶层 `omni` 段，它同时作为转录后端和 LLM；否则两者各用自己的配置。
"""
import asyncio
import base64
import contextlib
import logging
import os
import zlib
from itertools import groupby
from typing import Protocol

import aiohttp

from pipeline.splitter import segment_label

log = logging.getLogger(__name__)

_RETRY_DELAY = 5
_DEGENERATE_RUN = 20      # 连续重复同一字符达到该长度即判定为复读
_DEGENERATE_RATIO = 0.2   # zlib 压缩比低于该值即判定为复读


class Transcriber(Protocol):
    """转录后端。"""

    async def transcribe(self, audio_path: str) -> str:
        """转录单个音频文件，返回文本。"""


class _HttpClient:
    """持有 aiohttp 会话的基类，负责重试与超时。"""

    def __init__(self, url: str, api_key: str | None = None,
                 timeout: int = 1800, retries: int = 3):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _headers(self, content_type: str | None = None) -> dict:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    @property
    def _client_timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=self.timeout)

    async def _retry(self, coro_factory):
        last = None
        for i in range(self.retries):
            try:
                return await coro_factory()
            except aiohttp.ClientResponseError as e:
                if e.status < 500:
                    raise
                last = e
                log.warning("请求失败（%d/%d）HTTP %s", i + 1, self.retries, e.status)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last = e
                log.warning("请求失败（%d/%d）: %s", i + 1, self.retries, e)
            await asyncio.sleep(_RETRY_DELAY * (i + 1))
        raise last

    async def _post_json(self, payload: dict) -> dict:
        async def _do():
            async with self._session.post(
                self.url, json=payload,
                headers=self._headers("application/json"),
                timeout=self._client_timeout,
            ) as resp:
                resp.raise_for_status()
                return await resp.json()

        return await self._retry(_do)

    async def _post_form(self, form_ctx) -> dict:
        """form_ctx 是一个上下文管理器，产出待提交的 FormData（负责释放文件句柄）。"""
        async def _do():
            with form_ctx() as form:
                async with self._session.post(
                    self.url, data=form, headers=self._headers(),
                    timeout=self._client_timeout,
                ) as resp:
                    resp.raise_for_status()
                    return await resp.json()

        return await self._retry(_do)


class WhisperASR(_HttpClient):
    """OpenAI 兼容 audio/transcriptions（whisper 系列）。"""

    def __init__(self, cfg: dict, timeout: int, retries: int):
        super().__init__(cfg["url"], cfg.get("api_key"), timeout, retries)
        self.model = cfg.get("model", "whisper-1")
        self.language = cfg.get("language", "zh")

    async def transcribe(self, audio_path: str) -> str:
        @contextlib.contextmanager
        def _form():
            with open(audio_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field(
                    "file", f,
                    filename=os.path.basename(audio_path), content_type="audio/wav",
                )
                form.add_field("model", self.model)
                if self.language:
                    form.add_field("language", self.language)
                yield form

        data = await self._post_form(_form)
        return data["text"]


def _degenerate_reason(choice: dict, text: str, max_tokens: int) -> str | None:
    """判断转录结果是否为复读退化，是则返回原因，否则返回 None。

    模型在非语音片段上会陷入循环，反复输出同一字/词/句子。两种形态都要抓：
    紧挨着的重复用最长同字 run 判定；被标点或时间戳隔开的重复用压缩比判定。
    """
    if choice.get("finish_reason") == "length":
        return f"顶满 max_tokens={max_tokens}"
    run = max((sum(1 for _ in g) for _, g in groupby(text)), default=0)
    if run >= _DEGENERATE_RUN:
        return f"连续重复同一字符 {run} 字（阈值 {_DEGENERATE_RUN}）"
    raw = text.encode()
    ratio = len(zlib.compress(raw)) / len(raw)
    if ratio < _DEGENERATE_RATIO:
        return f"压缩比 {ratio:.3f} 低于 {_DEGENERATE_RATIO}"
    return None


class OmniASR(_HttpClient):
    """多模态大模型（Qwen-Omni 等）的 chat/completions 转录。

    非语音片段容易退化成复读，且 vLLM 在 temperature=0 下也不完全确定。
    这里用 frequency_penalty 抑制，并在结果退化时丢弃：返回空串交给上层按
    「转录为空」处理，坏文本不落盘，也不会卡住当天的 summary。
    """

    def __init__(self, cfg: dict, timeout: int, retries: int):
        super().__init__(cfg["url"], cfg.get("api_key"), timeout, retries)
        self.model = cfg["model"]
        self.prompt = cfg.get("prompt", "请把这段录音逐字转写为简体中文文本，不要翻译、总结或添加说明。")
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.frequency_penalty = cfg.get("frequency_penalty", 0.5)

    async def transcribe(self, audio_path: str) -> str:
        with open(audio_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": self.prompt},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": b64, "format": "wav"},
                    },
                ],
            }],
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "frequency_penalty": self.frequency_penalty,
        }
        data = await self._post_json(payload)
        choice = data["choices"][0]
        text = choice["message"]["content"]
        label = segment_label(audio_path)
        reason = _degenerate_reason(choice, text, self.max_tokens)
        if reason:
            log.warning("转录疑似复读退化，已丢弃 %s：%s", label, reason)
            return ""
        return text


class LLMClient(_HttpClient):
    def __init__(self, cfg: dict):
        super().__init__(
            cfg["url"], cfg.get("api_key"),
            cfg.get("timeout", 120), cfg.get("retries", 3),
        )
        self.model = cfg["model"]
        self.max_tokens = cfg.get("max_tokens", 8192)

    async def call(self, text: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": text}]}
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0.7,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        data = await self._post_json(payload)
        return data["choices"][0]["message"]["content"]


def build_clients(cfg: dict) -> tuple[Transcriber | None, LLMClient | None]:
    """按配置构造（转录后端, LLM）。

    顶层 omni 段存在时，它同时充当转录后端和 LLM；
    否则分别使用 transcribe.whisper 与 llm 各自的配置。
    """
    shared = cfg.get("omni")
    t_cfg = cfg.get("transcribe", {})

    if not t_cfg.get("enabled", True):
        transcriber = None
    elif shared:
        transcriber = OmniASR(
            shared, shared.get("timeout", 1800), shared.get("retries", 3)
        )
    else:
        w_cfg = t_cfg["whisper"]
        transcriber = WhisperASR(
            w_cfg, w_cfg.get("timeout", 1800), w_cfg.get("retries", 3)
        )

    l_cfg = cfg.get("llm", {})
    if not l_cfg.get("enabled", True):
        llm = None
    elif shared:
        llm = LLMClient({
            "url": shared["url"], "api_key": shared.get("api_key"),
            "model": shared["model"], "timeout": shared.get("timeout", 120),
            "max_tokens": l_cfg.get("max_tokens", 8192),
        })
    else:
        llm = LLMClient(l_cfg)

    return transcriber, llm
