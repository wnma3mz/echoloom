from pipeline.client import OmniASR, WhisperASR, build_clients

OMNI = {
    "url": "http://host:18301/v1/chat/completions",
    "api_key": "k",
    "model": "qwen3-omni-30b-a3b-fp8",
    "timeout": 1800,
    "retries": 3,
}
WHISPER = {
    "enabled": True,
    "whisper": {
        "url": "http://host/v1/audio/transcriptions",
        "api_key": "k2",
        "model": "whisper-large-v3",
        "language": "zh",
    },
}
LLM = {
    "enabled": True,
    "url": "http://host/v1/chat/completions",
    "api_key": "k2",
    "model": "qwen3.6-35b-a3b-nvfp4",
    "timeout": 120,
}


def test_omni_block_takes_over_both_transcribe_and_llm():
    asr, llm = build_clients({"omni": OMNI, "transcribe": WHISPER, "llm": LLM})
    assert isinstance(asr, OmniASR)
    assert asr.model == "qwen3-omni-30b-a3b-fp8"
    assert asr.url == "http://host:18301/v1/chat/completions"
    assert asr.timeout == 1800
    assert llm.model == "qwen3-omni-30b-a3b-fp8"
    assert llm.url == "http://host:18301/v1/chat/completions"
    assert llm.api_key == "k"


def test_without_omni_block_falls_back_to_whisper_and_llm():
    asr, llm = build_clients({"transcribe": WHISPER, "llm": LLM})
    assert isinstance(asr, WhisperASR)
    assert asr.model == "whisper-large-v3"
    assert asr.language == "zh"
    assert asr.timeout == 1800
    assert llm.model == "qwen3.6-35b-a3b-nvfp4"
    assert llm.timeout == 120


def test_transcribe_disabled_still_uses_shared_llm():
    asr, llm = build_clients(
        {"omni": OMNI, "transcribe": {"enabled": False}, "llm": LLM}
    )
    assert asr is None
    assert llm.model == "qwen3-omni-30b-a3b-fp8"


def test_llm_disabled_still_uses_shared_asr():
    asr, llm = build_clients(
        {"omni": OMNI, "transcribe": WHISPER, "llm": {"enabled": False}}
    )
    assert isinstance(asr, OmniASR)
    assert llm is None


def test_llm_max_tokens_is_configurable():
    _, llm = build_clients({"omni": OMNI, "llm": {"max_tokens": 8192}})
    assert llm.max_tokens == 8192


def test_llm_max_tokens_default_is_8192():
    _, llm = build_clients({"omni": OMNI, "llm": LLM})
    assert llm.max_tokens == 8192
    _, llm2 = build_clients({"transcribe": WHISPER, "llm": dict(LLM, max_tokens=4096)})
    assert llm2.max_tokens == 4096
