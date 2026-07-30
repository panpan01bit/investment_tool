"""Mock Feishu webhook server — PLACEHOLDER for real Feishu webhook.

Returns 200 with {"code": 0, "msg": "ok"} for any POST, logs the payload to /tmp/mock_feishu.log.

Why this exists:
- The real Feishu webhook URL has never been committed to the repo (security).
- The smoke test requires send_feishu() to return code=0 to validate the full pipeline.
- This mock lets us verify the bot builds and posts a valid payload without leaking data.

REMOVE THIS FILE and update FEISHU_WEBHOOK in .env with the real URL before production.
"""
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG_PATH = "/tmp/mock_feishu.log"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().isoformat()} {self.path} ===\n")
            f.write(body)
            f.write("\n")
        resp = json.dumps({"code": 0, "msg": "ok", "data": {"ok": True}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    print(f"mock_feishu listening on 127.0.0.1:{port}, log -> {LOG_PATH}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
