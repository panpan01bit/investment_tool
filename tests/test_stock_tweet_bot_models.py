"""stock-tweet-bot warn-only model whitelist regression tests."""
import io
import os
import sys
import contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "stock-tweet-bot"))

import fetch  # noqa: E402


class FakeResponse:
    status_code = 200
    def raise_for_status(self):
        pass
    def json(self):
        return {}


def run_call(model_name, captured):
    cfg = {"xai": {"model": model_name, "api_key": "fake"}}
    real_post = fetch.requests.post

    def fake_post(url, *a, **kw):
        captured["called_with"] = kw.get("json", {}).get("model")
        return FakeResponse()

    fetch.requests.post = fake_post
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            try:
                fetch.call_xai_search("test prompt", cfg, timeout=1, retries=0)
            except Exception:
                pass
    finally:
        fetch.requests.post = real_post
    return err.getvalue()


class TestKnownModelsWarning:
    def test_known_model_passes_through(self):
        captured = {}
        stderr_text = run_call("grok-4-1-fast-non-reasoning", captured)
        assert captured["called_with"] == "grok-4-1-fast-non-reasoning"
        assert "warning" not in stderr_text

    def test_unknown_model_warns_and_passes_through(self):
        captured = {}
        stderr_text = run_call("grok-4.5", captured)
        # Must NOT be silently overridden
        assert captured["called_with"] == "grok-4.5"
        assert "warning" in stderr_text
        assert "grok-4.5" in stderr_text

    def test_unknown_future_model_warns_but_passes(self):
        # Ensures the warn-only contract holds for arbitrary new model names
        captured = {}
        stderr_text = run_call("grok-5-ultra", captured)
        assert captured["called_with"] == "grok-5-ultra"
        assert "warning" in stderr_text

    def test_other_known_models(self):
        for m in ["grok-4-1-fast-reasoning", "grok-4-fast-reasoning", "grok-4-fast-non-reasoning"]:
            captured = {}
            stderr_text = run_call(m, captured)
            assert captured["called_with"] == m, f"{m} got overridden"
            assert "warning" not in stderr_text, f"{m} should not warn"