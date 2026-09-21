#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方数据源模块单测（ADR-043）：nad 扩展证据块 + 降级语义（纯函数层）。

handler 层（鉴权/审计/密码重校验）由 tools/smoke_thirdparty.py 覆盖。"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, "..", "server"))
sys.path.insert(0, SERVER)

import nad_client                                        # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


TERM = {
    "oid": "8899", "name": "诊室-01", "ttype": "PC",
    "manfct": "Dell", "model": "OptiPlex 7090",
    "ou": {"namepath": "总院/门诊楼/1F"},
    "online": 1, "block": 0,
    "owner": {"name": "赵四", "uuid": "19122"},
    "onlts": 1789620000,
    "reginfo": {"stat": "registered", "owner": "张三", "dept": "门诊部"},
    "macs": {"0": {"mac": "AA-BB-CC-DD-EE-01",
                   "ips": {"0": {"ip": "172.17.30.11", "ipver": 4}},
                   "macports": {"0": {"nasoid": "NAS-1", "nasif": "Gi0/1",
                                      "nasname": "核心-1F",
                                      "manip": "172.17.254.1"},
                                "1": {"nasoid": "NAS-2", "nasif": "Gi0/2",
                                      "nasname": "汇聚-2F",
                                      "manip": "172.17.254.2"}}},
             "1": {"mac": "AA-BB-CC-DD-EE-02",
                   "ips": {"0": {"ip": "10.8.0.5", "ipver": 4}},
                   "macports": None}},
}


def main():
    print("=== thirdparty unit tests ===")
    # nad_evidence_full：reginfo 完整 dict 原样透出（登记字段不裁剪）
    ev = nad_client.nad_evidence_full(TERM)
    check("扩展块键齐全", all(k in ev for k in
          ("source", "oid", "name", "ou", "ttype", "manfct", "model",
           "online", "block", "owner", "owner_name", "owner_uuid", "onlts",
           "reginfo", "reginfo_stat", "macs")))
    check("reginfo 完整 dict 透出", ev["reginfo"] == TERM["reginfo"]
          and ev["reginfo"]["owner"] == "张三", str(ev["reginfo"]))
    check("reginfo_stat 便捷位", ev["reginfo_stat"] == "registered")
    check("owner 责任人透出", ev["owner_name"] == "赵四"
          and ev["owner_uuid"] == "19122")
    check("onlts 最后在线透出", ev["onlts"] == 1789620000)
    check("ou 父链解包", ev["ou"] == "总院/门诊楼/1F")
    check("macports dict 形态归一（生产实测形态）", len(ev["macs"]) == 2
          and len(ev["macs"][0]["macports"]) == 2
          and ev["macs"][0]["macports"][0]["nasif"] == "Gi0/1"
          and ev["macs"][0]["macports"][1]["nasname"] == "汇聚-2F")
    check("block/online 原值", ev["block"] == 0 and ev["online"] == 1)

    # only_mac 过滤（与 _evidence_block 同语义）
    ev1 = nad_client.nad_evidence_full(TERM, only_mac="aa:bb:cc:dd:ee:02")
    check("only_mac 过滤", len(ev1["macs"]) == 1
          and ev1["macs"][0]["mac"] == "AA-BB-CC-DD-EE-02")

    # 与 ADR-024 冻结形状对比：_evidence_block 的 reginfo 仅 stat
    ev_legacy = nad_client._evidence_block(TERM)
    check("ADR-024 冻结形状不变", ev_legacy["reginfo"] == "registered"
          and not isinstance(ev_legacy["reginfo"], dict))

    # 缓存注入 → find_by_ip / find_by_mac 的 full 形状（无需真实画方）
    nad_client._cache.update(terms=[TERM], ts=time.time(), reason=None)
    hits, reason = nad_client.nad_find_by_ip(None, "172.17.30.11", full=True)
    check("find_by_ip full 命中", reason is None and len(hits) == 1
          and hits[0]["reginfo"]["owner"] == "张三", str(reason))
    hits2, _ = nad_client.nad_find_by_ip(None, "10.8.0.5", full=True)
    check("find_by_ip 第二 mac 命中", len(hits2) == 1
          and hits2[0]["macs"][0]["mac"] == "AA-BB-CC-DD-EE-02")
    hits3, _ = nad_client.nad_find_by_ip(None, "172.17.30.99", full=True)
    check("find_by_ip 未命中空列表", hits3 == [])
    hits4, _ = nad_client.nad_find_by_mac(None, "AA-BB-CC-DD-EE-01",
                                          full=True)
    check("find_by_mac full 命中", len(hits4) == 1)
    hits5, _ = nad_client.nad_find_by_ip(None, "172.17.30.11")
    check("find_by_ip 默认冻结形状", hits5[0]["reginfo"] == "registered")

    # nad_cache_ts：缓存时点暴露（前端采集时间标注）
    check("nad_cache_ts 暴露缓存时点",
          abs(nad_client.nad_cache_ts() - time.time()) < 5)

    # reginfo 非法形态防御
    bad = dict(TERM, reginfo="weird")
    evb = nad_client.nad_evidence_full(bad)
    check("reginfo 非法形态降级", evb["reginfo"] is None
          and evb["reginfo_stat"] is None)

    print("\n=== thirdparty unit tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        return 1
    print("ALL UNIT TESTS PASSED")
    return 0


if __name__ == "__main__":
    if os.environ.get("ETP_TMPDIR"):
        tempfile.tempdir = os.environ["ETP_TMPDIR"]
    sys.exit(main())
