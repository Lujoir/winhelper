"""
Disk Cleaner — C盘磁盘清理与应用数据迁移（独立项目）
====================================================
独立 Flask 入口: python app.py  →  http://127.0.0.1:5010

API 一览:
  磁盘清理   /api/disk/overview | scan | scan-status | scan-cancel |
             cleanup | open-location | tree | drives
  应用数据   /api/appdata/scan | drives | migrate | delete
  安装包     /api/installers/scan
"""

from flask import Flask, send_from_directory, request, jsonify

from disk_cleanup import (
    handle_disk_overview, handle_disk_scan, handle_disk_scan_status,
    handle_disk_scan_cancel, handle_disk_cleanup, handle_disk_open_location,
    handle_disk_tree, handle_disk_drives,
)
from appdata_scan import (
    handle_appdata_scan, handle_appdata_drives,
    handle_appdata_migrate, handle_appdata_delete,
    handle_installer_scan,
)

app = Flask(__name__, static_folder="web", static_url_path="")
app.config["SECRET_KEY"] = "disk-cleaner-standalone"


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---- 磁盘清理 ----
@app.route("/api/disk/overview")
def api_disk_overview():
    return jsonify(handle_disk_overview(request.args.to_dict()))


@app.route("/api/disk/scan")
def api_disk_scan():
    return jsonify(handle_disk_scan(request.args.to_dict()))


@app.route("/api/disk/scan-status")
def api_disk_scan_status():
    return jsonify(handle_disk_scan_status(request.args.to_dict()))


@app.route("/api/disk/scan-cancel")
def api_disk_scan_cancel():
    return jsonify(handle_disk_scan_cancel(request.args.to_dict()))


@app.route("/api/disk/cleanup")
def api_disk_cleanup():
    return jsonify(handle_disk_cleanup(request.args.to_dict()))


@app.route("/api/disk/open-location")
def api_disk_open_location():
    return jsonify(handle_disk_open_location(request.args.to_dict()))


@app.route("/api/disk/tree")
def api_disk_tree():
    return jsonify(handle_disk_tree(request.args.to_dict()))


@app.route("/api/disk/drives")
def api_disk_drives():
    return jsonify(handle_disk_drives(request.args.to_dict()))


# ---- 应用数据盘点与迁移 ----
@app.route("/api/appdata/scan")
def api_appdata_scan():
    return jsonify(handle_appdata_scan(request.args.to_dict()))


@app.route("/api/appdata/drives")
def api_appdata_drives():
    return jsonify(handle_appdata_drives(request.args.to_dict()))


@app.route("/api/appdata/migrate")
def api_appdata_migrate():
    return jsonify(handle_appdata_migrate(request.args.to_dict()))


@app.route("/api/appdata/delete")
def api_appdata_delete():
    return jsonify(handle_appdata_delete(request.args.to_dict()))


# ---- 安装包清理 ----
@app.route("/api/installers/scan")
def api_installer_scan():
    return jsonify(handle_installer_scan(request.args.to_dict()))


if __name__ == "__main__":
    print("Disk Cleaner 独立服务: http://127.0.0.1:5010")
    app.run(host="127.0.0.1", port=5010, debug=True, threaded=True)
