from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sys, os
from flask import abort
from dotenv import load_dotenv

load_dotenv('/www/wwwroot/macro-bot/.env')

sys.path.insert(0, '/www/wwwroot/macro-bot')
import app as original_app_module

CORS_ORIGINS = os.getenv('STATIC_CORS_ORIGINS', 'https://panpan.ink').split(',')

# 用 app.py 里的 Flask app 作为基底，自动带上所有 /api/* 路由（包括 auth）
app = original_app_module.app
# 添加静态文件支持（用于 spa fallback）
STATIC_DIR = os.getenv('STATIC_DIR', '/www/wwwroot/guanlan')
app.static_folder = STATIC_DIR
app.static_url_path = ''
# CORS is already configured once in app.py; do not double-register here.

def spa_fallback(path):
    # API 路由更具体，这里不应匹配到 /api/*；保留防御性 404
    if path.startswith('api/'):
        abort(404)
    # 路径安全规范化后映射到静态目录
    safe = os.path.normpath('/' + path).lstrip('/')
    # 拒绝路径遍历（URL 路径中显式出现 .. 段）
    if any(part == '..' for part in safe.split('/')):
        abort(404)
    full = os.path.join(STATIC_DIR, safe)
    # 再次确保落在 STATIC_DIR 内（针对规范化后仍越界的 corner case）
    if os.path.commonpath([os.path.abspath(full), STATIC_DIR]) != STATIC_DIR:
        abort(404)
    if os.path.isfile(full):
        return send_from_directory(STATIC_DIR, safe)
    # 其余前端路由 fallback 到 index.html
    return send_from_directory(STATIC_DIR, 'index.html')

# catch_all 兜底 SPA 路由（优先级低于 /api/* 和具体静态文件）
try:
    app.add_url_rule(
        '/<path:path>',
        'catch_all_spa',
        view_func=spa_fallback,
    )
except Exception as e:
    print('spa catch_all add err:', e)

if __name__ == '__main__':
    port = int(os.getenv('STATIC_SERVER_PORT', '8001'))
    app.run(host='0.0.0.0', port=port, debug=False)
