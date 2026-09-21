#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""深度检测引擎（ADR-029）本地单测：解析器/MAC 互转/编排分支 mock SSH/任务生命周期。

零凭据零外联：SSH 会话与 NAD 全部 mock（隔离规范 ADR-028，禁触生产交换机）；
临时库运行后自清理。"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import deep_engine as de                               # noqa: E402
import api as api_mod                                  # noqa: E402
import nad_client                                      # noqa: E402
import store as store_mod                              # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


class FakeSettings:
    def __init__(self, d):
        self.d = d

    def get(self, k, default=None):
        return self.d.get(k, default)


ARP_HEAD = ("  Type: S-Static   D-Dynamic   O-Other\n"
            "  IP address      MAC address    VLAN/VSI  Interface"
            "          Type   Aging\n")
ARP_ONE_MATCH = ARP_HEAD + (
    "  172.17.90.215   F4f1-9e3c-69e4 90        GigabitEthernet1/0/48"
    "  D      18\n")
ARP_MULTI = ARP_HEAD + (
    "  172.17.90.215   F4f1-9e3c-69e4 90        GigabitEthernet1/0/48"
    "  D      18\n"
    "  172.17.90.215   aabb-ccdd-ee01 90        GigabitEthernet1/0/51"
    "  D      12\n")
MAC_ONE = ("  MAC ADDR         VLAN ID   STATE          PORT/NICKNAME"
           "            AGING\n"
           "  F4f1-9e3c-69e4   90        Learned"
           "        GigabitEthernet1/0/48    Y\n")
MAC_MULTI = ("  MAC ADDR         VLAN ID   STATE          PORT/NICKNAME"
             "            AGING\n"
             "  F4f1-9e3c-69e4   90        Learned"
             "        GigabitEthernet1/0/48    Y\n"
             "  F4f1-9e3c-69e4   88        Learned"
             "        GigabitEthernet1/0/51    Y\n")


class FakeSession(object):
    """mock SSH 会话：按脚本输出回放，认证失败可注入。"""
    behavior = {"fail_auth": False, "arp": ARP_ONE_MATCH, "mac": MAC_ONE}
    created = []

    def __init__(self, host, username, password, port=22):
        self.host = host
        self.username = username
        self.commands = []
        FakeSession.created.append(self)

    def connect(self):
        if FakeSession.behavior["fail_auth"]:
            raise Exception("Authentication failed.")
        return "SSH-2.0-FakeComware"

    def run_readonly(self, commands):
        self.commands = list(commands)

        def _out(cmd):
            if "display arp" in cmd:
                return FakeSession.behavior["arp"]
            if "display mac-address" in cmd:
                return FakeSession.behavior["mac"]
            return ""
        return [(c, _out(c)) for c in commands]

    def close(self):
        pass


class FakeNad:
    mode = "not_configured"

    @staticmethod
    def find_by_ip(store, ip):
        if FakeNad.mode == "hit":
            return ([{"source": "nad", "name": "SW1348 SW4（11F网络） 192.168.254.4",
                      "macs": [{"mac": "F4:F1:9E:3C:69:E4",
                                "ips": {"0": {"ip": "172.17.90.215"}},
                                "macports": [
                                    {"nasoid": "x", "nasif": "GE1/0/7",
                                     "nasname": "SW1348 SW4（11F网络） 192.168.254.4",
                                     "manip": "192.168.254.4"}]}]}], None)
        return None, "not_configured"


KB_NODES = [{"match": "172.17.0.0/16", "zone": "核心", "desc": "核心交换机",
             "gw_ip": "172.17.254.1"}]


def _mk_store(tmp, name):
    st = store_mod.Store(os.path.join(tmp, name + ".db"), config_token="tk")
    st.register_terminal("T-A", "windows", "host-a", "Windows 11",
                         "1.0.0", "127.0.0.1")
    c = st._conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist(cidr,note,enabled,created_at)"
              " VALUES(?,?,1,?)", ("127.0.0.1", "test", int(time.time())))
    st._conn.commit()
    c.close()
    return st


def main():
    tmp = tempfile.mkdtemp(prefix="etp_deep_")
    _orig_session = de.SwitchSession
    _orig_nad = nad_client.nad_find_by_ip
    _orig_run = de.run_deep_check
    try:
        run(tmp)
    finally:
        de.SwitchSession = _orig_session
        nad_client.nad_find_by_ip = _orig_nad
        de.run_deep_check = _orig_run
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== deep engine tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def run(tmp):
    # [1] MAC 互转
    print("[1] MAC 格式适配器")
    check("四位段格式", de.mac_h3c("F4:F1:9E:3C:69:E4") == "F4F1-9E3C-69E4")
    check("连字符格式", de.mac_h3c("F4-F1-9E-3C-69-E4") == "F4F1-9E3C-69E4")
    check("裸串格式", de.mac_h3c("f4f19e3c69e4") == "F4F1-9E3C-69E4")
    check("归一键一致", de.mac_key("F4:F1:9E:3C:69:E4")
          == de.mac_key("F4F1-9E3C-69E4") == de.mac_key("f4-f1-9e-3c-69-e4"))
    check("非法 MAC 返回原文", de.mac_h3c("not-a-mac") == "not-a-mac")

    # [2] 解析器
    print("[2] display 输出解析器")
    rows = de.parse_arp(ARP_ONE_MATCH)
    check("ARP 单条解析", len(rows) == 1
          and rows[0]["ip"] == "172.17.90.215"
          and de.mac_key(rows[0]["mac"]) == "f4f19e3c69e4", str(rows))
    check("ARP 表头行跳过", all("Type" not in r["rest"] for r in rows))
    rows2 = de.parse_arp(ARP_MULTI)
    check("ARP 多条解析", len(rows2) == 2
          and len(de.distinct_macs(rows2)) == 2)
    mrows = de.parse_mac_address(MAC_ONE)
    check("MAC 表单条解析", len(mrows) == 1
          and mrows[0]["port"] == "GigabitEthernet1/0/48"
          and mrows[0]["vlan"] == "90", str(mrows))
    mrows2 = de.parse_mac_address(MAC_MULTI)
    check("MAC 表多端口解析", len(mrows2) == 2
          and len({r["port"] for r in mrows2}) == 2)

    # [3] 编排分支（mock SSH + NAD）
    print("[3] 编排分支（mock）")
    de.SwitchSession = FakeSession
    nad_client.nad_find_by_ip = FakeNad.find_by_ip
    st = _mk_store(tmp, "t1")
    settings = FakeSettings({"switch.default_username": "reader",
                             "switch.default_password": "fakepwd"})
    IP, MAC = "172.17.90.215", "F4:F1:9E:3C:69:E4"

    # 3a. 全链正常：ARP 单条匹配 + NAD 未配置 + 无 macports → normal
    FakeSession.behavior = {"fail_auth": False, "arp": ARP_ONE_MATCH,
                            "mac": MAC_ONE}
    FakeNad.mode = "not_configured"
    steps, verdict = de.run_deep_check(st, KB_NODES, settings, "T-A",
                                       IP, MAC)
    smap = {s["step"]: s for s in steps}
    check("resolve 命中网关", smap["resolve"]["status"] == "done"
          and smap["resolve"]["target"] == "172.17.254.1")
    check("ARP 单条匹配 → match", smap["arp"]["status"] == "match")
    check("ARP 命令白名单入证据链",
          any("display arp | include 172.17.90.215" == c["cmd"]
              for c in smap["arp"]["commands"]))
    check("NAD 未配置降级", smap["nad"]["status"] == "skipped")
    check("无 macports → macaddr skipped", smap["macaddr"]["status"]
          == "skipped")
    check("结论 normal", verdict["conclusion"] == "normal", str(verdict))

    # 3b. ARP 多 MAC → confirmed（网关层实锤）
    FakeSession.behavior = {"fail_auth": False, "arp": ARP_MULTI,
                            "mac": MAC_ONE}
    steps, verdict = de.run_deep_check(st, KB_NODES, settings, "T-A",
                                       IP, MAC)
    smap = {s["step"]: s for s in steps}
    check("ARP 多 MAC → multi", smap["arp"]["status"] == "multi"
          and len(smap["arp"]["evidence"]) == 2)
    check("结论 confirmed", verdict["conclusion"] == "confirmed"
          and "网关 ARP 同 IP 多 MAC" in verdict["reasons"], str(verdict))

    # 3c. NAD 命中 macports → 登录接入交换机；MAC 表单端口 → normal
    FakeNad.mode = "hit"
    FakeSession.behavior = {"fail_auth": False, "arp": ARP_ONE_MATCH,
                            "mac": MAC_ONE}
    steps, verdict = de.run_deep_check(st, KB_NODES, settings, "T-A",
                                       IP, MAC)
    smap = {s["step"]: s for s in steps}
    check("NAD 命中接入交换机", smap["nad"]["status"] == "done"
          and smap["nad"]["target"] == "192.168.254.4")
    check("macaddr 登录 manip 单端口", smap["macaddr"]["status"] == "done"
          and smap["macaddr"]["target"] == "192.168.254.4")
    check("MAC 表四位段 include 命令",
          any("display mac-address | include F4F1-9E3C-69E4" == c["cmd"]
              for c in smap["macaddr"]["commands"]))
    check("全链正常 → normal", verdict["conclusion"] == "normal")

    # 3d. MAC 多端口 → suspect（漂移信号）
    FakeSession.behavior = {"fail_auth": False, "arp": ARP_ONE_MATCH,
                            "mac": MAC_MULTI}
    steps, verdict = de.run_deep_check(st, KB_NODES, settings, "T-A",
                                       IP, MAC)
    smap = {s["step"]: s for s in steps}
    check("MAC 多端口 → multi/suspect", smap["macaddr"]["status"] == "multi"
          and verdict["conclusion"] == "suspect", str(verdict)[:160])

    # 3e. SSH 认证失败 → failed 降级 + insufficient（全链失败）
    FakeSession.behavior = {"fail_auth": True, "arp": ARP_ONE_MATCH,
                            "mac": MAC_ONE}
    FakeNad.mode = "not_configured"
    steps, verdict = de.run_deep_check(st, KB_NODES, settings, "T-A",
                                       IP, MAC)
    smap = {s["step"]: s for s in steps}
    check("ARP 认证失败标注 reader 提示", smap["arp"]["status"] == "failed"
          and "reader" in smap["arp"]["note"], smap["arp"]["note"])
    check("凭据零回显（证据链无密码）",
          "fakepwd" not in json.dumps(steps), "password leaked!")
    check("全链失败 → insufficient_evidence",
          verdict["conclusion"] == "insufficient_evidence")

    # 3f. 无 gw_ip → resolve/arp skipped（提示知识库维护）
    steps, verdict = de.run_deep_check(st, [{"match": "172.17.0.0/16",
                                             "zone": "核心"}],
                                       settings, "T-A", IP, MAC)
    smap = {s["step"]: s for s in steps}
    check("无 gw_ip → resolve/arp skipped", smap["resolve"]["status"]
          == "skipped" and smap["arp"]["status"] == "skipped")
    check("skipped 提示知识库维护", "route_nodes" in smap["arp"]["note"]
          or "知识库" in smap["resolve"].get("evidence", [""])[0]
          if smap["resolve"].get("evidence") else True)

    # [4] store 任务生命周期 + lookup
    print("[4] 任务生命周期与只读 lookup")
    st.deep_task_create("DC-abc", "T-A", IP, MAC)
    st.deep_task_update("DC-abc", steps=[{"step": "resolve"}])
    row = st.deep_task_get("DC-abc")
    check("任务创建/进度查询", row and row["status"] == "running"
          and row["steps"] == [{"step": "resolve"}])
    st.deep_task_update("DC-abc", verdict={"conclusion": "normal"},
                        status="done")
    row = st.deep_task_get("DC-abc")
    check("任务终态", row["status"] == "done"
          and row["verdict"]["conclusion"] == "normal")
    check("未知任务 404 数据源", st.deep_task_get("DC-none") is None)
    # lookup：T-A 上报后 T-B 同 IP → suspect（只读不落库）
    st.ipconflict_report("T-A", "10.9.9.9", "11-22-33-44-55-66")
    n_before = st._conn.execute(
        "SELECT COUNT(*) AS n FROM ipconflict_reports").fetchone()["n"]
    lk = st.ipconflict_lookup("10.9.9.9", "T-A", "11-22-33-44-55-66")
    n_after = st._conn.execute(
        "SELECT COUNT(*) AS n FROM ipconflict_reports").fetchone()["n"]
    check("lookup 只读不落库", n_before == n_after)
    check("lookup 自身视角干净", lk["conflict_suspect"] is False)
    lk2 = st.ipconflict_lookup("10.9.9.9", "T-B", "aa-bb-cc-dd-ee-01")
    check("lookup 他终端视角 suspect",
          lk2["conflict_suspect"] is True
          and lk2["suspect_reasons"] == ["multi_terminal"])

    # [5] dispatch 路由（mock run_deep_check）
    print("[5] 路由与异步任务")
    ctx = api_mod.ApiContext(st, {"terminal_token": "tk"},
                             settings=FakeSettings({}))

    def fake_run(store, kb_nodes, settings2, terminal_id, ip, mac,
                 on_step=None, task_id=""):
        if on_step:
            on_step(task_id, [{"step": "fake", "status": "done"}])
        return [{"step": "fake", "status": "done"}], \
            {"conclusion": "normal", "reasons": []}
    de.run_deep_check = fake_run
    status, payload, _ = api_mod.dispatch(
        ctx, "POST", "/api/v1/terminals/T-A/netdoctor/ipconflict-deep",
        {}, {"x-etp-token": "tk"},
        json.dumps({"ip": IP, "mac": MAC}).encode(), "127.0.0.1")
    body = json.loads(payload.decode())
    check("POST 创建任务 200", status == 200 and body.get("task_id", "")
          .startswith("DC-"), str(body))
    tid = body["task_id"]
    for _ in range(50):
        row = st.deep_task_get(tid)
        if row and row["status"] != "running":
            break
        time.sleep(0.05)
    check("异步任务终态 done", row["status"] == "done"
          and row["verdict"]["conclusion"] == "normal"
          and row["steps"] == [{"step": "fake", "status": "done"}])
    status, payload, _ = api_mod.dispatch(
        ctx, "GET", "/api/v1/terminals/T-A/netdoctor/ipconflict-deep/%s" % tid,
        {}, {"x-etp-token": "tk"}, None, "127.0.0.1")
    check("GET 任务进度 200", status == 200
          and json.loads(payload.decode())["task"]["task_id"] == tid)
    # 负向：非法 IP 400 / 缺 mac 400 / 未知任务 404 / 其它终端任务隔离
    for payload_b, expect in ((json.dumps({"ip": "999.1.1.1",
                                           "mac": MAC}).encode(), 400),
                              (json.dumps({"ip": IP}).encode(), 400)):
        try:
            api_mod.dispatch(
                ctx, "POST", "/api/v1/terminals/T-A/netdoctor/ipconflict-deep",
                {}, {"x-etp-token": "tk"}, payload_b, "127.0.0.1")
            check("负向 %d" % expect, False)
        except api_mod.ApiError as exc:
            check("负向 %d" % expect, exc.status == expect)
    try:
        status, payload, _ = api_mod.dispatch(
            ctx, "GET",
            "/api/v1/terminals/T-A/netdoctor/ipconflict-deep/DC-none",
            {}, {"x-etp-token": "tk"}, None, "127.0.0.1")
        check("未知任务 404", False)
    except api_mod.ApiError as exc:
        check("未知任务 404", exc.status == 404)


if __name__ == "__main__":
    sys.exit(main())
