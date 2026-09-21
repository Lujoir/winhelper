# -*- coding: utf-8 -*-
"""desktop-policy 冒烟：桥接 handler 真实执行（mock transport，不触真实平台/壁纸）
+ 主应用 bridge.py 挂载静态校验（零执行零副作用）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desktop_policy as dp  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name +
          ("" if cond else "  | " + str(detail)[:140]))


class FakeTransport(dp.Transport):
    def fetch_policy(self, revision, brief):
        return {"ok": True, "unchanged": True, "revision": revision}

    def download_wallpaper(self, wid, dest, checksum):
        raise OSError("smoke: no download")

    def report(self, payload):
        return {"success": True}


def main():
    # —— 主应用态日志一致性（BRG-063 防回归）：不注入 set_log_dir，
    #    log() 默认落盘路径必须与 handle_dp_logs 读取路径一致且 tail 可见 ——
    print("== smoke 0：主应用态日志写读一致性 ==")
    probe = "BRG063_PROBE_%d" % int(time.time())
    dp.log(probe)
    expected = os.path.join(dp.data_dir(), "logs",
                            "dp_%s.log" % time.strftime("%Y%m%d"))
    lg0 = dp.handle_dp_logs({})
    check("logs·主应用态 log() 落盘路径 == handle_dp_logs 读取路径",
          os.path.normpath(lg0.get("log_file") or "") ==
          os.path.normpath(expected), (lg0.get("log_file"), expected))
    check("logs·主应用态 tail 含刚写入探针",
          any(probe in ln for ln in (lg0.get("tail") or [])),
          (lg0.get("tail") or [])[-3:])
    check("logs·logs 目录缺失自动创建", os.path.isdir(lg0.get("log_dir") or ""))

    # 注入隔离日志目录（后续 handler 测试不污染真实日志）
    dp.set_log_dir(os.path.join(dp.data_dir(), "smoke_logs"))
    # 预注入引擎单例（避免 get_engine 启动真实轮询）
    dp._DP_ENGINE = dp.Engine(transport=FakeTransport(), poll_sec=3600)

    print("== smoke 1：桥接 handler 真实执行 ==")
    st = dp.handle_dp_status({})
    check("status·success", st.get("success") is True)
    check("status·引擎运行", st.get("engine_running") is True)
    check("status·显示器含本机真实布局", isinstance(
        st.get("monitors"), list) and len(st["monitors"]) >= 1,
        st.get("monitors"))
    check("status·四类策略结果键齐备",
          set(st.get("results") or {}) == {"desktop_wallpaper", "lock_screen",
                                           "power_plan", "idle_lock"})
    check("status·会话类型合法", st.get("session_type") in ("console", "rdp"),
          st.get("session_type"))

    r = dp.handle_dp_policy_now({})
    check("policy-now·返回 task_id", bool(r.get("task_id")), r)
    tid = r.get("task_id")
    deadline = time.time() + 10
    task = None
    while time.time() < deadline:
        task = dp.handle_dp_task_status({"task_id": tid})
        if task.get("status") != "running":
            break
        time.sleep(0.2)
    check("task-status·终态 done", task and task.get("status") == "done", task)
    check("task-status·unchanged 结果", bool(
        task and task.get("result") and task["result"].get("unchanged")),
        task)

    miss = dp.handle_dp_task_status({"task_id": "nope"})
    check("task-status·不存在任务报错", miss.get("success") is False)

    lg = dp.handle_dp_logs({})
    check("logs·返回目录与文件名", "logs" in (lg.get("log_dir") or "")
          and lg.get("log_file", "").startswith(lg.get("log_dir")),
          lg.get("log_dir"))

    print("== smoke 2：主应用 bridge.py 挂载静态校验（零执行） ==")
    ws = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))  # tools → desktop-policy → workspace 根
    bridge_src = ""
    path = os.path.join(ws, "bridge.py")
    if os.path.exists(path):
        import io
        bridge_src = io.open(path, encoding="utf-8").read()
    check("bridge·desktop_policy import 行存在",
          "from desktop_policy import" in bridge_src and
          "handle_dp_status" in bridge_src)
    for route in ("/api/desktoppolicy/status", "/api/desktoppolicy/policy-now",
                  "/api/desktoppolicy/apply-now",
                  "/api/desktoppolicy/task-status", "/api/desktoppolicy/logs"):
        check("bridge·路由挂载 %s" % route, ('"%s"' % route) in bridge_src or
              ("'%s'" % route) in bridge_src)
    import py_compile
    for f in (path, os.path.join(ws, "desktop_policy.py"),
              os.path.join(ws, "web", "desktoppolicy.js")):
        if f.endswith(".js"):
            continue
        try:
            py_compile.compile(f, doraise=True)
            check("语法·%s" % os.path.basename(f), True)
        except py_compile.PyCompileError as exc:
            check("语法·%s" % os.path.basename(f), False, exc)

    print("\n===== 冒烟结果: %d/%d PASS =====" % (len(PASS), len(PASS) + len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
