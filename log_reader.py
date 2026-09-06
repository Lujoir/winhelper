"""
Windows 系统事件日志读取引擎（Log Inspector 演进版）
=====================================================
自 winhelper 主应用 log_reader.py 迁入并增强（ADR-001）：
  1. iter_events(): 流式迭代读取（BACKWARDS_READ 逐批 + since/until 剪枝），
     大日志量禁止一次性载入内存（ADR-002）
  2. check_log_access(): Security 等类别权限探测（ADR-004）
  3. get_fault_analysis(): 支持输出证据样本 evidence（ADR-007）
"""

import datetime
from collections import defaultdict

import win32evtlog
import win32evtlogutil

# 事件严重级别定义
EVENT_LEVELS = {
    1: ("关键", "critical", "danger"),
    2: ("错误", "error", "error"),
    3: ("警告", "warning", "warning"),
    4: ("信息", "info", "info"),
    0: ("信息", "info", "info"),
}

# 事件日志源常量（含 Setup，ADR-001 四类别）
LOG_SOURCES = {
    "System": "系统日志",
    "Application": "应用程序日志",
    "Security": "安全日志",
    "Setup": "安装程序日志",
}

# 级别 label -> Windows EventType 数值（用于级别过滤）
LEVEL_FILTER_MAP = {"critical": 1, "error": 2, "warning": 3, "info": (0, 4)}


# 通用关键事件ID（Windows各版本通用）
CRITICAL_EVENT_IDS = {
    41: "系统意外重启（Kernel-Power）",
    55: "NTFS文件系统错误",
    56: "NTFS文件系统损坏",
    129: "磁盘控制器超时（重置）",
    134: "磁盘页面文件错误",
    1000: "应用程序错误",
    1001: "Windows错误报告",
    1003: "系统错误（蓝屏信息）",
    1005: "系统配置错误",
    1014: "DNS客户端解析错误",
    1015: "DNS客户端解析超时",
    1016: "网络连接断开",
    1017: "应用程序挂起",
    1022: "磁盘IO操作超时",
    1023: "磁盘IO操作失败",
    1024: "网络适配器重置",
    1025: "磁盘错误",
    1026: ".NET运行时错误",
    1027: "Windows更新错误",
    1034: "内存诊断错误",
    1035: "内存不足警告",
    1036: "虚拟内存不足",
    1037: "注册表空间不足",
    1038: "系统资源不足",
    1039: "磁盘空间不足",
    1040: "系统时间更改",
    1041: "系统策略阻止操作",
    1042: "服务启动超时",
    1043: "服务意外终止",
    1044: "驱动程序加载失败",
    1045: "驱动程序错误",
    1046: "硬件错误",
    1047: "系统固件错误",
    1058: "服务无法启动",
    1060: "服务未安装",
    1066: "服务依赖缺失",
    1069: "服务登录失败",
    1070: "服务启动超时",
    1071: "服务未响应",
    1074: "系统关机/重启（用户或系统）",
    1075: "服务依赖不存在",
    1076: "系统启动时间记录",
    1077: "服务启动失败",
    1078: "服务已停止",
    1079: "服务账户信息更改",
    1080: "服务状态变更",
    1081: "服务自动启动失败",
    1082: "服务手动启动失败",
    1083: "服务已禁用",
    1084: "服务无法在安全模式下启动",
    1085: "服务错误",
    1090: "服务配置错误",
    1091: "服务依赖服务停止",
    1092: "服务已暂停",
    1093: "服务恢复操作失败",
    1094: "服务组启动失败",
    1095: "服务控制管理器错误",
    1096: "服务未运行",
    1097: "服务标记为删除",
    1098: "服务孤立",
    1099: "服务数据库锁定",
    1100: "服务WMI提供程序错误",
    1101: "服务事件跟踪错误",
    1104: "服务卸载错误",
    1105: "服务已安装",
    1106: "服务已更改",
    1107: "服务已删除",
    1108: "服务权限错误",
    1109: "服务配置保存错误",
    1110: "服务对象已存在",
    1111: "服务硬件配置文件更改",
    1112: "服务依赖组不存在",
    1113: "服务依赖于以下组件",

    # 磁盘相关
    7: "磁盘错误（坏道/损坏）",
    11: "驱动程序错误",
    14: "磁盘存储错误",
    15: "磁盘已重新连接",
    50: "磁盘IO错误（页面文件）",
    51: "磁盘分页错误",
    52: "磁盘清理通知",
    53: "磁盘损坏通知",
    57: "磁盘/文件系统损坏",
    137: "磁盘错误",
    138: "磁盘错误已恢复",
    142: "磁盘逻辑错误",
    143: "磁盘物理错误",

    # 网络相关
    10000: "网络连接失败",
    10001: "DHCP获取失败",
    10002: "DNS解析失败",
    10003: "网络适配器断开",
    10004: "网络连接丢失",
    10005: "网络适配器故障",
    10006: "IP地址冲突",
    10007: "网络协议错误",
    10008: "网络访问被拒绝",
    10009: "网络超时",

    # 硬件相关
    10010: "CPU过热警告",
    10011: "CPU风扇故障",
    10012: "电源故障",
    10013: "内存故障",
    10014: "显卡故障",
    10015: "硬盘故障预警(SMART)",
    10016: "USB控制器故障",

    # 应用程序相关
    10017: "应用程序无响应",
    10018: "应用程序崩溃",
    10019: "应用程序安装失败",
    10020: "应用程序兼容性问题",
    10021: "驱动程序停止响应并已恢复",
    10022: "Windows更新安装失败",
    10023: "Windows Defender检测到威胁",
    10024: "用户账户控制通知",
    10025: "远程桌面连接失败",
}

# 故障模式分析规则
FAULT_PATTERNS = {
    "蓝屏崩溃": {
        "event_ids": [41, 1001, 1003, 10021],
        "sources": ["Kernel-Power", "BugCheck", "Microsoft-Windows-WER-SystemErrorReporting"],
        "keywords": ["blue screen", "bugcheck", "bug check", "0x000000", "unexpected shutdown", "系统崩溃", "蓝屏"],
        "severity": "critical",
        "suggestions": [
            "检查最近安装的驱动程序或更新，使用系统还原回滚",
            "运行内存诊断工具 (mdsched.exe) 检查内存故障",
            "检查硬盘健康状态 (chkdsk /f /r)",
            "检查散热系统，排除CPU/GPU过热导致崩溃",
            "查看C:\\Windows\\Minidump目录下的.dmp文件分析崩溃原因",
            "更新所有硬件驱动程序至最新稳定版本",
            "使用sfc /scannow 检查系统文件完整性",
        ],
    },
    "磁盘故障": {
        "event_ids": [7, 50, 51, 55, 56, 57, 129, 134, 137, 138, 142, 143, 1022, 1023, 1025],
        "sources": ["disk", "ntfs", "Ntfs", "Disk", "atapi", "iaStor", "storport"],
        "keywords": ["disk", "disk error", "坏道", "磁盘", "IO", "i/o", "timeout", "ntfs", "bad block"],
        "severity": "critical",
        "suggestions": [
            "立即备份重要数据，磁盘可能存在物理故障",
            "运行 chkdsk /f /r 检查和修复文件系统错误",
            "使用 CrystalDiskInfo 或 WMIC 检查硬盘SMART状态",
            "检查磁盘连接线缆是否松动",
            "如果使用SSD，检查固件更新",
            "考虑更换故障硬盘",
            "检查事件ID 129是否持续出现，可能表示磁盘控制器问题",
        ],
    },
    "内存不足": {
        "event_ids": [1034, 1035, 1036, 1037, 1038],
        "sources": ["Application Popup", "Application Hang", ".NET Runtime"],
        "keywords": ["memory", "out of memory", "内存不足", "虚拟内存", "分页", "page file", "virtual memory"],
        "severity": "warning",
        "suggestions": [
            "关闭不必要的应用程序和后台进程",
            "检查系统虚拟内存设置，建议为物理内存的1.5倍",
            "增加物理内存（RAM）",
            "检查是否有内存泄漏的应用程序（任务管理器查看内存占用持续增长）",
            "运行内存诊断工具检查是否有硬件故障",
            "减少开机自启动程序数量",
            "清理临时文件和系统缓存",
        ],
    },
    "服务异常": {
        "event_ids": [1042, 1043, 1058, 1060, 1066, 1069, 1070, 1071, 1077, 1078, 1081, 1082, 1083, 1091, 1094],
        "sources": ["Service Control Manager", "DCOM", "SvcHost"],
        "keywords": ["service", "服务", "failed to start", "timeout", "未响应", "启动失败"],
        "severity": "warning",
        "suggestions": [
            "检查服务依赖链，确保所有依赖服务已启动",
            "检查服务登录账户密码是否过期",
            "查看服务是否被禁用或删除",
            "检查系统资源是否充足（CPU、内存）",
            "修复或重新安装相关软件",
            "检查Windows是否有待重启的更新",
        ],
    },
    "网络故障": {
        "event_ids": [1014, 1015, 1016, 1024, 10000, 10001, 10002, 10003, 10004, 10005, 10006, 10007, 10009, 10025],
        "sources": ["Tcpip", "Dhcp-Client", "DnsApi", "DNS Client Events", "e1express", "netbt", "bowser", "MrxSmb"],
        "keywords": ["network", "网络", "dns", "dhcp", "ip", "连接", "connect", "timeout", "adapter"],
        "severity": "warning",
        "suggestions": [
            "运行网络诊断工具：ncpa.cpl → 右键网络适配器 → 诊断",
            "重置网络栈：netsh int ip reset && netsh winsock reset",
            "检查DHCP是否正常，尝试释放并更新IP：ipconfig /release && ipconfig /renew",
            "检查DNS设置，尝试使用公共DNS（8.8.8.8, 223.5.5.5）",
            "检查网卡驱动是否需要更新",
            "检查网络线缆或WiFi信号强度",
            "检查防火墙设置是否阻止了必要的连接",
            "检查IP地址是否冲突",
        ],
    },
    "应用程序崩溃": {
        "event_ids": [1000, 1017, 1026, 10017, 10018, 10019, 10020],
        "sources": ["Application Error", "Application Hang", ".NET Runtime", "Windows Error Reporting", "SideBySide"],
        "keywords": ["fault", "crash", "崩溃", "异常", "exception", "hang", "挂起", "无响应"],
        "severity": "error",
        "suggestions": [
            "重新安装故障应用程序",
            "检查应用程序是否有更新版本",
            "更新Microsoft Visual C++ Redistributable运行时库",
            "安装最新的.NET Framework",
            "检查应用程序与Windows版本兼容性",
            "以管理员身份运行该应用程序",
            "关闭冲突的杀毒软件或安全软件后重试",
        ],
    },
    "驱动程序问题": {
        "event_ids": [1044, 1045, 1046, 1047, 11, 10010, 10011, 10013, 10014, 10016],
        "sources": ["Microsoft-Windows-DriverFrameworks-UserMode", "e1cexpress", "nvlddmkm", "atikmdag", "iaStor", "dump", "WudfRd"],
        "keywords": ["driver", "驱动", "driver failed", "driver error", "reset", "停止响应"],
        "severity": "error",
        "suggestions": [
            "从设备制造商官网下载并安装最新驱动程序",
            "在设备管理器中卸载故障设备并重新扫描",
            "使用系统还原恢复到驱动更新前的状态",
            "检查Windows更新中是否有推荐的驱动程序更新",
            "使用Driver Verifier (verifier.exe) 检测问题驱动",
            "如果问题发生在显卡驱动，尝试在安全模式下卸载图形驱动",
        ],
    },
    "系统启动失败": {
        "event_ids": [1074, 1076, 1090, 1005],
        "sources": ["USER32", "EventLog", "Microsoft-Windows-Kernel-General"],
        "keywords": ["boot", "startup", "启动", "shutdown", "关机", "restart", "重启", "unexpected"],
        "severity": "critical",
        "suggestions": [
            "检查系统文件完整性：sfc /scannow",
            "检查磁盘错误：chkdsk /f /r",
            "检查启动配置数据：bcdedit /enum",
            "尝试进入安全模式排除问题",
            "使用系统还原回滚到最近的正常状态",
            "检查是否有Windows更新失败导致启动问题",
            "修复启动：使用Windows安装介质→修复计算机→启动修复",
        ],
    },
    "安全异常": {
        "event_ids": [10023, 10024, 1041],
        "sources": ["Microsoft-Windows-Windows Defender", "Microsoft-Windows-Security-Auditing", "Microsoft-Windows-User Account Control"],
        "keywords": ["threat", "病毒", "trojan", "malware", "malicious", "detected", "发现威胁", "remote", "rdp"],
        "severity": "error",
        "suggestions": [
            "立即运行Windows Defender全盘扫描",
            "检查最近安装的可疑软件",
            "查看安全日志中的登录尝试记录",
            "更改管理员密码",
            "检查远程桌面(RDP)是否暴露在公网",
            "检查防火墙规则是否有异常",
            "使用Microsoft Safety Scanner进行额外扫描",
            "检查计划的恶意软件删除工具 (MSRT) 运行结果",
        ],
    },
    "Windows更新问题": {
        "event_ids": [1027, 10022],
        "sources": ["Microsoft-Windows-WindowsUpdateClient", "Microsoft-Windows-Update", "TrustedInstaller"],
        "keywords": ["update", "更新", "windows update", "install failure", "安装失败", "0x800"],
        "severity": "warning",
        "suggestions": [
            "运行Windows更新疑难解答",
            "清空SoftwareDistribution文件夹：net stop wuauserv，删除C:\\Windows\\SoftwareDistribution内容，net start wuauserv",
            "使用DISM还原健康状态：DISM /Online /Cleanup-Image /RestoreHealth",
            "手动下载并安装更新补丁",
            "检查磁盘空间是否充足",
            "暂时禁用第三方杀毒软件后重试",
            "使用Windows Update MiniTool检查更新状态",
        ],
    },
    "硬件故障": {
        "event_ids": [10010, 10011, 10012, 10013, 10014, 10015, 1046],
        "sources": ["Microsoft-Windows-Kernel-Processor-Power", "ACPI", "HAL", "volmgr", "disk"],
        "keywords": ["thermal", "temperature", "温度", "fan", "风扇", "power", "电源", "hardware", "硬件", "SMART"],
        "severity": "critical",
        "suggestions": [
            "检查CPU/GPU温度是否过高（使用HWMonitor等工具）",
            "清理机箱灰尘，确保散热风扇正常运转",
            "检查电源供应器(PSU)是否正常工作",
            "检查内存条是否接触良好，重新插拔",
            "使用硬件检测工具进行全面诊断",
            "检查主板电容是否有鼓包或漏液",
            "如果硬件检测到即将故障，立即备份数据并准备更换",
        ],
    },
}


def safe_str(value, encoding='gbk'):
    """安全转换字符串，避免编码错误"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        try:
            return value.decode(encoding, errors='replace')
        except Exception:
            return ""


def check_log_access(log_name, machine_name=None):
    """
    探测指定类别日志是否可读（Security 需管理员权限，ADR-004）
    :return: {"ok": bool, "error": str}
    """
    try:
        hand = win32evtlog.OpenEventLog(machine_name, log_name)
        win32evtlog.CloseEventLog(hand)
        return {"ok": True, "error": ""}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _parse_event(ev_obj, log_name, seq):
    """将 pywin32 事件对象转换为标准 dict"""
    try:
        timestamp = ev_obj.TimeGenerated.Format()
        if hasattr(ev_obj.TimeGenerated, 'timetuple'):
            dt = datetime.datetime(*ev_obj.TimeGenerated.timetuple()[:6])
        else:
            try:
                dt = datetime.datetime.strptime(timestamp, "%a %b %d %H:%M:%S %Y")
            except ValueError:
                dt = datetime.datetime.now()
    except Exception:
        timestamp = ""
        dt = datetime.datetime.now()

    event_id = ev_obj.EventID & 0xFFFF
    source = safe_str(ev_obj.SourceName)
    category = safe_str(ev_obj.EventCategory)
    level = ev_obj.EventType
    computer = safe_str(ev_obj.ComputerName)

    try:
        desc = win32evtlogutil.SafeFormatMessage(ev_obj)
        if isinstance(desc, bytes):
            desc = desc.decode('utf-8', errors='replace')
    except Exception:
        desc = ""

    strings = []
    if ev_obj.StringInserts:
        strings = [safe_str(s) for s in ev_obj.StringInserts]

    level_name, level_label, level_class = EVENT_LEVELS.get(level, ("未知", "unknown", "secondary"))

    return {
        "id": seq,
        "event_id": event_id,
        "source": source,
        "category": category,
        "level": level,
        "level_name": level_name,
        "level_label": level_label,
        "level_class": level_class,
        "timestamp": timestamp,
        "datetime": dt,
        "computer": computer,
        "description": desc,
        "strings": strings,
        "log_type": LOG_SOURCES.get(log_name, log_name),
    }


def iter_events(log_name, machine_name=None, since=None, until=None,
                batch=256, cancel_event=None, progress_cb=None, max_events=None):
    """
    流式迭代读取事件日志（ADR-002 核心）。

    从最新事件往回读（BACKWARDS_READ），逐批产出；
    - 事件时间早于 since 时整体停止（后续更旧，无需继续）
    - 事件时间晚于 until 时跳过
    - cancel_event 被置位时立即停止（导出任务取消）
    - progress_cb(scanned) 每批回调（进度上报）
    - max_events 上限保护（检索模式防止内存爆炸）

    :yields: 事件 dict
    """
    try:
        hand = win32evtlog.OpenEventLog(machine_name, log_name)
    except Exception as e:
        raise RuntimeError(f"读取{LOG_SOURCES.get(log_name, log_name)}失败: {e}")

    scanned = 0
    yielded = 0
    try:
        flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
        chunk = win32evtlog.ReadEventLog(hand, flags, 0)
        while chunk:
            if cancel_event is not None and cancel_event.is_set():
                break
            stop = False
            for ev_obj in chunk:
                scanned += 1
                try:
                    dt = datetime.datetime(*ev_obj.TimeGenerated.timetuple()[:6])
                except Exception:
                    dt = datetime.datetime.now()
                # 时间剪枝（倒序：越过 until 之前的都跳过；早于 since 即终止）
                if until is not None and dt > until:
                    continue
                if since is not None and dt < since:
                    stop = True
                    break
                if max_events is not None and yielded >= max_events:
                    stop = True
                    break
                ev = _parse_event(ev_obj, log_name, yielded + 1)
                ev["datetime"] = dt
                yield ev
                yielded += 1
            if progress_cb is not None:
                try:
                    progress_cb(scanned)
                except Exception:
                    pass
            if stop:
                break
            try:
                chunk = win32evtlog.ReadEventLog(hand, flags, 0)
            except Exception:
                break
    finally:
        try:
            win32evtlog.CloseEventLog(hand)
        except Exception:
            pass


def get_event_logs(log_type="System", machine_name=None, max_events=500, since=None):
    """
    读取Windows事件日志（兼容旧签名；内部走流式迭代）
    :param log_type: 日志类型 System, Application, Security, Setup
    :param machine_name: 远程机器名，None为本地
    :param max_events: 最大事件数
    :param since: datetime，仅读取该时间之后的事件（None=不限）
    :return: 事件日志列表
    """
    return list(iter_events(log_type, machine_name=machine_name,
                            since=since, max_events=max_events))


def match_condition(event, conditions):
    """
    对单个事件应用过滤条件（服务端过滤，ADR-002）
    :param conditions: {"levels": set[int], "source": str, "keyword": str, "event_id": int|None}
    """
    levels = conditions.get("levels")
    if levels:
        if event["level"] not in levels:
            return False
    source = (conditions.get("source") or "").strip()
    if source:
        if source.lower() not in (event["source"] or "").lower():
            return False
    keyword = (conditions.get("keyword") or "").strip()
    if keyword:
        kw = keyword.lower()
        haystack = (event["description"] or "") + " " + " ".join(event.get("strings") or [])
        if kw not in haystack.lower() and kw not in (event["source"] or "").lower():
            return False
    event_id = conditions.get("event_id")
    if event_id:
        try:
            if int(event_id) != int(event["event_id"]):
                return False
        except (TypeError, ValueError):
            return False
    return True


def analyze_event(event):
    """
    对单个事件进行分析，匹配已知故障模式
    :return: 匹配的故障模式名称列表
    """
    event_id = event["event_id"]
    source = event["source"].lower()
    desc = event["description"].lower()

    matches = []
    for pattern_name, pattern in FAULT_PATTERNS.items():
        if event_id in pattern["event_ids"]:
            matches.append(pattern_name)
            continue
        for src in pattern["sources"]:
            if src.lower() in source:
                matches.append(pattern_name)
                break
        else:
            for kw in pattern["keywords"]:
                if kw.lower() in desc:
                    matches.append(pattern_name)
                    break

    return list(dict.fromkeys(matches))


def get_system_summary(events):
    """
    获取系统日志摘要统计
    :param events: 事件列表
    :return: 统计字典
    """
    summary = {
        "total": len(events),
        "critical": 0,
        "error": 0,
        "warning": 0,
        "info": 0,
        "unknown": 0,
        "by_source": defaultdict(int),
        "by_hour": defaultdict(int),
        "by_hour_labels": [],
        "by_hour_values": [],
        "by_type": defaultdict(int),
        "top_events": [],
    }

    for event in events:
        level_name = event["level_name"]
        if level_name == "关键":
            summary["critical"] += 1
        elif level_name == "错误":
            summary["error"] += 1
        elif level_name == "警告":
            summary["warning"] += 1
        elif level_name == "信息":
            summary["info"] += 1
        else:
            summary["unknown"] += 1

        summary["by_source"][event["source"]] += 1
        summary["by_type"][event["log_type"]] += 1

        hour = event["datetime"].hour
        summary["by_hour"][hour] += 1

    summary["by_hour_labels"] = [f"{h}:00" for h in range(24)]
    summary["by_hour_values"] = [summary["by_hour"].get(h, 0) for h in range(24)]

    summary["top_events"] = sorted(summary["by_source"].items(), key=lambda x: x[1], reverse=True)[:10]

    return summary


def get_fault_analysis(events, with_evidence=False, max_evidence=5):
    """
    全面故障分析
    :param events: 事件列表
    :param with_evidence: 是否输出证据样本（ADR-007）
    :param max_evidence: 每模式最多证据条数
    :return: 故障分析结果列表
    """
    fault_counts = defaultdict(lambda: {"count": 0, "events": [], "severity": "info", "suggestions": []})

    for event in events:
        matches = analyze_event(event)
        for pattern_name in matches:
            pattern = FAULT_PATTERNS[pattern_name]
            data = fault_counts[pattern_name]
            data["count"] += 1
            data["severity"] = pattern["severity"]
            data["suggestions"] = pattern["suggestions"]
            if with_evidence and len(data["events"]) < max_evidence:
                data["events"].append({
                    "timestamp": event["timestamp"],
                    "event_id": event["event_id"],
                    "source": event["source"],
                    "level_name": event["level_name"],
                    "description": (event["description"] or "")[:200],
                })

    result = []
    for name, data in sorted(fault_counts.items(), key=lambda x: x[1]["count"], reverse=True):
        item = {
            "name": name,
            "count": data["count"],
            "severity": data["severity"],
            "suggestions": data["suggestions"],
        }
        if with_evidence:
            item["evidence"] = data["events"]
        result.append(item)

    return result

