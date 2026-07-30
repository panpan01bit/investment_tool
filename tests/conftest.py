"""conftest.py — make server-review subprojects importable without installing them."""
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
sys.path.insert(0, os.path.join(ROOT, "macro-bot"))
sys.path.insert(0, os.path.join(ROOT, "stock-tweet-bot"))

# Tests that need a tmp BRIEFINGS_DIR or .env should use the fixture in conftest.

# Ensure app.py can be imported for tests — its DB_PATH points to /www/wwwroot/...
# which doesn't exist locally. Provide a temp DB and temp BRIEFINGS_DIR via env
# before any test imports the app module.
_TMP_HOME = tempfile.mkdtemp(prefix="server-review-tests-")
os.environ.setdefault("BRIEFING_OUTPUT_DIR", os.path.join(_TMP_HOME, "briefings"))
os.makedirs(os.environ["BRIEFING_OUTPUT_DIR"], exist_ok=True)
# app.py reads BRIEFINGS_DIR via os.getenv; provide a temp DB via monkey-patching
# sqlite3.connect inside _init_db.