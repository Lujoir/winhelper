"""
服务层 — 业务处理器（桌面模式 JS 桥接的唯一入口）
==================================================
原 B/S Web 模式已移除；本模块承载与传输协议无关的全部业务处理器，
由 bridge.py（pywebview js_api）直接调用。

入参: params dict;  出参: 可JSON序列化的 dict

注：日志诊断处理器已由独立的 log_service.py 承接（菜单合并 ADR-001），
旧 handle_analyze/handle_events/handle_faults/handle_log_types/handle_knowledge
随 /api/analyze 等旧路由一并移除。
"""

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
