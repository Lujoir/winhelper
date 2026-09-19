#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""client_update 命令处理器单测（端侧，2026-09-19）。

覆盖中心「更新推送」命令在终端侧的执行语义：
- notify：下载就绪即成功，applied=False（等本机用户点「立即更新」）
- silent：就绪 + 有退出钩子 → 拉起 updater 并延后退出主进程
- silent 无钩子（非桌面态）→ 降级 notify，mode_fallback 如实回执
- 已是最新（idle）→ 成功，note=already_latest
- 下载失败（failed）→ 失败 + error 透出
- uplink 未配置 → 失败 uplink_not_configured
- 非法 mode → 回退 notify（绝不误触发静默安装）

隔离：UPLINK_CONFIG_DIR 指向临时目录；updater/appctl 以假模块注入，
不触网、不写用户目录、不起真实安装进程。
"""
import os
import shutil
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASSED, FAILED = [], []

CFG_BASE = {"terminal_id": "UPD-E2E", "heartbeat_interval": 30,
            "server_ca_fingerprint": ""}


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


def _install_fakes():
    """注入假 updater / appctl，返回可观测的计数器与状态字典。"""
    obs = {"fetch": 0, "apply": 0}
    state = {"status": "ready", "version": "9.9.9", "file": "C:/tmp/fake.exe",
             "error": None}

    fake_upd = types.ModuleType("updater")

    def _check_and_fetch(current_version, server_url, token):
        obs["fetch"] += 1
        return dict(state)

    fake_upd.check_and_fetch = _check_and_fetch
    fake_upd.load_state = lambda: dict(state)
    fake_upd.write_main_pid = lambda: None
    sys.modules["updater"] = fake_upd

    fake_appctl = types.ModuleType("appctl")

    def _update_apply():
        obs["apply"] += 1
        return {"success": True, "exiting": True}

    fake_appctl.update_apply = _update_apply
    sys.modules["appctl"] = fake_appctl
    return obs, state


def main():
    tmp = tempfile.mkdtemp(prefix="etp_upd_")
    os.environ["UPLINK_CONFIG_DIR"] = tmp
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== client_update cmd tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def run(tmp):
    obs, state = _install_fakes()
    import uplink                                          # noqa: E402

    def cfg(server="http://e2e.local:1", token="tok", enabled=True):
        d = dict(CFG_BASE)
        d.update({"enabled": enabled, "server_url": server, "token": token})
        uplink.save_config(d)

    cfg()

    handler = uplink.COMMAND_HANDLERS.get("client_update")
    check("client_update 处理器已注册", handler is not None)
    if handler is None:
        return

    # ---------- 1. notify（默认，安全模式）----------
    state.update({"status": "ready", "version": "9.9.9", "error": None})
    obs["fetch"] = 0
    ok, data = handler({"mode": "notify"})
    check("notify 就绪 → ok=True / applied=False / ready_await_user",
          ok is True and data.get("applied") is False
          and data.get("status") == "ready"
          and data.get("note") == "ready_await_user", str(data))
    check("notify 只下载不安装（未调 update_apply）", obs["apply"] == 0)
    check("notify 回执带 current_version", bool(data.get("current_version")))

    # ---------- 2. silent 无退出钩子 → 降级为 notify ----------
    uplink.register_update_exit_hook(None)
    ok, data = handler({"mode": "silent"})
    check("silent 无钩子 → 降级 notify 且如实标注 mode_fallback",
          ok is True and data.get("applied") is False
          and data.get("mode_fallback") == "notify"
          and obs["apply"] == 0, str(data))

    # ---------- 3. silent 有钩子 → 拉起 updater + 延后退出 ----------
    fired = {"n": 0}
    uplink.register_update_exit_hook(
        lambda: fired.__setitem__("n", fired["n"] + 1))
    old_delay = uplink._SILENT_EXIT_DELAY_SEC
    uplink._SILENT_EXIT_DELAY_SEC = 0.05
    try:
        obs["apply"] = 0
        ok, data = handler({"mode": "silent"})
        check("silent 就绪 → applied=True + 已拉起 updater",
              ok is True and data.get("applied") is True
              and data.get("note") == "silent_install_scheduled"
              and obs["apply"] == 1, str(data))
        time.sleep(0.4)
        check("silent 延后退出被触发（先回执、后退出主进程）",
              fired["n"] == 1, "fired=%d" % fired["n"])
    finally:
        uplink._SILENT_EXIT_DELAY_SEC = old_delay

    # ---------- 4. 已是最新 → 成功（不是失败）----------
    state.update({"status": "idle", "version": None, "error": None})
    ok, data = handler({"mode": "notify"})
    check("已是最新(idle) → 成功 + note=already_latest",
          ok is True and data.get("note") == "already_latest", str(data))

    # ---------- 5. 下载失败 → 失败 + 原因透出 ----------
    state.update({"status": "failed", "error": "sha256 mismatch"})
    ok, data = handler({"mode": "notify"})
    check("下载失败(failed) → ok=False + error 透出",
          ok is False and data.get("error") == "sha256 mismatch", str(data))
    check("下载失败不触发安装", obs["apply"] == 1)

    # ---------- 6. uplink 未配置 ----------
    cfg(server="", token="", enabled=False)
    ok, data = handler({"mode": "silent"})
    check("未配置 uplink → uplink_not_configured",
          ok is False and data.get("error") == "uplink_not_configured",
          str(data))

    # ---------- 7. 非法 mode 回退 notify（安全兜底）----------
    cfg()
    state.update({"status": "ready", "version": "9.9.9", "error": None})
    ok, data = handler({"mode": "SILENT-typo"})
    check("非法 mode 回退 notify（绝不误触发静默安装）",
          ok is True and data.get("mode") == "notify"
          and data.get("applied") is False, str(data))

    # ---------- 8. args 非 dict / 缺省不崩 ----------
    ok, data = handler(None)
    check("args 为 None 时不崩（按 notify 处理）",
          ok is True and data.get("mode") == "notify", str(data))

    # ---------- 9. 通知可靠性（2026-09-19 缺陷修复的门禁）----------
    #   背景：发布新版本后终端收不到通知，两根因——
    #   a) 心跳只在「版本号变化」时检查一次，卡死/失败后再无重试；
    #   b) 提示条是主窗口内 DOM，客户端常驻托盘时用户看不到。
    import inspect
    check("心跳兜底重查间隔已配置（防通知永久丢失）",
          int(getattr(uplink, "_UPDATE_RECHECK_SEC", 0) or 0) > 0,
          str(getattr(uplink, "_UPDATE_RECHECK_SEC", None)))
    loop_src = inspect.getsource(uplink._loop)
    check("心跳循环含兜底重查分支（_last_update_check）",
          "_last_update_check" in loop_src
          and "_UPDATE_RECHECK_SEC" in loop_src)
    desk = os.path.join(ROOT, "desktop.py")
    dtxt = ""
    if os.path.isfile(desk):
        with open(desk, encoding="utf-8") as fh:
            dtxt = fh.read()
    check("桌面入口含更新就绪托盘气泡（_update_notify_loop）",
          "_update_notify_loop" in dtxt and "t.notify(" in dtxt)


if __name__ == "__main__":
    sys.exit(main())
