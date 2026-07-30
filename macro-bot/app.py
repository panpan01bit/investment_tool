# -*- coding: utf-8 -*-
"""
app.py - FastAPI backend for macro-bot Q&A & conversation history
Python 3.6+ compatible

Endpoints:
  GET  /api/briefings          - List all briefing dates
  GET  /api/briefings/{date}   - Get briefing by date (2026-07-13)
  POST /api/chat               - Ask follow-up question
  GET  /api/chat/{session_id}  - Get conversation history
"""

from __future__ import print_function
import os
import sys
import json
import sqlite3
import hashlib
import time
import re
from datetime import datetime

# FastAPI not available on Python 3.6, use Flask instead
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ===== Config =====
BRIEFINGS_DIR = os.getenv("BRIEFING_OUTPUT_DIR", "/www/wwwroot/macro-bot/briefings")
DB_PATH = os.getenv("CHAT_DB_PATH", "/www/wwwroot/macro-bot/chat_history.db")
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ===== Helpers =====
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date_str(date: str) -> bool:
    """Strict YYYY-MM-DD validation (rejects 2026-99-99 etc.)."""
    if not _DATE_RE.match(date):
        return False
    try:
        datetime.strptime(date, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _safe_briefing_path(date):
    """Validate date format and return a path guaranteed to be inside BRIEFINGS_DIR."""
    if not _valid_date_str(date):
        return None
    path = os.path.join(BRIEFINGS_DIR, "%s.json" % date)
    real = os.path.realpath(path)
    real_dir = os.path.realpath(BRIEFINGS_DIR)
    if not real.startswith(real_dir + os.sep):
        return None
    return real


# ===== DB Setup =====
def _init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            session_id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            ticker TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES conversations(session_id)
        )
    """)
    conn.commit()
    conn.close()

_init_db()

# ===== Helpers =====
def _now():
    return datetime.utcnow().isoformat() + "Z"


def _call_kimi(messages, max_tokens=1500):
    """Call Kimi API with messages list."""
    import requests
    headers = {
        "Authorization": "Bearer %s" % KIMI_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "model": "kimi-k2.5",
        "messages": messages,
        "max_tokens": max_tokens,
    }
    try:
        print("[DEBUG] KIMI_KEY prefix:", KIMI_API_KEY[:12])
        print("[DEBUG] Request URL:", "%s/chat/completions" % KIMI_BASE_URL)
        print("[DEBUG] Headers:", headers)
        resp = requests.post(
            "%s/chat/completions" % KIMI_BASE_URL,
            headers=headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return "[ERROR] Kimi call failed: %s" % e


def _call_tavily_search(query):
    """Call Tavily for real-time web search."""
    if not TAVILY_API_KEY:
        return None
    import requests
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        answer = data.get("answer", "")
        results = data.get("results", [])
        snippets = []
        for r in results[:3]:
            t = r.get("title", "")
            c = r.get("content", "")[:200]
            snippets.append("%s: %s" % (t, c))
        return "\n".join([answer] + snippets)
    except Exception as e:
        return None


# ===== API Routes =====

@app.route("/api/briefings", methods=["GET"])
def list_briefings():
    """List all available briefing dates."""
    try:
        files = sorted([f.replace(".json", "") for f in os.listdir(BRIEFINGS_DIR)
                        if f.endswith(".json")], reverse=True)
        return jsonify({"dates": files, "count": len(files)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/briefings/<date>", methods=["GET"])
def get_briefing(date):
    """Get briefing JSON by date."""
    path = _safe_briefing_path(date)
    if path is None:
        return jsonify({"error": "invalid date"}), 400
    if not os.path.exists(path):
        return jsonify({"error": "Not found"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def chat():
    """
    POST /api/chat
    Body: {"date": "2026-07-13", "ticker": "AAPL", "question": "...", "session_id": "optional"}
    """
    data = request.get_json() or {}
    date = data.get("date", "")
    ticker = data.get("ticker", "")
    question = data.get("question", "").strip()
    session_id = data.get("session_id", "")

    if not question:
        return jsonify({"error": "question required"}), 400

    # Generate or reuse session_id
    if not session_id:
        session_id = hashlib.md5(("%s|%s|%s|%s" % (date, ticker, question[:20], time.time())).encode()).hexdigest()[:16]

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Check if session exists
    c.execute("SELECT 1 FROM conversations WHERE session_id = ?", (session_id,))
    exists = c.fetchone()
    now = _now()
    if not exists:
        c.execute(
            "INSERT INTO conversations (session_id, date, ticker, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, date, ticker, now, now),
        )
    else:
        c.execute("UPDATE conversations SET updated_at = ? WHERE session_id = ?", (now, session_id))

    # Save user message
    c.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (session_id, "user", question, now),
    )

    # Build context: previous messages + briefing content + Tavily search
    c.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at LIMIT 20",
        (session_id,),
    )
    history = [{"role": row[0], "content": row[1]} for row in c.fetchall()]

    # Load briefing as context
    briefing_text = ""
    briefing_path = _safe_briefing_path(date)
    if briefing_path and os.path.exists(briefing_path):
        try:
            with open(briefing_path, "r", encoding="utf-8") as f:
                b = json.load(f)
            briefing_text = b.get("summary", "")[:1500]
        except Exception:
            pass

    # Tavily search for real-time info
    search_context = ""
    if ticker:
        search_query = "%s stock news market %s" % (ticker, date)
    else:
        search_query = "macro market news %s" % date
    tavily_result = _call_tavily_search(search_query)
    if tavily_result:
        search_context = "\n\n[Real-time search results]:\n%s" % tavily_result[:800]

    # Build system prompt
    system_prompt = (
        "You are a senior macro analyst at a buy-side fund. "
        "You are answering a follow-up question from the portfolio manager about a daily briefing. "
        "Use the briefing context below + real-time search results to answer. "
        "Be concise (under 200 words), specific, and cite sources when possible. "
        "If discussing major news, state the news first, then its impact on the covered company. "
        "Focus on company-specific catalysts and market implications.\n\n"
        "[Briefing Context]:\n%s\n%s" % (briefing_text, search_context)
    )

    messages = [{"role": "system", "content": system_prompt}]
    # Add history (excluding the just-inserted user message which is already in history)
    for msg in history:
        messages.append(msg)

    # Call LLM
    answer = _call_kimi(messages)

    # Save assistant message
    c.execute(
        "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (session_id, "assistant", answer, _now()),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "session_id": session_id,
        "answer": answer,
        "has_search": bool(tavily_result),
    })


@app.route("/api/chat/<session_id>", methods=["GET"])
def get_chat_history(session_id):
    """Get conversation history by session_id."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY created_at",
        (session_id,),
    )
    messages = [{"role": row[0], "content": row[1], "time": row[2]} for row in c.fetchall()]
    conn.close()
    return jsonify({"session_id": session_id, "messages": messages})


@app.route("/api/stats", methods=["GET"])
def stats():
    """Simple stats endpoint."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM conversations")
    n_sessions = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM messages")
    n_messages = c.fetchone()[0]
    conn.close()
    return jsonify({"sessions": n_sessions, "messages": n_messages})


# ============================================================
# Auth 模块：基于服务器端数据库的用户认证（替代 localStorage）
# ============================================================
import secrets
from werkzeug.security import generate_password_hash, check_password_hash

# token 有效天数
AUTH_TOKEN_TTL_DAYS = int(os.getenv("AUTH_TOKEN_TTL_DAYS", "30"))
# 初始 admin（首次启动时 seed）
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def _init_users_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            expires_at INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            created_by TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS auth_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()


_init_users_db()


def _seed_admin():
    if not ADMIN_PASSWORD:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    n = c.fetchone()[0]
    if n > 0:
        conn.close()
        return
    h = generate_password_hash(ADMIN_PASSWORD)
    try:
        c.execute(
            "INSERT INTO users (username, password_hash, role, expires_at, created_at, created_by) VALUES (?, ?, 'admin', 0, ?, 'system')",
            (ADMIN_USERNAME, h, _now()),
        )
        conn.commit()
        print("[auth] Seeded admin user:", ADMIN_USERNAME)
    except sqlite3.IntegrityError:
        pass
    conn.close()


_seed_admin()


@app.route("/api/auth/users", methods=["GET"])
def auth_list_users():
    """管理员可查看所有用户列表。"""
    auth = _auth_require()
    if not auth:
        return jsonify({"error": "未登录"}), 401
    if auth["user"]["role"] != "admin":
        return jsonify({"error": "需要管理员权限"}), 403
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username, role, expires_at, created_at, created_by FROM users ORDER BY id")
    rows = c.fetchall()
    conn.close()
    users = [
        {
            "id": r[0],
            "username": r[1],
            "role": r[2],
            "expiresAt": r[3],
            "createdAt": r[4],
            "createdBy": r[5] or "",
        }
        for r in rows
    ]
    return jsonify({"users": users})


@app.route("/api/auth/users", methods=["POST"])
def auth_create_user():
    """管理员新增用户。"""
    auth = _auth_require()
    if not auth:
        return jsonify({"error": "未登录"}), 401
    if auth["user"]["role"] != "admin":
        return jsonify({"error": "需要管理员权限"}), 403
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") or "user"
    expires_days = int(data.get("expires_days") or 30)
    if not username:
        return jsonify({"error": "用户名不能为空"}), 400
    if not password or len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    if role not in ("admin", "user"):
        return jsonify({"error": "角色不合法"}), 400
    expires_at = 0 if expires_days <= 0 else int(time.time() * 1000) + expires_days * 86400000
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO users (username, password_hash, role, expires_at, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (username, generate_password_hash(password), role, expires_at, _now(), auth["user"]["username"]),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "用户名已存在"}), 400
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/auth/users/<int:user_id>", methods=["DELETE"])
def auth_delete_user(user_id):
    """管理员删除用户（不能删自己）。"""
    auth = _auth_require()
    if not auth:
        return jsonify({"error": "未登录"}), 401
    if auth["user"]["role"] != "admin":
        return jsonify({"error": "需要管理员权限"}), 403
    if int(auth["user"]["id"]) == user_id:
        return jsonify({"error": "不能删除当前登录的账号"}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM users WHERE id = ?", (user_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if not deleted:
        return jsonify({"error": "用户不存在"}), 404
    return jsonify({"ok": True})



def _auth_get_user_by_token(token):
    if not token:
        return None
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT t.user_id, t.expires_at, u.username, u.role FROM auth_tokens t JOIN users u ON u.id = t.user_id WHERE t.token = ?",
        (token,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    user_id, expires_at, username, role = row
    if expires_at > 0 and int(time.time() * 1000) > expires_at:
        return None
    return {"id": user_id, "username": username, "role": role}


def _auth_require():
    token = (
        request.headers.get("X-Auth-Token", "")
        or request.cookies.get("auth_token", "")
        or (request.get_json(silent=True) or {}).get("token", "")
    )
    user = _auth_get_user_by_token(token)
    if not user:
        return None
    return {"token": token, "user": user}


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not username or not password:
        return jsonify({"error": "username 和 password 不能为空"}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT id, password_hash, role, expires_at FROM users WHERE username = ?",
        (username,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "用户名或密码错误"}), 401
    user_id, pwd_hash, role, user_expires_at = row

    if user_expires_at > 0 and int(time.time() * 1000) > user_expires_at:
        return jsonify({"error": "账户已过期，请联系管理员"}), 403

    if not check_password_hash(pwd_hash, password):
        return jsonify({"error": "用户名或密码错误"}), 401

    token = secrets.token_urlsafe(32)
    now_ms = int(time.time() * 1000)
    expires_at = now_ms + AUTH_TOKEN_TTL_DAYS * 86400000
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO auth_tokens (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now_ms, expires_at),
    )
    conn.commit()
    conn.close()

    resp = jsonify({
        "token": token,
        "user": {"id": user_id, "username": username, "role": role},
        "expires_at": expires_at,
    })
    resp.set_cookie(
        "auth_token",
        token,
        max_age=AUTH_TOKEN_TTL_DAYS * 86400,
        httponly=False,
        samesite="Lax",
    )
    return resp


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    data = request.get_json(silent=True) or {}
    token = (
        data.get("token")
        or request.headers.get("X-Auth-Token", "")
        or request.cookies.get("auth_token", "")
    )
    if token:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM auth_tokens WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    resp = jsonify({"ok": True})
    resp.delete_cookie("auth_token")
    return resp


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    auth = _auth_require()
    if not auth:
        return jsonify({"error": "未登录"}), 401
    return jsonify({"user": auth["user"]})


@app.route("/api/auth/seed-admin", methods=["GET"])
def auth_seed_admin_status():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    n = c.fetchone()[0]
    conn.close()
    return jsonify({"admin_seeded": n > 0, "admin_username": ADMIN_USERNAME if n == 0 else None})


@app.route("/api/auth/change-password", methods=["POST"])
def auth_change_password():
    auth = _auth_require()
    if not auth:
        return jsonify({"error": "未登录"}), 401
    data = request.get_json(silent=True) or {}
    new_pwd = data.get("new_password") or ""
    if not new_pwd or len(new_pwd) < 6:
        return jsonify({"error": "新密码至少 6 位"}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_pwd), auth["user"]["id"]),
    )
    c.execute("DELETE FROM auth_tokens WHERE user_id = ?", (auth["user"]["id"],))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "密码已更新，请重新登录"})


@app.route("/")
def index():
    return app.send_static_file("index.html")



# 持仓上传接口
import upload_holdings_module
upload_holdings_module.upload_holdings_route(app, _auth_require)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)

# ============================================================
# 听涛早报接口
# ============================================================
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")


def _require_internal_secret():
    """Worker 回源时必须带 X-Internal-Secret，防止 origin 域名被嗅探。"""
    if INTERNAL_SECRET == "DISABLED":
        return True
    if not INTERNAL_SECRET:
        return False
    secret = request.headers.get("X-Internal-Secret", "")
    if not secret:
        return False
    return secrets.compare_digest(secret, INTERNAL_SECRET)


def _get_latest_date():
    """从 briefings 目录找到最新日期。"""
    try:
        files = [f for f in os.listdir(BRIEFINGS_DIR) if f.endswith(".json")]
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        dates = sorted([f.replace(".json", "") for f in files if date_pattern.match(f.replace(".json", ""))], reverse=True)
        return dates[0] if dates else None
    except Exception as e:
        print("[tingtao] get_latest_date error:", e)
        return None


@app.route("/api/tingtao/manifest", methods=["GET"])
def tingtao_manifest():
    if not _require_internal_secret():
        return jsonify({"error": "forbidden"}), 403
    try:
        files = [f for f in os.listdir(BRIEFINGS_DIR) if f.endswith(".json")]
        dates = sorted([f.replace(".json", "") for f in files if re.compile(r"^\d{4}-\d{2}-\d{2}$").match(f.replace(".json", ""))], reverse=True)
        return jsonify({"dates": dates, "count": len(dates)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tingtao/latest", methods=["GET"])
def tingtao_latest():
    if not _require_internal_secret():
        return jsonify({"error": "forbidden"}), 403
    date = request.args.get("date", "").strip()
    if not date:
        date = _get_latest_date()
    elif not re.compile(r"^\d{4}-\d{2}-\d{2}$").match(date):
        date = _get_latest_date()
    if not date:
        return jsonify({"found": False, "error": "no briefing available"}), 404

    json_path = os.path.join(BRIEFINGS_DIR, "%s.json" % date)
    md_path = os.path.join(BRIEFINGS_DIR, "%s.md" % date)
    if not os.path.exists(json_path):
        return jsonify({"found": False, "error": "not found"}), 404

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    md = ""
    if os.path.exists(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                md = f.read()
        except Exception as e:
            print("[tingtao] read md error:", e)

    if not isinstance(data.get("chat_history"), list):
        data["chat_history"] = []

    return jsonify({
        "found": True,
        "id": date,
        "date": data.get("date", date),
        "title": data.get("title", "听涛日报") + (" · %s" % date if data.get("date") else ""),
        "content": md or data.get("summary", "") or data.get("content", ""),
        "sources": data.get("sources", ["macro-bot"]),
        "chat_history": data["chat_history"],
    })


@app.route("/api/tingtao/chat", methods=["POST"])
def tingtao_chat():
    if not _require_internal_secret():
        return jsonify({"error": "forbidden"}), 403

    body = request.get_json(silent=True) or {}
    date = body.get("date", "").strip()
    message = body.get("message", "").strip()
    role = body.get("role", "user").strip()
    if not date or not re.compile(r"^\d{4}-\d{2}-\d{2}$").match(date):
        return jsonify({"error": "invalid date"}), 400
    if not message:
        return jsonify({"error": "empty message"}), 400

    json_path = os.path.join(BRIEFINGS_DIR, "%s.json" % date)
    if not os.path.exists(json_path):
        return jsonify({"error": "daily file not found"}), 404

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"error": "invalid json: %s" % e}), 500

    if not isinstance(data.get("chat_history"), list):
        data["chat_history"] = []

    entry = {
        "id": "%s-%s" % (date, int(time.time() * 1000)),
        "role": role if role in ("user", "assistant") else "user",
        "content": message,
        "created_at": _now(),
    }
    data["chat_history"].append(entry)

    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({"error": "write failed: %s" % e}), 500

    return jsonify({
        "success": True,
        "entry": entry,
        "chat_history": data["chat_history"],
    })
