#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""画方准入客户端（ADR-024）本地单测：签名/配置三态/缓存/IP·MAC 索引/证据注入。

零网络：mock nad_fetch_terms；临时库自清理。"""
import hashlib
import hmac
import importlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import api as api_mod                                  # noqa: E402
import nad_client                                      # noqa: E402
import store as store_mod                              # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


def seed_nad_config(st, enabled=1, params=None, base="https://172.17.254.250:9002"):
    c = st._conn.cursor()
    c.execute("DELETE FROM third_party_apis WHERE id=2")
    c.execute(
        "INSERT INTO third_party_apis(id,name,base_url,method,params_json,"
        "headers_json,enabled,note,created_ts,updated_ts)"
        " VALUES(2,'nad',?,'POST',?,'{}',?,'test',0,0)",
        (base, json.dumps(params or {"app_key": "k-test", "app_secret":
                                     "s-test", "enctype": 0,
                                     "version": "v2.0"}), enabled))
    st._conn.commit()
    c.close()


def fake_terms():
    """归一化后形态（nad_fetch_terms 输出契约）。"""
    return [
        {"oid": 101, "name": "WIN-Jun-office-PC",
         "ou": {"namepath": "信息科/办公区"}, "ttype": "pc",
         "manfct": "Dell", "model": "OptiPlex", "online": 1, "block": 0,
         "reginfo": {"stat": "registered"},
         "macs": [{"mac": "AA-BB-CC-DD-EE-01",
                   "ips": [{"ip": "172.17.5.100", "ipver": 4}],
                   "macports": [{"nasoid": 9, "nasif": "Gi0/1",
                                 "nasname": "SW0691",
                                 "manip": "172.17.254.250"}]}]},
        {"oid": 102, "name": "printer-3f",
         "ou": {"namepath": "信息科/三楼"}, "ttype": "printer",
         "manfct": "HP", "model": "M405", "online": 0, "block": 1,
         "reginfo": {"stat": "registered"},
         "macs": [{"mac": "aa:bb:cc:dd:ee:02",
                   "ips": [{"ip": "172.17.5.100", "ipver": 4}],   # 同 IP 另一 MAC
                   "macports": []}]},
        {"oid": 103, "name": "guest-pad", "ou": {"namepath": "访客"},
         "ttype": "mobile", "manfct": "Apple", "model": "iPad",
         "online": 1, "block": 0, "reginfo": {"stat": "registered"},
         "macs": [{"mac": "aabbccdde003",
                   "ips": [{"ip": "10.9.9.9", "ipver": 4}],
                   "macports": []}]},
    ]


def raw_nad_page():
    """实测原始响应形态样本（探针 2026-09-09 捕获）。"""
    return {
        "errno": 0, "errmsg": "ok",
        "data": {"total": 1588, "list": {
            "0": {"oid": 9001, "name": "SW0691", "ou": {"namepath": "核心"},
                  "macs": {"0": {"mac": "EC:CD:4C:26:6E:2C",
                                 "ips": {"0": {"ip": "172.17.90.1",
                                               "ipver": 1}},
                                 "macports": None}}},
            "1": {"oid": 9002, "name": "pc-2", "ou": {"namepath": "办公"},
                  "macs": {"0": {"mac": "EC-CD-4C-26-6E-3D",
                                 "ips": [{"ip": "172.17.90.2"}],
                                 "macports": [{"nasoid": 1,
                                               "nasname": "SW01"}]}}},
        }},
    }


_orig_fetch = nad_client.nad_fetch_terms


def main():
    tmp = tempfile.mkdtemp(prefix="etp_nad_")
    try:
        run(tmp)
    finally:
        nad_client.nad_fetch_terms = _orig_fetch
        nad_client.nad_cache_invalidate()
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== nad client tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def run(tmp):
    # [1] 签名构造（独立实现对照）
    print("[1] nad_sign 签名")
    expect = hmac.new(b"s-test", b"appkey=k-test&nonce=1234567890&time=1700000000",
                      hashlib.sha256).hexdigest()
    check("hmac_sha256 hex 与独立实现一致",
          nad_client.nad_sign("s-test", "k-test", "1234567890",
                              "1700000000") == expect)
    check("签名消息拼装（time 为字符串）",
          nad_client.nad_sign("s", "k", "n", "t") ==
          hmac.new(b"s", b"appkey=k&nonce=n&time=t",
                   hashlib.sha256).hexdigest())

    # [2] 配置三态
    print("[2] nad_load_config 配置读取")
    db = os.path.join(tmp, "t.db")
    st = store_mod.Store(db, config_token="cfg-token")
    seed_nad_config(st, enabled=1)
    cfg = nad_client.nad_load_config(st)
    check("完整配置读取", bool(cfg) and cfg["app_key"] == "k-test"
          and cfg["app_secret"] == "s-test"
          and cfg["base_url"] == "https://172.17.254.250:9002"
          and cfg["enctype"] == 0 and cfg["version"] == "v2.0", str(cfg))
    seed_nad_config(st, enabled=0)
    check("禁用 → None", nad_client.nad_load_config(st) is None)
    seed_nad_config(st, enabled=1, params={"app_key": "k"})
    check("缺 app_secret → None", nad_client.nad_load_config(st) is None)
    seed_nad_config(st, enabled=1, params="not-json")
    check("params_json 坏 → None", nad_client.nad_load_config(st) is None)
    c = st._conn.cursor()
    c.execute("DELETE FROM third_party_apis WHERE id=2")
    st._conn.commit()
    c.close()
    check("登记删除 → None", nad_client.nad_load_config(st) is None)

    # [3] 实测原始形态归一（ADR-024 探针样本）
    print("[3] 原始响应归一")
    page = nad_client._norm_page_list(raw_nad_page()["data"])
    check("list dict → values 归一", isinstance(page, list) and
          len(page) == 2)
    macs0 = nad_client._norm_macs(page[0])
    check("macs 条目对象归一", macs0[0]["mac"] == "EC:CD:4C:26:6E:2C"
          and macs0[0]["ips"] == [{"ip": "172.17.90.1", "ipver": 1}]
          and macs0[0]["macports"] == [])
    macs1 = nad_client._norm_macs(page[1])
    ips1 = nad_client._norm_ips(macs1[0]["ips"])
    check("ips list 形态兼容", ips1 == [{"ip": "172.17.90.2"}]
          and macs1[0]["mac"] == "EC-CD-4C-26-6E-3D")

    # [4] 缓存与 IP/MAC 索引
    print("[4] 缓存与 IP/MAC 索引")
    seed_nad_config(st, enabled=1)
    calls = []

    def fake_fetch(cfg):
        calls.append(cfg["app_key"])
        return fake_terms()

    nad_client.nad_fetch_terms = fake_fetch
    nad_client.nad_cache_invalidate()
    t1, r1 = nad_client.nad_terminals_cached(st)
    t2, r2 = nad_client.nad_terminals_cached(st)
    check("首次拉取成功", t1 is not None and r1 is None and len(t1) == 3)
    check("60s 缓存命中（第二次不拉取）", len(calls) == 1)
    nad_client.nad_cache_invalidate()
    nad_client.nad_terminals_cached(st)
    check("invalidate 后重新拉取", len(calls) == 2)

    hits, reason = nad_client.nad_find_by_ip(st, "172.17.5.100")
    check("按 IP 命中 2 台（不同 MAC）", reason is None and len(hits) == 2,
          str([h.get("name") for h in hits or []]))
    macs_hit = sorted(m.get("mac") for h in hits for m in h.get("macs") or [])
    check("证据含 mac/ips/macports", macs_hit == ["AA-BB-CC-DD-EE-01",
                                                  "aa:bb:cc:dd:ee:02"]
          and hits[0]["macs"][0]["ips"] == ["172.17.5.100"]
          and hits[0]["macs"][0]["macports"][0]["nasname"] == "SW0691")
    check("证据含终端摘要字段", all(
        k in hits[0] for k in ("source", "name", "ou", "ttype", "manfct",
                               "model", "online", "block", "reginfo")))
    check("ou 取 namepath", hits[0]["ou"] == "信息科/办公区")
    hits_mac, _ = nad_client.nad_find_by_mac(st, "AA-BB-CC-DD-EE-01")
    check("按 MAC 命中（大写连字符归一）", hits_mac and
          hits_mac[0]["name"] == "WIN-Jun-office-PC")
    hits_mac2, _ = nad_client.nad_find_by_mac(st, "aabbccdde003")
    check("按 MAC 命中（无分隔符归一）", hits_mac2 and
          hits_mac2[0]["name"] == "guest-pad")
    miss, _ = nad_client.nad_find_by_ip(st, "10.99.99.99")
    check("未命中返回空列表", miss == [])
    nad_client.nad_cache_invalidate()

    def boom(cfg):
        raise OSError("connect timeout")

    nad_client.nad_fetch_terms = boom
    nad_client.nad_cache_invalidate()
    t3, r3 = nad_client.nad_terminals_cached(st)
    check("拉取异常 → (None, error:...)", t3 is None and
          str(r3).startswith("error:"))
    seed_nad_config(st, enabled=0)
    nad_client.nad_cache_invalidate()
    t4, r4 = nad_client.nad_terminals_cached(st)
    check("未配置 → (None, not_configured)", t4 is None
          and r4 == "not_configured")

    # [4] verdict 注入
    print("[4] ipconflict verdict 准入注入")
    seed_nad_config(st, enabled=1)
    nad_client.nad_fetch_terms = lambda cfg: fake_terms()
    nad_client.nad_cache_invalidate()
    st.register_terminal("T-NAD", "windows", "host", "Windows 11",
                         "1.0.0", "127.0.0.1")
    c = st._conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist(cidr,note,enabled,created_at)"
              " VALUES('127.0.0.1','test',1,0)")
    st._conn.commit()
    c.close()

    # 4a' 干净 IP（准入库仅一条登记且 MAC 与上报一致）→ 不误报
    v0 = st.ipconflict_report("T-NAD", "10.9.9.9", "aabbccdde003")
    api_mod._enrich_ipconflict_admission(v0, st, "10.9.9.9", "aabbccdde003")
    check("干净 IP：admission_log=connected",
          v0["sources"]["admission_log"] == "connected")
    check("干净 IP：不误报 suspect", v0["conflict_suspect"] is False)
    check("干净 IP：verdict.admission 证据块",
          (v0.get("admission") or {}).get("name") == "guest-pad")

    # 4a 上报 MAC 与准入一致，但准入库同 IP 另有登记（AA-BB...@172.17.5.100，
    # 准入侧还存在 printer ee:02 同 IP）→ 按任务书判定升级 suspect
    v = st.ipconflict_report("T-NAD", "172.17.5.100", "AA-BB-CC-DD-EE-01")
    api_mod._enrich_ipconflict_admission(v, st, "172.17.5.100",
                                         "AA-BB-CC-DD-EE-01")
    check("同 IP 多登记：admission_log=connected",
          v["sources"]["admission_log"] == "connected")
    check("同 IP 多登记：core_switch_state=nas_connected（有 SW 端口）",
          v["sources"]["core_switch_state"] == "nas_connected")
    check("同 IP 多登记：准入侧另一 MAC → suspect 升级",
          v["conflict_suspect"] is True)
    check("同 IP 多登记：verdict.admission 证据块",
          (v.get("admission") or {}).get("name") == "WIN-Jun-office-PC")

    # 4b 上报 MAC 与准入登记不一致 → 升级 suspect
    v2 = st.ipconflict_report("T-NAD", "172.17.5.100", "11-22-33-44-55-66")
    api_mod._enrich_ipconflict_admission(v2, st, "172.17.5.100",
                                         "11-22-33-44-55-66")
    check("不一致 MAC：suspect 升级 true", v2["conflict_suspect"] is True)
    nad_evs = [e for e in v2["evidence"] if e.get("source") == "nad"]
    check("证据含准入侧登记 MAC 块", len(nad_evs) == 2
          and sorted(m.get("mac") for e in nad_evs
                     for m in e.get("macs") or [])
          == ["AA-BB-CC-DD-EE-01", "aa:bb:cc:dd:ee:02"])
    check("admission_registered_macs", sorted(
        v2.get("admission_registered_macs") or [])
        == ["AA-BB-CC-DD-EE-01", "aa:bb:cc:dd:ee:02"])
    check("不一致 MAC：core_switch_state=not_connected（上报 MAC 不在库）",
          v2["sources"]["core_switch_state"] == "not_connected")
    check("平台内证据保留", any(e.get("mac") == "AA-BB-CC-DD-EE-01"
                                and "source" not in e
                                for e in v2["evidence"]))

    # 4c 上报 MAC 有登记无端口 → registered_no_port
    v3 = st.ipconflict_report("T-NAD", "172.17.5.100", "aa:bb:cc:dd:ee:02")
    api_mod._enrich_ipconflict_admission(v3, st, "172.17.5.100",
                                         "aa:bb:cc:dd:ee:02")
    check("同 IP 另一 MAC（无端口）：core=registered_no_port 且 suspect 升级",
          v3["sources"]["core_switch_state"] == "registered_no_port"
          and v3["conflict_suspect"] is True)

    # 4d 未配置降级
    seed_nad_config(st, enabled=0)
    nad_client.nad_cache_invalidate()
    v4 = st.ipconflict_report("T-NAD", "10.8.8.8", "AA-BB-CC-DD-EE-01")
    api_mod._enrich_ipconflict_admission(v4, st, "10.8.8.8",
                                         "AA-BB-CC-DD-EE-01")
    check("未配置：admission_log=not_configured 不阻断",
          v4["sources"]["admission_log"] == "not_configured"
          and v4["conflict_suspect"] is False
          and v4["sources"]["terminal_reports"] == "ok")

    # 4e 拉取异常降级（恢复启用配置后再模拟故障）
    seed_nad_config(st, enabled=1)
    nad_client.nad_fetch_terms = boom
    nad_client.nad_cache_invalidate()
    v5 = st.ipconflict_report("T-NAD", "10.8.8.9", "AA-BB-CC-DD-EE-01")
    api_mod._enrich_ipconflict_admission(v5, st, "10.8.8.9",
                                         "AA-BB-CC-DD-EE-01")
    check("异常：admission_log=error:... 不阻断",
          str(v5["sources"]["admission_log"]).startswith("error:")
          and v5["conflict_suspect"] is False)


if __name__ == "__main__":
    import nad_client  # noqa: F401  确保模块级缓存绑定本进程
    sys.exit(main())
