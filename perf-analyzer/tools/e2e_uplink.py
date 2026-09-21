# -*- coding: utf-8 -*-
"""
uplink 终端接入 E2E（本地 mock 服务端，验证门禁）
================================================================
流程：mock_etp_server（随机端口+随机 token）→ uplink 配置/启动 →
  注册（hwinfo）→ 心跳解析注入命令 → 分发与回执 → 指标上报 → 幂等 → 停止。
断言：注册字段 / token 鉴权 / iperf·net_probe·未知类型·ai_disabled 回执 /
  metrics 到达 / 同 id 幂等单回执 / 状态接口不回显 token。

隔离：UPLINK_CONFIG_DIR 指向临时目录（不碰用户真实配置）；
  UPLINK_IPERF_EXE 指向临时生成的假 iperf3 客户端（固定 JSON 输出）。
注意（ADR-018）：未知命令类型用良性探测串 unknown_type_probe_x。

运行：python tools/e2e_uplink.py
"""

import io
import json
import os
import re
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # perf-analyzer/
WORKSPACE = os.path.dirname(ROOT)     # 工作区根（主应用）
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

# 隔离配置目录（必须在 import uplink 前设置）
TMP_CFG = tempfile.mkdtemp(prefix="e2e_uplink_cfg_")
os.environ["UPLINK_CONFIG_DIR"] = TMP_CFG

import uplink  # noqa: E402
from mock_etp_server import MockEtpServer  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def _make_fake_iperf(tmpdir):
    """假 iperf3 客户端（.bat 包装 python 脚本，固定输出 -J JSON）。"""
    py = sys.executable
    script = os.path.join(tmpdir, "fake_iperf3_payload.py")
    with io.open(script, "w", encoding="utf-8") as f:
        f.write("import json\n"
                "print(json.dumps({'end': {'sum_sent': {'bits_per_second': 941000000.0, "
                "'retransmits': 2}, 'sum_received': {'bits_per_second': 938000000.0}}}))\n")
    bat = os.path.join(tmpdir, "iperf3.bat")
    with io.open(bat, "w", encoding="ascii", errors="replace") as f:
        f.write('@echo off\r\n"%s" "%s"\r\n' % (py, script))
    return bat


def _wait_results(mock, cids, timeout=60):
    """轮询 mock 直到全部 cid 收到回执；返回 {cid: body}"""
    deadline = time.time() + timeout
    got = {}
    while time.time() < deadline:
        for r in mock.results():
            got.setdefault(r["cid"], r["body"])
        missing = [c for c in cids if c not in got]
        if not missing:
            return got
        time.sleep(0.3)
    return got


def main():
    tmp = tempfile.mkdtemp(prefix="e2e_uplink_")
    fake_iperf = _make_fake_iperf(tmp)
    os.environ["UPLINK_IPERF_EXE"] = fake_iperf

    mock = MockEtpServer()
    mock.start()
    print("== uplink E2E == mock at %s (token=%s...)" % (mock.url, mock.token[:6]))

    T_IPERF, T_NET, T_UNK, T_AI = "e2e-cmd-iperf-1", "e2e-cmd-net-2", "e2e-cmd-unk-3", "e2e-cmd-ai-4"

    # ---- 1. 配置保存（handle_uplink_save 约定路径）----
    st = uplink.handle_uplink_save({
        "server_url": mock.url,
        "token": mock.token,
        "enabled": "true",
        "heartbeat_interval": "1",
    })
    check("save: success + enabled", st.get("success") is True and st["uplink"]["enabled"] is True)
    check("save: token not echoed", mock.token not in json.dumps(st), "token 泄露在状态返回中")
    check("save: has_token=true", st["uplink"]["has_token"] is True)
    uplink.save_config(dict(uplink.load_config(), heartbeat_interval=1))

    # ---- 2. 注入命令（单次下发语义）----
    mock.push_command({"id": T_IPERF, "command": "iperf_client",
                       "args": {"task_id": T_IPERF, "server_ip": "127.0.0.1",
                                "server_port": 59999, "duration_sec": 1, "mode": "tcp"},
                       "timeout_sec": 60})
    mock.push_command({"id": T_NET, "command": "net_probe",
                       "args": {"task_id": T_NET,
                                "targets": [{"host": "127.0.0.1", "method": "ping"},
                                            {"host": "_gateway", "method": "ping"},
                                            {"host": "127.0.0.1", "method": "tcp", "port": mock.port}]},
                       "timeout_sec": 60})
    mock.push_command({"id": T_UNK, "command": "unknown_type_probe_x", "args": {}, "timeout_sec": 30})
    mock.push_command({"id": T_AI, "command": "ai_context", "args": {"issue_description": "e2e"},
                       "timeout_sec": 30})

    # ---- 3. 等待回执收敛 ----
    got = _wait_results(mock, [T_IPERF, T_NET, T_UNK, T_AI], timeout=60)
    for cid in (T_IPERF, T_NET, T_UNK, T_AI):
        check("回执到达 %s" % cid, cid in got, "等待超时，已收到: %s" % list(got))
    if len(got) < 4:
        _summary(mock, tmp)
        return 1

    # ---- 4. 回执内容断言 ----
    ip = got[T_IPERF]
    check("iperf 回执 ok=true", ip.get("ok") is True, json.dumps(ip)[:160])
    check("iperf data 带 task_id", (ip.get("data") or {}).get("task_id") == T_IPERF)
    check("iperf summary 带宽", "Mbits/sec" in str((ip.get("data") or {}).get("summary", "")),
          str(ip.get("data"))[:120])

    np = got[T_NET]
    nd = np.get("data") or {}
    check("net_probe 回执 ok=true", np.get("ok") is True, json.dumps(np)[:160])
    check("net_probe data 带 task_id", nd.get("task_id") == T_NET)
    results = nd.get("results") or []
    check("net_probe 3 个探测结果", len(results) == 3, str(results)[:160])
    localhost_ping = [r for r in results if r.get("host") == "127.0.0.1" and r.get("method") == "ping"]
    check("net_probe 127.0.0.1 ping 成功", bool(localhost_ping) and localhost_ping[0].get("ok") is True,
          str(localhost_ping)[:120])
    gw_entries = [r for r in results
                  if r.get("host") == "_gateway"
                  or re.match(r"^\d{1,3}(\.\d{1,3}){3}$", str(r.get("host")))]
    check("net_probe _gateway 条目（解析为网关 IP 或 gateway_not_found）", len(gw_entries) >= 1,
          str(results)[:200])
    check("net_probe gateway 字段回传", bool(nd.get("gateway")))
    tcp_entries = [r for r in results if r.get("method") == "tcp"]
    check("net_probe TCP 探测 mock 端口成功", bool(tcp_entries) and tcp_entries[0].get("ok") is True,
          str(tcp_entries)[:120])

    unk = got[T_UNK]
    check("未知类型回执 ok=false", unk.get("ok") is False, json.dumps(unk)[:120])
    check("未知类型 error 含 unknown_command_type",
          "unknown_command_type" in json.dumps(unk.get("data") or {}))

    ai = got[T_AI]
    check("ai_context 回执 ok=false", ai.get("ok") is False, json.dumps(ai)[:120])
    check("ai_context reason=ai_disabled",
          (ai.get("data") or {}).get("error") == "ai_disabled")

    # ---- 5. 注册与指标 ----
    regs = mock.calls("/register")
    check("register 到达", len(regs) >= 1)
    if regs:
        hw = (regs[-1]["body"].get("hwinfo") or {})
        check("register 携带 terminal_type=windows", regs[-1]["body"].get("terminal_type") == "windows")
        check("register 携带 hostname", bool(regs[-1]["body"].get("hostname")))
        check("register hwinfo.cpu_model", bool(hw.get("cpu_model")), str(hw)[:160])
        check("register hwinfo.cpu_cores", bool(hw.get("cpu_cores")))
        check("register hwinfo.mem_total_mb", bool(hw.get("mem_total_mb")))
        check("register hwinfo.os_arch", bool(hw.get("os_arch")))
    check("全部请求 token 鉴权通过", all(c["token_ok"] for c in mock.seen),
          str([c["path"] for c in mock.seen if not c["token_ok"]])[:160])
    mets = mock.calls("/metrics")
    check("metrics 上报到达（≥1 条）", len(mets) >= 1)
    if mets:
        m0 = mets[-1]["body"]
        check("metrics 结构 cpu/mem/swap/disks/volumes",
              all(k in m0 for k in ("cpu", "mem", "swap", "disks", "volumes")), str(list(m0))[:120])
        check("metrics 携带 terminal_type/client_version/ts",
              m0.get("terminal_type") == "windows" and bool(m0.get("client_version")) and bool(m0.get("ts")))
        check("metrics mem.used_percent", m0["mem"].get("used_percent") is not None)
        check("metrics disks 条目带 mount", bool((m0.get("disks") or [{}])[0].get("mount")))

    # ---- 6. 幂等：同 cid 重复 dispatch 只回执一次 ----
    before = len(mock.results())
    uplink._dispatch("TID", {"id": "idem-1", "command": "unknown_type_probe_x", "args": {}})
    uplink._dispatch("TID", {"id": "idem-1", "command": "unknown_type_probe_x", "args": {}})
    deadline = time.time() + 15
    while time.time() < deadline:
        after = mock.results()
        if len(after) >= before + 1:
            break
        time.sleep(0.2)
    after = mock.results()
    idem = [r for r in after if r["cid"] == "idem-1"]
    check("同 id 幂等：仅一次回执", len(idem) == 1, "收到 %d 次" % len(idem))

    # ---- 7. 状态接口 ----
    st = uplink.handle_uplink_status({})
    u = st.get("uplink") or {}
    check("status: state=connected", u.get("state") == "connected", str(u.get("state")))
    check("status: registered=true", u.get("registered") is True)
    check("status: executed_count ≥ 5", (u.get("executed_count") or 0) >= 5, str(u.get("executed_count")))
    check("status: 不回显 token", mock.token not in json.dumps(st))

    # ---- 8. save 负路径 ----
    st = uplink.handle_uplink_save({"server_url": mock.url, "token": "", "enabled": "true"})
    check("save 留空 token 不修改（仍 enabled）", st.get("success") is True and st["uplink"]["enabled"] is True)
    # 清空配置后再测缺参拒绝（配置已有值时空参数=不修改，不会拒绝）
    uplink.save_config({"enabled": False, "server_url": "", "token": "",
                        "terminal_id": "", "heartbeat_interval": 30})
    st = uplink.handle_uplink_save({"server_url": "", "token": "", "enabled": "true"})
    check("save 缺 token 拒绝", st.get("success") is False and "missing" in str(st.get("error")), str(st))
    st = uplink.handle_uplink_save({"server_url": "ftp://x", "token": "", "enabled": "true"})
    check("save 非法 scheme 拒绝", st.get("success") is False, str(st))
    # 恢复配置供后续断言状态
    uplink.save_config({"enabled": True, "server_url": mock.url, "token": mock.token,
                        "terminal_id": "", "heartbeat_interval": 1})

    # ---- 9. 停止 ----
    uplink.stop()
    time.sleep(0.5)
    st = uplink.handle_uplink_status({})
    check("stop 后 state=disabled", (st["uplink"] or {}).get("state") == "disabled")

    _summary(mock, tmp)


def _summary(mock, tmp):
    try:
        mock.shutdown()
    except Exception:
        pass
    print("=" * 50)
    if FAILED:
        print("E2E 失败：%d 项" % len(FAILED))
        for e in FAILED:
            print("  -", e)
        sys.exit(1)
    print("uplink E2E 全部通过（%d 项）" % len(PASSED))
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(TMP_CFG, ignore_errors=True)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
