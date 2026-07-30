from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sys, os
from dotenv import load_dotenv

load_dotenv('/www/wwwroot/macro-bot/.env')

sys.path.insert(0, '/www/wwwroot/macro-bot')
import app as original_app_module

# 用 app.py 里的 Flask app 作为基底，自动带上所有 /api/* 路由（包括 auth）
app = original_app_module.app
# 添加静态文件支持（用于 spa fallback）
app.static_folder = '/www/wwwroot/guanlan'
app.static_url_path = ''
CORS(app, supports_credentials=True)


# 把 catch_all 路由接入（app.py 里也有一个 "/" index，优先用这里的 spa fallback）
try:
    app.add_url_rule(
        '/<path:path>',
        'catch_all_spa',
        view_func=lambda path: send_from_directory('/www/wwwroot/guanlan', 'index.html'),
    )
except Exception as e:
    print('spa catch_all add err:', e)
# root
try:
    app.add_url_rule(
        '/',
        'spa_root',
        view_func=lambda: send_from_directory('/www/wwwroot/guanlan', 'index.html'),
    )
except Exception as e:
    pass

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)
