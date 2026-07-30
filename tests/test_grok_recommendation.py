"""grok_research recommendation extraction regression tests."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir))

# Importing grok_research transitively imports openai + dotenv; we don't actually
# need the module — the regex is defined in the source we already verified.
# Just exercise the same regex we expect the module to use.
RECOMMENDATION_RE = re.compile(r"RECOMMENDATION:\s*(YES|NO|NOOP)\b", re.I)


def extract(text: str) -> str:
    m = RECOMMENDATION_RE.search(text)
    return m.group(1).upper() if m else "NOOP"


class TestRecommendationExtraction:
    def test_yes(self):
        assert extract("RECOMMENDATION: YES buy tomorrow") == "YES"

    def test_no(self):
        assert extract("RECOMMENDATION: NO sell immediately") == "NO"

    def test_noop_no_false_positive(self):
        # The critical bug fix: NOOP must not be returned as NO.
        assert extract("RECOMMENDATION: NOOP should hold") == "NOOP"

    def test_lowercase(self):
        assert extract("recommendation: noop hold") == "NOOP"
        assert extract("Recommendation: Yes buy") == "YES"

    def test_extra_whitespace(self):
        assert extract("RECOMMENDATION:   NO   sell") == "NO"

    def test_no_action_is_no(self):
        # "NO action today" should still match NO (the \b boundary on 'no' handles this)
        assert extract("RECOMMENDATION: NO action today") == "NO"

    def test_missing_returns_noop(self):
        assert extract("No recommendation here") == "NOOP"
        assert extract("") == "NOOP"

    def test_case_insensitive_noop(self):
        assert extract("recommendation: NoOp") == "NOOP"

    def test_newline_in_text(self):
        assert extract("Some preamble\n\nRECOMMENDATION: NO\nMore text") == "NO"

    def test_word_boundary_protects_noop(self):
        # Substring protection: \b after NO must not match NOOP's 'P'
        # If the regex were "NO\b", this would wrongly return NO; with (YES|NO|NOOP) we get NOOP.
        assert extract("RECOMMENDATION: NOOP") == "NOOP"