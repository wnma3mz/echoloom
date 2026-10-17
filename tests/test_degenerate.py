from pipeline.client import _DEGENERATE_RUN, _degenerate_reason

# 真实 omni 输出中出现过的退化形态
LOOP_SAME_CHAR = "哔" * 500
LOOP_TIMESTAMPS = "".join(f"[ {i // 60:02d}:{i % 60:02d} ] 你\n" for i in range(120))
LOOP_LYRICS = "我住长江头，君住长江尾。日日思君不见君，共饮长江水。\n" * 24
LOOP_PHRASE = "王师傅" * 100
# 非退化
NORMAL = (
    "没有，他全部弄好了，不需要我管。像你这样子租房子，比如说用的时候现在，"
    "比如出现问题，是找房东吧。嗯，应该是找他。哦对，我上次也遇到过这种情况。"
)
NORMAL_SHORT = "（键盘敲击声）"
NORMAL_SONGS = "我住长江头，君住长江尾。日日思君不见君，共饮长江水。"


def ok(text, finish="stop"):
    assert _degenerate_reason({"finish_reason": finish}, text, 8192) is None


def bad(text, finish="stop"):
    assert _degenerate_reason({"finish_reason": finish}, text, 8192) is not None


def test_normal_text_kept():
    ok(NORMAL)
    ok(NORMAL_SHORT)
    ok(NORMAL_SONGS)


def test_truncated_by_max_tokens_rejected():
    bad(NORMAL, finish="length")


def test_same_char_loop_rejected():
    bad(LOOP_SAME_CHAR)
    reason = _degenerate_reason({"finish_reason": "stop"}, LOOP_SAME_CHAR, 8192)
    assert "重复同一字符" in reason


def test_timestamp_split_loop_rejected():
    # 字符都被时间戳隔开，最长同字 run 只有 1，只能靠压缩比识别
    assert _DEGENERATE_RUN > 1
    bad(LOOP_TIMESTAMPS)


def test_repeated_phrase_loop_rejected():
    bad(LOOP_PHRASE)
    bad(LOOP_LYRICS)
