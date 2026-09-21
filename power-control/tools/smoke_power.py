# -*- coding: utf-8 -*-
"""power-control · 本机真实采集冒烟（P0 门禁，只读）。

验证：五段采集真实执行、消费线分支如实标注（本开发机为 IdeaCentre 消费线）、
日志落盘、无任何写操作与 shutdown 调用。
运行：python tools/smoke_power.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import power_control as pc  # noqa: E402

_checks = []


def check(name, ok, detail=""):
    _checks.append((name, bool(ok), detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         ("  <- " + str(detail)[:220]) if detail and not ok
                         else ""))


def main():
    snap = pc.collect_snapshot()
    # ① 顶层结构
    check("快照 schema=1", snap.get("schema") == 1)
    check("collected_at 非空", bool(snap.get("collected_at")))
    m = snap.get("machine") or {}
    check("机型 Manufacturer 非空", bool(m.get("manufacturer")),
          m.get("manufacturer"))
    check("机型 Model 非空", bool(m.get("model")), m.get("model"))
    print("    机型: %s / %s / %s" % (m.get("manufacturer"), m.get("model"),
                                      m.get("system_family")))
    print("    分线: %s -> %s" % (m.get("vendor_line"),
                                  m.get("capability_text")))
    # ② 本机（开发机 IdeaCentre 消费线）能力分支
    if "LENOVO" in (m.get("manufacturer") or "").upper():
        check("Lenovo 消费线判定", m.get("vendor_line") == "lenovo_consumer",
              m.get("vendor_line"))
        check("消费线能力=不支持远程配置",
              m.get("capability") == "not_supported",
              m.get("capability"))
        check("BIOS 人工指引存在", "人工" in (snap["bios"].get("reason") or ""),
              snap["bios"].get("reason"))
        check("BIOS 企业线类未检出（消费线）",
              snap["bios"].get("wmi_class_found") is False)
    # ③ 唤醒定时器 / 关机任务 / 快速启动
    wt = snap.get("wake_timers") or {}
    check("唤醒定时器段 ok 或需管理员", wt.get("ok") is True,
          wt.get("error"))
    print("    唤醒定时器: count=%s need_admin=%s"
          % (wt.get("count"), wt.get("need_admin")))
    st = snap.get("shutdown_tasks") or {}
    check("关机任务段 ok", st.get("ok") is True, st.get("error"))
    print("    关机计划任务: count=%s" % st.get("count"))
    fs = snap.get("fast_startup") or {}
    check("快速启动段有判定", fs.get("enabled") is not None,
          fs.get("note"))
    print("    快速启动: enabled=%s available=%s note=%s"
          % (fs.get("enabled"), fs.get("available"), fs.get("note")))
    check("errors 已汇总", isinstance(snap.get("errors"), list))
    if snap.get("errors"):
        print("    采集告警（如实上报）: %s" % snap["errors"])
    # ④ 日志落盘
    log_dir = os.path.join(pc.data_dir(), "logs")
    logs = [f for f in os.listdir(log_dir) if f.startswith("pc_")
            and f.endswith(".log")] if os.path.isdir(log_dir) else []
    check("日期分文件日志存在", bool(logs), log_dir)
    # ⑤ 红线：引擎源码无 shutdown 直呼（ADR-018）
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "power_control.py"),
        encoding="utf-8").read()
    import re
    bad = re.findall(r"shutdown\s+(/s|/r|\.exe)", src, re.I)
    check("引擎源码无 shutdown 调用串", not bad, bad)

    n_fail = sum(1 for _, ok, _ in _checks if not ok)
    print("\n== smoke 完成：%d 项，失败 %d ==" % (len(_checks), n_fail))
    print("== 快照样例 JSON（脱敏前，仅本机字段）==")
    print(json.dumps(snap, ensure_ascii=False, indent=2)[:3000])
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
