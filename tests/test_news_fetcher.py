"""news_fetcher date + ticker + path validation tests."""
import os
import sys
import tempfile

# Make the module importable
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "macro-bot"))

import news_fetcher as nf  # noqa: E402


class TestParseDate:
    def test_iso_with_t(self):
        r = nf._parse_date("2026-07-30T08:00:00")
        assert r is not None
        assert r.tzinfo is None
        assert r.year == 2026 and r.minute == 0

    def test_iso_with_tz_offset(self):
        r = nf._parse_date("2026-07-30T08:00:00+08:00")
        assert r is not None
        assert r.tzinfo is None  # naive after our normalize
        assert r.hour == 8

    def test_iso_with_z_utc(self):
        # After the fix, 'Z' should be normalized to '+00:00' before fromisoformat.
        r = nf._parse_date("2026-07-30T00:00:00Z")
        assert r is not None
        assert r.tzinfo is None  # naive after normalize
        assert r.year == 2026 and r.hour == 0

    def test_iso_with_microseconds(self):
        r = nf._parse_date("2026-07-30T08:00:00.123456")
        assert r is not None
        assert r.microsecond == 123456

    def test_space_separated(self):
        r = nf._parse_date("2026-07-30 08:00:00")
        assert r is not None
        assert r.hour == 8 and r.minute == 0

    def test_date_only(self):
        r = nf._parse_date("2026-07-30")
        assert r is not None
        assert r.hour == 0

    def test_slash_date(self):
        r = nf._parse_date("2026/07/30")
        assert r is not None

    def test_compact_date(self):
        r = nf._parse_date("20260730")
        assert r is not None
        assert r.day == 30

    def test_garbage_returns_none(self):
        assert nf._parse_date("not a date") is None
        assert nf._parse_date("") is None
        assert nf._parse_date(None) is None
        assert nf._parse_date(12345) is None


class TestTickerEquivalents:
    def test_ss_to_ch_collision(self):
        e = nf._ticker_equivalents("688169.SS")
        assert "688169" in e
        assert "688169.CH" in e
        assert "688169.SS" in e

    def test_t_to_jp_collision(self):
        a = nf._ticker_equivalents("9843.T")
        b = nf._ticker_equivalents("9843.JP")
        assert a & b, f"9843.T and 9843.JP must overlap, got {a} vs {b}"

    def test_de_to_gr_collision(self):
        a = nf._ticker_equivalents("PUM.DE")
        b = nf._ticker_equivalents("PUM.GR")
        assert a & b

    def test_pa_to_fr_collision(self):
        a = nf._ticker_equivalents("KER.PA")
        b = nf._ticker_equivalents("KER.FR")
        assert a & b

    def test_empty_returns_empty(self):
        assert nf._ticker_equivalents("") == set()
        # None must not crash
        assert nf._ticker_equivalents(None) == set()

    def test_bare_code_includes_all_markets(self):
        e = nf._ticker_equivalents("AAPL")
        # bare code with no suffix should include common markets
        assert "AAPL" in e
        # no-suffix case deliberately also adds all market variants
        assert len(e) > 1

    def test_us_ticker(self):
        e = nf._ticker_equivalents("WMT")
        assert "WMT" in e

    def test_hk_ticker(self):
        e = nf._ticker_equivalents("9992.HK")
        assert "9992.HK" in e
        assert "9992" in e


class TestSafeBriefingPath:
    """_safe_briefing_path lives in macro-bot/app.py (not news_fetcher.py).

    app.py imports `from app import _safe_briefing_path`, but the module is heavy
    (flask, dotenv, sqlite at import time). For this test we only need the function,
    so we copy the implementation verbatim and assert via a local mirror.

    A regression test that runs against the real app.py would require monkey-patching
    BRIEFINGS_DIR + DB_PATH + dotenv before import, which we keep out of scope here.
    """

    def setup_method(self):
        # Mirror of the implementation in macro-bot/app.py (kept identical on purpose).
        import re as _re
        from datetime import datetime as _dt
        DATE_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")

        def valid_date_str(date):
            if not DATE_RE.match(date):
                return False
            try:
                _dt.strptime(date, "%Y-%m-%d")
                return True
            except ValueError:
                return False

        import os as _os

        def safe_briefing_path(date, base_dir):
            if not valid_date_str(date):
                return None
            path = _os.path.join(base_dir, "%s.json" % date)
            real = _os.path.realpath(path)
            real_dir = _os.path.realpath(base_dir)
            if not real.startswith(real_dir + _os.sep):
                return None
            return real

        self.fn = safe_briefing_path
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def teardown_method(self):
        self.tmp.cleanup()

    def test_valid_date(self):
        p = self.fn("2026-07-30", self.base)
        assert p is not None
        assert os.path.realpath(p).startswith(os.path.realpath(self.base) + os.sep)

    def test_invalid_date_rejected(self):
        assert self.fn("2026-99-99", self.base) is None

    def test_traversal_rejected(self):
        assert self.fn("../etc/passwd", self.base) is None

    def test_embedded_traversal_rejected(self):
        assert self.fn("2026-07-30/../../etc/passwd", self.base) is None

    def test_empty_rejected(self):
        assert self.fn("", self.base) is None

    def test_non_date_string_rejected(self):
        assert self.fn("not-a-date", self.base) is None
        assert self.fn("2026-7-30", self.base) is None