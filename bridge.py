"""
JS 桥接层（C/S 桌面模式专用）
=============================
pywebview 的 js_api 通道：前端 JS 直接调用本地 Python 处理器，
全程无 HTTP 服务、无端口占用、无防火墙弹窗。

与 service.py 共用同一套 handle_* 业务处理器，行为一致。
"""

from urllib.parse import urlparse, parse_qs

import json

from service import (
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
    handle_perf_stress_start, handle_perf_stress_status,
    handle_perf_stress_cancel, handle_perf_stress_export,
    handle_perf_hwinfo, handle_perf_temps, handle_perf_restart_admin,
    handle_perf_app_config,
)
from log_service import (
    handle_log_access, handle_log_search,
    handle_log_export_start, handle_log_export_status, handle_log_export_cancel,
    handle_log_analyze, handle_log_report_export, handle_log_knowledge,
)
from uplink import (
    handle_uplink_status, handle_uplink_save, handle_uplink_register,
    autostart as uplink_autostart,
)
from home_service import handle_home_network
from file_search import (
    handle_fs_query, handle_fs_status, handle_fs_stats, handle_fs_open_location,
)

from net_service import (
    handle_net_config, handle_net_config_check, handle_net_ipconflict,
    handle_net_conflict_deep_start, handle_net_conflict_deep_poll,
    handle_net_conflict_ai_reanalyze, handle_net_trace_ai_analyze,
    handle_net_ai_history, handle_net_ai_history_append, handle_net_ai_history_delete,
    handle_net_ping_start, handle_net_ping_history, handle_net_tracert_start,
    handle_net_stress_start, handle_net_stress_export,
    handle_net_task_status, handle_net_task_cancel, handle_net_ai_diagnose,
    handle_net_ai_personal_test,
)
from desktop_policy import (
    handle_dp_status, handle_dp_policy_now, handle_dp_apply_now,
    handle_dp_task_status, handle_dp_logs,
    handle_dp_powercfg_read, handle_dp_powercfg_set,
)
from power_control import (
    handle_pc_snapshot, handle_pc_report, handle_pc_bios_apply,
    handle_pc_bios_restore, handle_pc_shutdown_set, handle_pc_shutdown_remove,
    handle_pc_shutdown_toggle, handle_pc_task_status,
)

# 路由表：前端请求路径 -> 业务处理器
ROUTES = {
    # 日志诊断
    "/api/loginspector/access": handle_log_access,
    "/api/loginspector/search": handle_log_search,
    "/api/loginspector/export-start": handle_log_export_start,
    "/api/loginspector/export-status": handle_log_export_status,
    "/api/loginspector/export-cancel": handle_log_export_cancel,
    "/api/loginspector/analyze": handle_log_analyze,
    "/api/loginspector/report-export": handle_log_report_export,
    "/api/loginspector/knowledge": handle_log_knowledge,
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
    "/api/perf/stress-start": handle_perf_stress_start,
    "/api/perf/stress-status": handle_perf_stress_status,
    "/api/perf/stress-cancel": handle_perf_stress_cancel,
    "/api/perf/stress-export": handle_perf_stress_export,
    "/api/perf/hwinfo": handle_perf_hwinfo,
    "/api/perf/temps": handle_perf_temps,
    "/api/perf/app-config": handle_perf_app_config,
    "/api/perf/restart-admin": handle_perf_restart_admin,
    # 平台接入（EyeTerm 服务端联动）
    "/api/perf/uplink/status": handle_uplink_status,
    "/api/perf/uplink/save": handle_uplink_save,
    "/api/perf/uplink/register": handle_uplink_register,
    # 主页（终端概览）
    "/api/home/network": handle_home_network,
    # 网络排障（net-doctor）
    "/api/netdoctor/config": handle_net_config,
    "/api/netdoctor/config-check": handle_net_config_check,
    "/api/netdoctor/ipconflict": handle_net_ipconflict,
    "/api/netdoctor/conflict-deep-start": handle_net_conflict_deep_start,
    "/api/netdoctor/conflict-deep-poll": handle_net_conflict_deep_poll,
    "/api/netdoctor/conflict-ai-reanalyze": handle_net_conflict_ai_reanalyze,
    "/api/netdoctor/trace-ai-analyze": handle_net_trace_ai_analyze,
    "/api/netdoctor/ai-history": handle_net_ai_history,
    "/api/netdoctor/ai-history-append": handle_net_ai_history_append,
    "/api/netdoctor/ai-history-delete": handle_net_ai_history_delete,
    # 文件检索（file-search 子系统，路线 C 自研索引引擎）
    "/api/filesearch/query": handle_fs_query,
    "/api/filesearch/status": handle_fs_status,
    "/api/filesearch/stats": handle_fs_stats,
    "/api/filesearch/open-location": handle_fs_open_location,

    "/api/netdoctor/ping-start": handle_net_ping_start,
    "/api/netdoctor/ping-history": handle_net_ping_history,
    "/api/netdoctor/tracert-start": handle_net_tracert_start,
    "/api/netdoctor/stress-start": handle_net_stress_start,
    "/api/netdoctor/stress-export": handle_net_stress_export,
    "/api/netdoctor/task-status": handle_net_task_status,
    "/api/netdoctor/task-cancel": handle_net_task_cancel,
    "/api/netdoctor/ai-diagnose": handle_net_ai_diagnose,
    "/api/netdoctor/ai-personal-test": handle_net_ai_personal_test,
    # 锁屏及壁纸管理（desktop-policy）
    "/api/desktoppolicy/status": handle_dp_status,
    "/api/desktoppolicy/policy-now": handle_dp_policy_now,
    "/api/desktoppolicy/apply-now": handle_dp_apply_now,
    "/api/desktoppolicy/task-status": handle_dp_task_status,
    "/api/desktoppolicy/logs": handle_dp_logs,
    "/api/desktoppolicy/powercfg/read": handle_dp_powercfg_read,
    "/api/desktoppolicy/powercfg/set": handle_dp_powercfg_set,
    # 自动开关机（power-control）
    "/api/powercontrol/snapshot": handle_pc_snapshot,
    "/api/powercontrol/report": handle_pc_report,
    "/api/powercontrol/bios-apply": handle_pc_bios_apply,
    "/api/powercontrol/bios-restore": handle_pc_bios_restore,
    "/api/powercontrol/shutdown-set": handle_pc_shutdown_set,
    "/api/powercontrol/shutdown-remove": handle_pc_shutdown_remove,
    "/api/powercontrol/shutdown-toggle": handle_pc_shutdown_toggle,
    "/api/powercontrol/task-status": handle_pc_task_status,
}


class ApiBridge:
    """暴露给前端 window.pywebview.api 的桥接对象"""

    def call(self, path: str, body: str = None) -> dict:
        """
        统一入口：前端传入 '/api/loginspector/search?page=1' 形式的路径，
        分发到对应业务处理器并返回 dict（pywebview 自动转为 JS Promise）。
        body（可选）：JSON 字符串，仅下列需要双参透传的路由使用
        （2026-09-09 AI 诊断增量；2026-09-16 桌面管控电源修改增量）：
        /api/netdoctor/ai-diagnose、/api/desktoppolicy/powercfg/set
        """
        try:
            parsed = urlparse(path or "")
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            handler = ROUTES.get(parsed.path)
            if handler is None:
                return {"success": False, "error": f"未知接口: {parsed.path}"}
            if parsed.path == "/api/netdoctor/ai-diagnose":
                try:
                    data = json.loads(body) if body else None
                except Exception:
                    data = None
                return handle_net_ai_diagnose(params, data)
            if parsed.path == "/api/desktoppolicy/powercfg/set":
                try:
                    data = json.loads(body) if body else {}
                except Exception:
                    data = {}
                return handle_dp_powercfg_set(data)
            # 自动开关机 P1：带 body 的路由双参透传（params + data）
            if parsed.path.startswith("/api/powercontrol/") and body:
                try:
                    data = json.loads(body) if body else None
                except Exception:
                    data = None
                return handler(params, data)
            return handler(params)
        except Exception as e:
            return {"success": False, "error": str(e)}


# 按配置自动恢复平台接入心跳（enabled=true 时；失败静默不崩主进程）
uplink_autostart()
