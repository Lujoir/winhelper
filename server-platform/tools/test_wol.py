# -*- coding: utf-8 -*-
"""WoL 调度状态机单测（纯标准库，桩注入；python tools/test_wol.py）。

覆盖（ADR-044）：到期直发 → 观察窗 → 中继升级（同网段选举）→ 唤醒确认 /
无中继放弃；MAC 缺失失败；broadcast/MAC 归一等纯函数。
"""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))
import wol  # noqa: E402


# ---------------------------------------------------------------- 桩
class FakePC(object):
    def __init__(self, scheds):
        self.scheds = scheds
        self.marks = []
        self.attempts = []

    def wol_schedule_list(self):
        return [dict(s) for s in self.scheds]

    def wol_schedule_mark(self, sid, **fields):
        self.marks.append((sid, fields))
        for s in self.scheds:
            if s["id"] == sid:
                s.update(fields)
        return True

    def wol_attempt_add(self, sid, tid, phase, relay_tid, ok, detail,
                        now=None):
        self.attempts.append((sid, tid, phase, relay_tid, ok, detail))


class FakeStore(object):
    def __init__(self, terms):
        self.terms = terms
        self.cmds = []

    def get_terminal(self, t):
        return self.terms.get(t)

    def list_terminals(self):
        return list(self.terms.values())

    def get_terminal_asset(self, t):
        return {"network": [{"ip": "172.17.90.18",
                             "mac": "AA-BB-CC-DD-EE-FF"}]}

    def enqueue_command(self, t, c, a, timeout_sec=120, source=""):
        self.cmds.append((t, c, a))
        return 777

    def get_command(self, t, c):
        return {"status": "done", "result_json": '{"ok": true}'}


class FakeCtx(object):
    def __init__(self, store, pc, settings=None):
        self.store = store
        self.pc = pc
        self.settings = settings if settings is not None else {}
        self.config = {"heartbeat_timeout_sec": 180}


def _mk_sched(**kw):
    s = {"id": 1, "terminal_id": "T1", "mac": "", "name": "t",
         "time_hhmm": time.strftime("%H:%M"), "enabled": 1,
         "method": "auto", "run_state": "", "direct_ts": None,
         "relay_ts": None, "relay_cid": None, "relay_tid": "",
         "last_run_date": "", "last_result": "", "operator": "e2e",
         "created_ts": 0, "updated_ts": 0}
    s.update(kw)
    return s


def _base_terms(last_seen=1000):
    # R1 与 T1 同 /24 且在线（last_seen=now）
    return {"T1": {"terminal_id": "T1", "ip": "172.17.90.215",
                   "last_seen": last_seen},
            "R1": {"terminal_id": "R1", "ip": "172.17.90.99",
                   "last_seen": int(time.time())}}


_sent = []


def _fake_send(mac, bcasts, ports=wol.DIRECT_PORTS, timeout=2.0):
    _sent.append((mac, tuple(bcasts)))
    return ["%s:%d" % (b, wol.DEFAULT_PORT) for b in bcasts]


def run():
    now = int(time.time())
    # 纯函数
    assert wol.normalize_mac("aa-bb-cc-dd-ee-ff") == "AABBCCDDEEFF"
    assert wol.derive_broadcast("172.17.90.18") == "172.17.90.255"
    assert wol.same_subnet("172.17.90.18", "172.17.90.99")
    assert not wol.same_subnet("172.17.90.18", "172.17.91.99")
    try:
        wol.validate_broadcast("192.168.1")
        raise AssertionError("validate_broadcast should reject")
    except ValueError:
        pass

    # 环境确定性：桩掉本机源 IP 探测（默认与目标同 /24 → 直发适用）
    wol._local_ip_for = lambda ip: "172.17.90.1"

    # 1) 到期直发 → running
    wol.direct_send = _fake_send
    pc = FakePC([_mk_sched()])
    store = FakeStore(_base_terms())
    ctx = FakeCtx(store, pc)
    wol.wol_tick(ctx)
    s = pc.scheds[0]
    assert s["run_state"] == "running" and s["direct_ts"], s
    assert s["last_run_date"] == time.strftime("%Y-%m-%d")
    assert _sent and _sent[0][0] == "AABBCCDDEEFF", _sent
    assert "172.17.90.255" in _sent[0][1], _sent
    assert any(a[2] == "direct" and a[4] for a in pc.attempts)

    # 2) 观察窗内不升级（目标机未上线）
    wol.wol_tick(ctx)
    assert pc.scheds[0]["run_state"] == "running", pc.scheds[0]
    assert not store.cmds, store.cmds

    # 3) 观察窗耗尽 → 中继升级（R1 同网段在线）
    pc.wol_schedule_mark(1, direct_ts=now - wol.WAKE_WAIT_SEC - 5)
    wol.wol_tick(ctx)
    s = pc.scheds[0]
    assert s["run_state"] == "relay_sent" and s["relay_cid"] == 777, s
    assert s["relay_tid"] == "R1", s
    assert store.cmds and store.cmds[0][0] == "R1" \
        and store.cmds[0][1] == "wol_relay", store.cmds
    args = store.cmds[0][2]
    assert args == {"mac": "AABBCCDDEEFF", "broadcast": "172.17.90.255",
                    "port": 9}, args

    # 4) 目标机上线 → 确认 done
    store.terms["T1"]["last_seen"] = int(time.time())
    wol.wol_tick(ctx)
    s = pc.scheds[0]
    assert s["run_state"] == "done" and "已上线" in s["last_result"], s
    assert any(a[2] == "confirm" and a[4] for a in pc.attempts)

    # 5) 无中继可选举 → 直发观察窗耗尽后 failed
    pc2 = FakePC([_mk_sched()])
    store2 = FakeStore({"T1": {"terminal_id": "T1", "ip": "172.17.90.215",
                               "last_seen": 1000}})
    ctx2 = FakeCtx(store2, pc2)
    wol.wol_tick(ctx2)
    assert pc2.scheds[0]["run_state"] == "running"
    pc2.wol_schedule_mark(1, direct_ts=int(time.time())
                          - wol.WAKE_WAIT_SEC - 5)
    wol.wol_tick(ctx2)
    s2 = pc2.scheds[0]
    assert s2["run_state"] == "failed" and "无在线中继" in s2["last_result"], s2

    # 6) 中继后仍未上线 → giveup 结论（附命令回执摘要）
    pc3 = FakePC([_mk_sched(run_state="relay_sent",
                            direct_ts=now - 2 * wol.WAKE_WAIT_SEC,
                            relay_ts=now - wol.WAKE_WAIT_SEC - 5,
                            relay_cid=777, relay_tid="R1",
                            last_run_date=time.strftime("%Y-%m-%d"))])
    ctx3 = FakeCtx(FakeStore(_base_terms(last_seen=1000)), pc3)
    wol.wol_tick(ctx3)
    s3 = pc3.scheds[0]
    assert s3["run_state"] == "failed" and "直发+中继" in s3["last_result"], s3
    assert any(a[2] == "giveup" for a in pc3.attempts)

    # 7) 停用的计划不触发
    pc4 = FakePC([_mk_sched(enabled=0)])
    wol.wol_tick(FakeCtx(FakeStore(_base_terms()), pc4))
    assert pc4.scheds[0]["run_state"] == "" and not pc4.marks

    # 8) 跨网段目标 + auto → 跳过直发直接中继（R1 同网段在线）
    wol._local_ip_for = lambda ip: "172.17.5.215"   # 服务器在 5 段
    assert not wol.direct_applicable(FakeStore(_base_terms()), "T1")
    wol.direct_send = _fake_send
    n_sent = len(_sent)
    pc5 = FakePC([_mk_sched()])
    store5 = FakeStore(_base_terms())
    ctx5 = FakeCtx(store5, pc5)
    wol.wol_tick(ctx5)
    s5 = pc5.scheds[0]
    assert s5["run_state"] == "relay_sent" and not s5["direct_ts"], s5
    assert s5["relay_tid"] == "R1" and s5["relay_cid"] == 777, s5
    assert "跳过直发" in s5["last_result"], s5
    assert store5.cmds and store5.cmds[0][1] == "wol_relay", store5.cmds
    assert len(_sent) == n_sent, "cross-subnet auto must NOT direct-send"
    assert any(a[2] == "relay" and a[4] for a in pc5.attempts)

    # 9) 跨网段 relay 观察窗耗尽 → giveup（无 direct_ts → 「中继未唤醒」）
    pc6 = FakePC([_mk_sched(run_state="relay_sent",
                            relay_ts=now - wol.WAKE_WAIT_SEC - 5,
                            relay_cid=777, relay_tid="R1",
                            last_run_date=time.strftime("%Y-%m-%d"))])
    wol.wol_tick(FakeCtx(FakeStore(_base_terms(last_seen=1000)), pc6))
    s6 = pc6.scheds[0]
    assert s6["run_state"] == "failed" and "中继未唤醒" in s6["last_result"], s6

    # 10) 跨网段 + method=relay 显式指定 → 同样跳直发（措辞区分）
    pc7 = FakePC([_mk_sched(method="relay")])
    store7 = FakeStore(_base_terms())
    wol.wol_tick(FakeCtx(store7, pc7))
    s7 = pc7.scheds[0]
    assert s7["run_state"] == "relay_sent" and not s7["direct_ts"], s7
    assert "已指定中继方式" in s7["last_result"], s7

    # 11) 跨网段 + method=direct 显式指定 → 仍直发（用户意志优先）
    wol.direct_send = _fake_send
    pc8 = FakePC([_mk_sched(method="direct")])
    store8 = FakeStore(_base_terms())
    wol.wol_tick(FakeCtx(store8, pc8))
    s8 = pc8.scheds[0]
    assert s8["run_state"] == "running" and s8["direct_ts"], s8
    assert _sent, "method=direct override must direct-send"

    # 12) 同网段 + auto → 直发适用（回归保护）
    wol._local_ip_for = lambda ip: "172.17.90.1"
    st9 = FakeStore(_base_terms())
    assert wol.direct_applicable(st9, "T1")

    # 13) nad 模式 + 接口未接入 → 如实留痕后回退中继链（不哑等、不漏跑）
    pc9 = FakePC([_mk_sched()])
    ctx9 = FakeCtx(FakeStore(_base_terms()), pc9,
                   settings={"wol.wake_mode": "nad"})
    wol.wol_tick(ctx9)
    s9 = pc9.scheds[0]
    nad_att = [a for a in pc9.attempts if a[2] == "nad"]
    assert nad_att and nad_att[0][4] == 0 and "未接入" in nad_att[0][5], \
        pc9.attempts
    assert s9["run_state"] == "relay_sent" and not s9["direct_ts"], s9
    assert "nad 模式回退中继" in s9["last_result"], s9

    # 14) nad 模式 + url 已配置但适配器未接通 → 留痕「规格待交付」并回退
    pc10 = FakePC([_mk_sched()])
    ctx10 = FakeCtx(FakeStore(_base_terms()), pc10,
                    settings={"wol.wake_mode": "nad",
                              "nad.wol_api_url": "https://nad.example/api/wol"})
    wol.wol_tick(ctx10)
    att10 = [a for a in pc10.attempts if a[2] == "nad"]
    assert att10 and "规格待交付" in att10[0][5], pc10.attempts
    assert pc10.scheds[0]["run_state"] == "relay_sent", pc10.scheds[0]

    # 15) 纯函数：wake_via_nad 两态均诚实失败 + _wake_mode 非法值兜底
    ok_a, msg_a = wol.wake_via_nad(
        FakeCtx(None, None, settings={}), None, "T1")
    ok_b, msg_b = wol.wake_via_nad(
        FakeCtx(None, None, settings={"nad.wol_api_url": "https://x/y"}),
        None, "T1")
    assert ok_a is False and "未接入" in msg_a and "回退中继模式" in msg_a
    assert ok_b is False and "规格待交付" in msg_b and "回退中继模式" in msg_b
    assert wol._wake_mode(FakeCtx(None, None, settings={})) == "relay"
    assert wol._wake_mode(FakeCtx(
        None, None, settings={"wol.wake_mode": "bogus"})) == "relay"
    assert wol._wake_mode(FakeCtx(
        None, None, settings={"wol.wake_mode": "nad"})) == "nad"

    # 16) 触发即落「当日已跑」标记（2026-09-19 缺陷修复）：中继路径也要写。
    #     原实现只有 _fire_direct 写 last_run_date，跨网段走中继的任务该字段
    #     恒空 → 触发条件恒真 → 成功后反复重开链（生产实测一早晨 29 个魔术包）。
    wol._local_ip_for = lambda ip: "172.17.5.215"        # 跨网段 → 走中继
    pc11 = FakePC([_mk_sched(method="relay")])
    store11 = FakeStore(_base_terms(last_seen=1000))
    ctx11 = FakeCtx(store11, pc11)
    wol.wol_tick(ctx11)
    s11 = pc11.scheds[0]
    assert s11["run_state"] == "relay_sent", s11
    assert s11["last_run_date"] == time.strftime("%Y-%m-%d"), \
        "中继路径触发时必须落当日已跑标记：%s" % s11

    # 17) 当日已跑 + 已完成（done）→ 不得被重新开链，成功结论不被覆盖
    #     （核心回归：修复前 done 会被下一节拍覆盖回 relay_sent 并继续发包）
    store11.cmds[:] = []
    pc11.attempts[:] = []
    pc11.wol_schedule_mark(1, run_state="done",
                           last_result="目标机已上线（09-19 07:31）")
    for _ in range(3):                       # 连续多个节拍均不得重开
        wol.wol_tick(ctx11)
    assert not store11.cmds, "已跑任务不得重新开链：%s" % store11.cmds
    assert not pc11.attempts, "已跑任务不得新增尝试：%s" % pc11.attempts
    assert pc11.scheds[0]["run_state"] == "done", pc11.scheds[0]
    assert "07:31" in pc11.scheds[0]["last_result"], \
        "成功结论不得被后续节拍覆盖：%s" % pc11.scheds[0]

    print("wol state machine tests: 17/17 groups passed")


if __name__ == "__main__":
    run()
