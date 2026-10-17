from datetime import datetime, timedelta

from pipeline.splitter import _parse_start_time, _plan_segments, segment_label


def test_parse_start_time():
    assert _parse_start_time("20260923211139.WAV") == datetime(2026, 9, 23, 21, 11, 39)
    assert _parse_start_time("bad.WAV") is None


def test_plan_segments_shorter_than_limit():
    start = datetime(2026, 9, 23, 21, 11, 39)
    segs = _plan_segments(start, 22.336, 600)
    assert len(segs) == 1
    assert segs[0] == (0.0, 22.336, start)


def test_plan_segments_splits_on_fixed_length():
    start = datetime(2026, 9, 23, 21, 33, 53)
    segs = _plan_segments(start, 1700.0, 600)  # 600 + 600 + 500
    assert len(segs) == 3
    assert [round(s[1]) for s in segs] == [600, 600, 500]
    assert segs[1][2] == start + timedelta(seconds=600)
    assert segs[2][2] == start + timedelta(seconds=1200)


def test_plan_segments_exact_multiple():
    start = datetime(2026, 9, 23, 21, 0, 0)
    segs = _plan_segments(start, 1200.0, 600)
    assert len(segs) == 2
    assert segs[-1][2] == datetime(2026, 9, 23, 21, 10, 0)


def test_plan_segments_one_hour_six_segments():
    start = datetime(2026, 9, 23, 21, 0, 0)
    segs = _plan_segments(start, 3600.0, 600)
    assert len(segs) == 6
    assert all(round(s[1]) == 600 for s in segs)


def test_segment_label_single_recording():
    assert segment_label("20261017015344.WAV") == "2026-10-17 01:53:44"
    assert segment_label("20261017015344") == "2026-10-17 01:53:44"


def test_segment_label_split_recording():
    assert segment_label("20261017015344__20261017022344.WAV") == \
        "2026-10-17 01:53:44（段 02:23:44）"


def test_segment_label_falls_back_when_unparseable():
    assert segment_label("bad.WAV") == "bad"
    assert segment_label("20261017015344__notatime.WAV") == "2026-10-17 01:53:44"


def test_plan_segments_does_not_overlap_and_covers_duration():
    start = datetime(2026, 9, 23, 21, 0, 0)
    segs = _plan_segments(start, 3601.0, 600)
    assert sum(s[1] for s in segs) == 3601.0
    assert [s[0] for s in segs] == [0.0, 600.0, 1200.0, 1800.0, 2400.0, 3000.0, 3600.0]
