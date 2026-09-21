#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""IP 冲突检测判定精化（ADR-028）本地单测：判定矩阵/MAC 归一/窗口/路由/降级。

零凭据零网络：临时库（隔离 config/db，测试数据禁触生产库——ADR-028 规范）；
nad 未配置走 not_configured 降级路径。运行后自清理临时目录。"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import api as api_mod                                   # noqa: E402
import store as store_mod                               # noqa: E402

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


def _mk_store(tmp, name):
    st = store_mod.Store(os.path.join(tmp, name + ".db"), config_token="tk")
    st.register_terminal("T-A", "windows", "host-a", "Windows 11",
                         "1.0.0", "127.0.0.1")
    st.register_terminal("T-B", "windows", "host-b", "Windows 11",
                         "1.0.0", "127.0.0.1")
    c = st._conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist(cidr,note,enabled,created_at)"
              " VALUES(?,?,1,?)", ("127.0.0.1", "test", int(time.time())))
    st._conn.commit()
    c.close()
    return st


def _old_report(st, terminal_id, ip, mac, days_ago):
    """直接写历史记录（绕过判定，构造窗口外数据）。"""
    c = st._conn.cursor()
    c.execute("INSERT INTO ipconflict_reports(terminal_id,ip,mac,created_ts)"
              " VALUES(?,?,?,?)",
              (terminal_id, ip, mac, int(time.time()) - days_ago * 86400))
    st._conn.commit()
    c.close()


def run(tmp):
    # [1] 判定矩阵
    print("[1] store.ipconflict_report 判定矩阵")
    st = _mk_store(tmp, "t1")
    v = st.ipconflict_report("T-A", "10.1.1.5", "AA-BB-CC-DD-EE-01")
    check("清理后空证据不报错", v["conflict_suspect"] is False
          and v["evidence"] == [] and v["nic_history"] == []
          and v["suspect_reasons"] == [], str(v)[:120])
    # 同终端换 MAC（真实网卡变更/虚拟适配器）：不 suspect，记 nic_history
    v2 = st.ipconflict_report("T-A", "10.1.1.5", "11-22-33-44-55-66")
    check("同终端换 MAC 不误报", v2["conflict_suspect"] is False
          and v2["evidence"] == [], str(v2)[:120])
    check("同终端换 MAC 记录网卡变更史",
          len(v2["nic_history"]) == 1
          and v2["nic_history"][0]["mac"] == "AA-BB-CC-DD-EE-01"
          and v2["nic_history"][0]["last_ts"] > 0, str(v2["nic_history"]))
    # 同网卡异格式重复上报（四位段格式）：归一识别为同一网卡，不新增历史
    v3 = st.ipconflict_report("T-A", "10.1.1.5", "aabb.ccdd.ee01")
    check("MAC 归一（四位段=连字符同键不新增历史）",
          v3["conflict_suspect"] is False and len(v3["nic_history"]) == 1,
          str(v3["nic_history"]))
    # 真正的第三块网卡：nic_history 累积
    v3b = st.ipconflict_report("T-A", "10.1.1.5", "AABB.CCDD.EEFF")
    check("第三块网卡 nic_history 累积",
          v3b["conflict_suspect"] is False and len(v3b["nic_history"]) == 2
          and {h["mac"] for h in v3b["nic_history"]} ==
          {"AA-BB-CC-DD-EE-01", "11-22-33-44-55-66"}, str(v3b["nic_history"]))
    # 双终端同 IP：suspect 保留（真冲突信号）
    v4 = st.ipconflict_report("T-B", "10.1.1.5", "22-33-44-55-66-77")
    check("双终端同 IP suspect", v4["conflict_suspect"] is True
          and v4["suspect_reasons"] == ["multi_terminal"]
          and len(v4["evidence"]) == 3, str(v4)[:160])
    # 未知 terminal_id：保守 suspect
    v5 = st.ipconflict_report("T-B", "10.1.1.9", "33-44-55-66-77-88")
    check("同终端新 IP 不误报", v5["conflict_suspect"] is False)
    _old_report(st, "T-GHOST", "10.1.1.9", "44-55-66-77-88-99", 1)
    v6 = st.ipconflict_report("T-B", "10.1.1.9", "33-44-55-66-77-88")
    check("未知 terminal_id 保守 suspect", v6["conflict_suspect"] is True
          and v6["suspect_reasons"] == ["unknown_terminal"]
          and v6["evidence"][0]["known"] is False, str(v6)[:160])
    # 7 天窗口：8 天前记录不参与
    _old_report(st, "T-B", "10.1.1.7", "55-66-77-88-99-AA", 8)
    v7 = st.ipconflict_report("T-A", "10.1.1.7", "66-77-88-99-AA-BB")
    check("窗口外记录不参与判定", v7["conflict_suspect"] is False
          and v7["evidence"] == [], str(v7)[:120])
    _old_report(st, "T-B", "10.1.1.7", "55-66-77-88-99-AA", 2)
    v8 = st.ipconflict_report("T-A", "10.1.1.7", "66-77-88-99-AA-BB")
    check("窗口内 foreign 记录参与判定", v8["conflict_suspect"] is True
          and v8["suspect_reasons"] == ["multi_terminal"])

    # [2] dispatch 路由 + nad 未配置降级（全新 IP，避免 [1] 判定数据耦合）
    print("[2] 路由与降级（隔离库，无第三方配置）")
    ctx = api_mod.ApiContext(st, {"terminal_token": "tk"},
                             settings=FakeSettings({}))
    try:
        status, payload, _ctype = api_mod.dispatch(
            ctx, "POST", "/api/v1/terminals/T-A/netdoctor/ipconflict",
            {}, {"x-etp-token": "tk"},
            b'{"ip": "10.2.2.2", "mac": "AA-BB-CC-DD-EE-01"}', "127.0.0.1")
        body = json.loads(payload.decode("utf-8"))
        vd = body.get("verdict") or {}
        check("路由 200 verdict 结构完整", status == 200
              and vd.get("conflict_suspect") is not None
              and "evidence" in vd and "nic_history" in vd
              and "suspect_reasons" in vd
              and (vd.get("sources") or {}).get("terminal_reports") == "ok",
              str(vd)[:160])
        check("nad 未配置降级 not_configured",
              (vd.get("sources") or {}).get("admission_log") in
              ("not_configured", "not_connected"), str(vd.get("sources")))
        check("路由 verdict 与 store 判定一致（新 IP 干净判定）",
              vd.get("conflict_suspect") is False
              and vd.get("nic_history") == []
              and vd.get("suspect_reasons") == [])
        status2, payload2, _c2 = api_mod.dispatch(
            ctx, "POST", "/api/v1/terminals/T-A/netdoctor/ipconflict",
            {}, {"x-etp-token": "tk"},
            b'{"ip": "10.2.2.2", "mac": "aa-bb-cc-dd-ee-01"}', "127.0.0.1")
        vd2 = (json.loads(payload2.decode("utf-8")).get("verdict") or {})
        check("路由层同网卡异格式不新增历史", vd2.get("conflict_suspect") is False
              and vd2.get("nic_history") == [])
    except api_mod.ApiError as exc:
        check("路由 200 verdict 结构完整", False, "ApiError %s" % exc.status)
    # 参数缺失 400
    for payload_b in (b'{"mac": "AA-BB-CC-DD-EE-01"}', b'{"ip": "10.1.1.5"}'):
        try:
            api_mod.dispatch(
                ctx, "POST", "/api/v1/terminals/T-A/netdoctor/ipconflict",
                {}, {"x-etp-token": "tk"}, payload_b, "127.0.0.1")
            check("缺参 400 (%s)" % payload_b.decode()[:12], False)
        except api_mod.ApiError as exc:
            check("缺参 400 (%s)" % payload_b.decode()[:12], exc.status == 400)


def main():
    tmp = tempfile.mkdtemp(prefix="etp_ipconflict_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== ipconflict tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
