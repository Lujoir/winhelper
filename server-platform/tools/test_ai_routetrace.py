#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路由追踪 AI 研判（ADR-031）本地单测：hops 聚合/知识库注入/预算裁剪/
超时跳保真/前缀识别/回退。llm mock、临时库自清理。"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import ai as ai_mod                                    # noqa: E402
import api as api_mod                                  # noqa: E402
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


ORIG_CHAIN = ai_mod.llm_chat_chain


def main():
    tmp = tempfile.mkdtemp(prefix="etp_ai_rt_")
    try:
        run(tmp)
    finally:
        ai_mod.llm_chat_chain = ORIG_CHAIN
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== ai routetrace tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def _hops(with_timeout=True, count=5):
    hops = [
        {"hop": 1, "ip": "172.17.90.254", "host": "gateway.local",
         "delays": "1 ms", "timeout": False, "zone": "终端区",
         "zone_desc": "办公终端网段"},
        {"hop": 2, "ip": "172.17.254.1", "host": "core-sw",
         "delays": "2 ms", "timeout": False, "zone": "核心交换机",
         "zone_desc": "核心层网关"},
        {"hop": 3, "ip": "172.17.254.2", "host": "agg-sw",
         "delays": "3 ms", "timeout": False, "zone": "数据中心汇聚",
         "zone_desc": "汇聚网关"},
    ]
    if with_timeout:
        hops.append({"hop": 4, "ip": "10.99.99.9", "host": "*",
                     "delays": "*", "timeout": True, "zone": "",
                     "zone_desc": ""})
    if count >= 5:
        hops.append({"hop": 5, "ip": "172.17.7.215", "host": "nqi",
                     "delays": "5 ms", "timeout": False, "zone": "数据中心区",
                     "zone_desc": "服务器区"})
    return hops


def run(tmp):
    st = store_mod.Store(os.path.join(tmp, "t.db"), config_token="tk")
    st.register_terminal("T-RT", "windows", "rt-pc", "Windows 11",
                         "1.0.0", "172.17.90.215")
    c = st._conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist(cidr,note,enabled,created_at)"
              " VALUES(?,?,1,?)", ("127.0.0.1", "test", int(time.time())))
    st._conn.commit()
    c.close()
    ctx = api_mod.ApiContext(st, {"terminal_token": "tk"},
                             settings=FakeSettings(
                                 {"llm.url": "http://llm",
                                  "llm.api_key": "k",
                                  "llm.model": "main-model"}))
    seen = {}

    def fake_chain(url, key, models, messages, timeout=60, max_retries=1):
        seen.update(system=messages[0]["content"], user=messages[1]["content"])
        return {"ok": True, "content": "路径研判：正常", "model": "main-model"}
    ai_mod.llm_chat_chain = fake_chain

    # [1] build 层：三源聚合 + 超时跳保真
    print("[1] build_routetrace_context")
    kb_nodes = [{"match": "172.17.254.1/32", "zone": "核心交换机",
                 "desc": "核心层网关", "gw_ip": "172.17.254.1"},
                {"match": "172.17.7.215/32", "zone": "数据中心区",
                 "desc": "NQI 平台"}]
    pkg = {"target": "172.17.7.215", "hops": _hops(),
           "route_nodes": kb_nodes,
           "terminal": {"hostname": "rt-pc", "ip": "172.17.90.215",
                        "segment": "172.17.90.0/24"}}
    prompt, ev, stats = ai_mod.build_routetrace_context("路由追踪分析", pkg)
    check("三节齐全", all("【%s】" % lbl in prompt for lbl in
                         ("路由追踪跳点（终端上传）", "知识库路由表（网段→区域）",
                          "终端信息")))
    check("追踪目标置入", "【追踪目标】172.17.7.215" in prompt)
    check("超时跳保真", '"timeout":true' in prompt.replace(" ", "")
          or "10.99.99.9" in prompt)
    check("知识库条目注入", "核心交换机" in prompt and "172.17.254.1/32" in prompt)
    check("终端信息注入", "rt-pc" in prompt and "172.17.90.0/24" in prompt)
    check("系统提示词三态+硬约束", all(
        w in ai_mod.ROUTETRACE_SYSTEM_PROMPT for w in
        ("正常 / 疑似 / 需关注", "严禁编造", "跳数序号", "知识库未覆盖")))
    check("硬约束④：禁止推测网段归属（用户纠偏）", all(
        w in ai_mod.ROUTETRACE_SYSTEM_PROMPT for w in
        ("禁止根据 IP 地址段", "只标注「知识库未覆盖」", "管理员提供的权威数据")))
    for k, v in ev.items():
        try:
            json.loads(v)
            ok = True
        except ValueError:
            ok = False
        check("存证 %s 可 json.loads" % k, ok)
    check("stats 无缺失", all(not s["missing"] for s in stats.values()))

    # [2] 预算裁剪：超大 hops → 16KB cap 且 JSON 完好
    print("[2] 预算裁剪")
    big_hops = _hops() * 40  # 远超 16KB
    pkg2 = {"target": "172.17.7.215", "hops": big_hops,
            "route_nodes": kb_nodes, "terminal": pkg["terminal"]}
    prompt2, ev2, stats2 = ai_mod.build_routetrace_context("裁剪测试", pkg2)
    # 注入层（prompt 内 hops 节）≤16KB cap；存证层 ≤32KB 总上限
    hop_seg = [seg for seg in prompt2.split("【")
               if seg.startswith("路由追踪跳点")]
    hop_inject = len(hop_seg[0].split("】", 1)[-1]) if hop_seg else 0
    check("hops 注入 ≤16KB+开销",
          hop_inject <= ai_mod.ROUTETRACE_CAPS["hops"] + 64,
          "len=%d" % hop_inject)
    check("hops 存证 ≤32KB 总上限",
          len(ev2["hops"]) <= ai_mod.ROUTETRACE_TOTAL + 64,
          "len=%d" % len(ev2["hops"]))
    seg_lens = [len(seg) for seg in prompt2.split("【") if seg]
    check("总注入 ≤32KB+开销", len(prompt2) <=
          ai_mod.ROUTETRACE_TOTAL + 2048, "len=%d" % len(prompt2))
    try:
        json.loads(ev2["hops"])
        ok2 = True
    except ValueError:
        ok2 = False
    check("裁剪后 hops JSON 完好（结构感知）", ok2)
    check("stats 如实标记截断", stats2["hops"]["truncated"] is True)
    check("裁剪保留头部跳点", '"hop":1' in ev2["hops"].replace(" ", ""))

    # [3] 缺源：无知识库 → skipped 声明
    print("[3] 缺源声明")
    pkg3 = {"target": "172.17.7.215", "hops": _hops()}
    prompt3, ev3, stats3 = ai_mod.build_routetrace_context("缺源", pkg3)
    check("无知识库 → 声明未提供", "本次未提供" in prompt3
          and "知识库路由表" in prompt3.split("（注")[1][:40])
    check("stats missing 如实", stats3["route_nodes"]["missing"] is True)

    # [4] 路由层：dispatch 前缀识别 + context 透传 + 回退
    print("[4] 路由与分支")
    route_ctx = {"target": "172.17.7.215", "hops": _hops()}
    payload = json.dumps({"terminal_id": "T-RT",
                          "issue_description": "路由追踪分析：到 NQI 平台",
                          "context": route_ctx}).encode()
    r = api_mod.dispatch(ctx, "POST", "/api/v1/ai/analyze", {},
                         {"x-etp-token": "tk"}, payload, "127.0.0.1")
    status, body = r[0], json.loads(r[1].decode())
    check("routetrace 200 + 结构", status == 200 and body.get("ok")
          and body.get("analysis_id") > 0 and body.get("model") ==
          "main-model", str(body)[:120])
    check("prompt 含 hops 与知识库", "路由追踪跳点" in seen["user"]
          and "知识库路由表" in seen["user"])
    row = st.ai_get(body["analysis_id"])
    ctxj = row["context"] or {}
    check("落库 kind=target/hops_count", ctxj.get("kind") == "routetrace"
          and ctxj.get("target") == "172.17.7.215"
          and ctxj.get("hops_count") == 5, str(ctxj)[:150])
    check("落库 sources", (ctxj.get("sources") or {}).get("hops")
          == "terminal_upload")
    # kind 字段触发
    payload2 = json.dumps({"terminal_id": "T-RT", "kind": "routetrace",
                           "issue_description": "随便写",
                           "context": route_ctx}).encode()
    api_mod.dispatch(ctx, "POST", "/api/v1/ai/analyze", {},
                     {"x-etp-token": "tk"}, payload2, "127.0.0.1")
    check("kind 字段触发", "路由追踪跳点" in seen["user"])
    # 空 hops 回退一般性分析
    payload3 = json.dumps({"terminal_id": "T-RT", "kind": "routetrace",
                           "issue_description": "x",
                           "context": {"target": "1.2.3.4",
                                       "hops": []}}).encode()
    api_mod.dispatch(ctx, "POST", "/api/v1/ai/analyze", {},
                     {"x-etp-token": "tk"}, payload3, "127.0.0.1")
    check("空 hops 回退一般性分析", "【终端诊断上下文】" in seen["user"]
          and "路由追踪跳点" not in seen["user"])
    # hops 非法（非 list）回退
    payload4 = json.dumps({"terminal_id": "T-RT", "kind": "routetrace",
                           "issue_description": "x",
                           "context": {"hops": "bad"}}).encode()
    api_mod.dispatch(ctx, "POST", "/api/v1/ai/analyze", {},
                     {"x-etp-token": "tk"}, payload4, "127.0.0.1")
    check("hops 非法回退", "【终端诊断上下文】" in seen["user"])
    # payload 缺 context 键回退（team-lead 建议断言；终端 data/context 键错位场景）
    api_mod.dispatch(ctx, "POST", "/api/v1/ai/analyze", {},
                     {"x-etp-token": "tk"},
                     json.dumps({"terminal_id": "T-RT",
                                 "kind": "routetrace",
                                 "issue_description": "x"}).encode(),
                     "127.0.0.1")
    check("缺 context 键回退一般性分析", "【终端诊断上下文】" in seen["user"]
          and "路由追踪跳点" not in seen["user"])


if __name__ == "__main__":
    sys.exit(main())
