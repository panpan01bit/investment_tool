import os
import sys
import tempfile
import shutil
from datetime import datetime
from flask import request, jsonify

# 持仓表上传接口：管理员上传 holdings.xlsx 覆盖原文件
HOLDINGS_TARGET_PATH = os.getenv("HOLDINGS_FILE", "/www/wwwroot/macro-bot/holdings.xlsx")


def upload_holdings_route(app, _auth_require):
    @app.route("/api/upload-holdings", methods=["POST"])
    def upload_holdings():
        """接收管理员上传的 Excel，覆盖 holdings.xlsx。

        Body: multipart/form-data with field 'file' (.xlsx / .xls / .csv)
        Header: X-Auth-Token or Cookie auth_token
        """
        auth = _auth_require()
        if not auth:
            return jsonify({"error": "未登录"}), 401
        if auth["user"]["role"] != "admin":
            return jsonify({"error": "需要管理员权限"}), 403

        if "file" not in request.files:
            return jsonify({"error": "缺少 file 字段"}), 400

        f = request.files["file"]
        if f.filename == "":
            return jsonify({"error": "文件名不能为空"}), 400

        allowed = (".xlsx", ".xls", ".csv")
        if not f.filename.lower().endswith(allowed):
            return jsonify({"error": "仅支持 .xlsx/.xls/.csv"}), 400

        ext = os.path.splitext(f.filename)[1].lower()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        tmp_path = tmp.name
        try:
            f.save(tmp_path)
        except Exception as e:
            return jsonify({"error": "保存上传文件失败: %s" % e}), 500
        finally:
            tmp.close()

        # 验证：必须能解析且至少有一条持仓
        try:
            sys.path.insert(0, os.path.dirname(HOLDINGS_TARGET_PATH))
            import holdings
            rows = holdings.load_holdings(file_path=tmp_path, log=print)
            if rows is None:
                return jsonify({"error": "解析失败：文件格式不符合要求（需要至少包含 Ticker、Exchange 列）"}), 400
            if len(rows) == 0:
                return jsonify({"error": "未找到有效持仓（Ticker 为空或 Run!=Y）"}), 400
            invalid = [r for r in rows[:10] if not r.get("ticker") or not r.get("exchange")]
            if invalid:
                return jsonify({"error": "前 10 行中有 Ticker 或 Exchange 为空"}), 400
        except Exception as e:
            return jsonify({"error": "解析异常: %s" % e}), 400

        # 备份原文件
        backup_path = HOLDINGS_TARGET_PATH + ".bak." + datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            if os.path.exists(HOLDINGS_TARGET_PATH):
                shutil.copy2(HOLDINGS_TARGET_PATH, backup_path)
        except Exception as e:
            print("[upload-holdings] 备份原文件失败:", e)

        # 覆盖目标文件
        try:
            shutil.move(tmp_path, HOLDINGS_TARGET_PATH)
        except Exception as e:
            return jsonify({"error": "覆盖目标文件失败: %s" % e}), 500

        # 重新读取验证
        try:
            rows = holdings.load_holdings(file_path=HOLDINGS_TARGET_PATH, log=print)
        except Exception as e:
            rows = []
            print("[upload-holdings] 覆盖后重新读取失败:", e)

        return jsonify({
            "ok": True,
            "path": HOLDINGS_TARGET_PATH,
            "backup": backup_path,
            "count": len(rows) if rows else 0,
            "filename": f.filename,
        })
