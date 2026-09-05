"""
JS 桥接层（C/S 桌面模式专用）
=============================
pywebview 的 js_api 通道：前端 JS 直接调用本地 Python 处理器，
全程无 HTTP 服务、无端口占用、无防火墙弹窗。

与 service.py 共用同一套 handle_* 业务处理器，行为一致。
"""

from urllib.parse import urlparse, parse_qs

from service import (
    handle_analyze, handle_events, handle_faults,
    handle_log_types, handle_knowledge,
    handle_disk_overview, handle_disk_scan, handle_disk_scan_status,
    handle_disk_scan_cancel, handle_disk_cleanup, handle_disk_open_location,
    handle_disk_tree, handle_disk_drives,
    handle_appdata_scan, handle_appdata_drives,
    handle_appdata_migrate, handle_appdata_delete,
    handle_installer_scan,
)
from perf_service import (
    handle_perf_snapshot, handle_perf_record_start,
    handle_perf_record_status, handle_perf_record_stop,
    handle_perf_record_report, handle_perf_record_export,
)

# 路由表：前端请求路径 -> 业务处理器
ROUTES = {
    "/api/analyze": handle_analyze,
    "/api/events": handle_events,
    "/api/faults": handle_faults,
    "/api/log-types": handle_log_types,
    "/api/knowledge": handle_knowledge,
    # 磁盘清理
    "/api/disk/overview": handle_disk_overview,
    "/api/disk/scan": handle_disk_scan,
    "/api/disk/scan-status": handle_disk_scan_status,
    "/api/disk/scan-cancel": handle_disk_scan_cancel,
    "/api/disk/cleanup": handle_disk_cleanup,
    "/api/disk/open-location": handle_disk_open_location,
    "/api/disk/tree": handle_disk_tree,
    "/api/disk/drives": handle_disk_drives,
    # 应用数据盘点与迁移
    "/api/appdata/scan": handle_appdata_scan,
    "/api/appdata/drives": handle_appdata_drives,
    "/api/appdata/migrate": handle_appdata_migrate,
    "/api/appdata/delete": handle_appdata_delete,
    "/api/installers/scan": handle_installer_scan,
    # 性能分析
    "/api/perf/snapshot": handle_perf_snapshot,
    "/api/perf/record-start": handle_perf_record_start,
    "/api/perf/record-status": handle_perf_record_status,
    "/api/perf/record-stop": handle_perf_record_stop,
    "/api/perf/record-report": handle_perf_record_report,
    "/api/perf/record-export": handle_perf_record_export,
}


class ApiBridge:
    """暴露给前端 window.pywebview.api 的桥接对象"""

    def call(self, path: str) -> dict:
        """
        统一入口：前端传入 '/api/analyze?type=System&max=1000' 形式的路径，
        分发到对应业务处理器并返回 dict（pywebview 自动转为 JS Promise）。
        """
        try:
            parsed = urlparse(path or "")
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            handler = ROUTES.get(parsed.path)
            if handler is None:
                return {"success": False, "error": f"未知接口: {parsed.path}"}
            return handler(params)
        except Exception as e:
            return {"success": False, "error": str(e)}
