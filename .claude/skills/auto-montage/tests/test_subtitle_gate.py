"""Tests for the bilingual subtitle gate.

Runs under pytest (CI) and as a plain script (`python tests/test_subtitle_gate.py`).
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD = os.path.join(_HERE, "..", "scripts", "subtitle_align_check.py")
_spec = importlib.util.spec_from_file_location("subtitle_align_check", _MOD)
sub = importlib.util.module_from_spec(_spec)
sys.modules["subtitle_align_check"] = sub  # dataclasses need the module registered
_spec.loader.exec_module(sub)


def _write(tmpdir, name, body):
    p = os.path.join(str(tmpdir), name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    return p


GOOD = """1
00:00:00,000 --> 00:00:02,500
紅燒牛肉麵
Braised Beef Noodle Soup

2
00:00:02,700 --> 00:00:05,500
湯頭熬煮八小時
Broth simmered for eight hours
"""

OVERLAP = """1
00:00:00,000 --> 00:00:03,000
第一段字幕

2
00:00:02,500 --> 00:00:05,000
第二段跟第一段重疊
"""

TOO_FAST = """1
00:00:00,000 --> 00:00:00,500
這一行字數太多停留又太短根本看不完啦啦啦
"""


def test_good_passes(tmp_path):
    cues = sub.parse_srt(_write(tmp_path, "g.srt", GOOD))
    assert len(cues) == 2
    findings = sub.check(cues, "auto")
    assert [f for f in findings if f["severity"] == "critical"] == []


def test_overlap_is_critical(tmp_path):
    cues = sub.parse_srt(_write(tmp_path, "o.srt", OVERLAP))
    crit = [f for f in sub.check(cues, "cjk") if f["severity"] == "critical"]
    assert any("重疊" in f["msg"] for f in crit)


def test_cps_and_linelen_critical(tmp_path):
    cues = sub.parse_srt(_write(tmp_path, "f.srt", TOO_FAST))
    crit = [f for f in sub.check(cues, "cjk") if f["severity"] == "critical"]
    assert any("CPS" in f["msg"] for f in crit)
    assert any("單行過長" in f["msg"] for f in crit)


def test_pair_count_mismatch(tmp_path):
    zh = sub.parse_srt(_write(tmp_path, "zh.srt", GOOD))
    en = sub.parse_srt(_write(tmp_path, "en.srt", "1\n00:00:00,000 --> 00:00:02,500\nOnly one line\n"))
    out = sub.check_pair(zh, en)
    assert any("條數不一致" in f["msg"] for f in out)


def test_bilingual_stacked_cue_passes(tmp_path):
    # Regression: a 中/EN stacked cue must judge each line by its own script,
    # not force the whole cue to one language (the EN line is longer than the CJK limit).
    bilingual = (
        "1\n00:00:00,000 --> 00:00:03,000\n"
        "招牌紅燒牛肉麵\nSignature Braised Beef Noodles\n"
    )
    cues = sub.parse_srt(_write(tmp_path, "b.srt", bilingual))
    crit = [f for f in sub.check(cues, "zh") if f["severity"] == "critical"]
    assert crit == [], crit


def test_script_detection():
    assert sub.script_of("紅燒牛肉麵") == "cjk"
    assert sub.script_of("Beef Noodle Soup") == "latin"


if __name__ == "__main__":
    import tempfile

    class _TP:
        def __init__(self, d):
            self._d = d

        def __str__(self):
            return self._d

    passed = 0
    with tempfile.TemporaryDirectory() as d:
        tp = _TP(d)
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                try:
                    fn(tp) if "tmp_path" in fn.__code__.co_varnames else fn()
                    print(f"PASS {name}")
                    passed += 1
                except AssertionError as e:
                    print(f"FAIL {name}: {e}")
                    raise
    print(f"\n{passed} passed")
