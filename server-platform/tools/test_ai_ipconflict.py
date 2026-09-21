#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 冲突智能分析（ADR-030）本地单测：四源聚合/缺源降级/回退/预算/路由。

零凭据零外联：llm_chat_chain mock、NAD mock、临时库运行后自清理。"""
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


class FakeNad:
    mode = "not_configured"

    @staticmethod
    def find_by_ip(store, ip):
        if FakeNad.mode == "hit":
            return ([{"source": "nad", "name": "赵吕骏的办公电脑",
                      "ou": "…信息管理部", "ttype": "普通终端", "online": 1,
                      "macs": [{"mac": "F4:F1:9E:3C:69:E4",
                                "ips": {"0": {"ip": "172.17.90.215"}},
                                "macports": [
                                    {"nasif": "GE1/0/7",
                                     "nasname": "SW4（11F网络）",
                                     "manip": "192.168.254.4"}]}]}], None)
        return None, "not_configured"


ORIG_CHAIN = ai_mod.llm_chat_chain
ORIG_NAD = nad_client.nad_find_by_ip


def main():
    tmp = tempfile.mkdtemp(prefix="etp_ai_ipc_")
    try:
        run(tmp)
    finally:
        ai_mod.llm_chat_chain = ORIG_CHAIN
        nad_client.nad_find_by_ip = ORIG_NAD
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== ai ipconflict tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def run(tmp):
    de = sys.modules.get("deep_engine")
    st = store_mod.Store(os.path.join(tmp, "t.db"), config_token="tk")
    st.register_terminal("T-A", "windows", "host-a", "Windows 11",
                         "1.0.0", "127.0.0.1")
    st.register_terminal("T-B", "windows", "host-b", "Windows 11",
                         "1.0.0", "127.0.0.1")
    c = st._conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist(cidr,note,enabled,created_at)"
              " VALUES(?,?,1,?)", ("127.0.0.1", "test", int(time.time())))
    st._conn.commit()
    c.close()
    ctx = api_mod.ApiContext(st, {"terminal_token": "tk"},
                             settings=FakeSettings(
                                 {"llm.url": "http://llm",
                                  "llm.api_key": "k",
                                  "llm.model": "main-model",
                                  "llm.model_fallback": "bk"}))
    seen = {}

    def fake_chain(url, key, models, messages, timeout=60, max_retries=1):
        seen.update(system=messages[0]["content"],
                    user=messages[1]["content"],
                    timeout=timeout, max_retries=max_retries, models=models)
        return {"ok": True, "content": "结论：疑似 IP 冲突", "model":
                "main-model"}
    ai_mod.llm_chat_chain = fake_chain
    nad_client.nad_find_by_ip = FakeNad.find_by_ip

    # [1] 四源齐
    print("[1] 四源聚合")
    FakeNad.mode = "hit"
    st.ipconflict_report("T-A", "172.17.90.215", "F4:F1:9E:3C:69:E4")
    st.ipconflict_report("T-B", "172.17.90.215", "AA-BB-CC-DD-EE-01")
    st.deep_task_create("DC-t1", "T-A", "172.17.90.215",
                        "F4:F1:9E:3C:69:E4")
    st.deep_task_update("DC-t1",
                        steps=[{"step": "resolve", "status": "done",
                                "target": "172.17.254.1", "note": "",
                                "name": "区域定位"},
                               {"step": "arp", "status": "multi",
                                "target": "172.17.254.1",
                                "note": "网关 ARP 同 IP 多 MAC",
                                "name": "网关 ARP 检索",
                                "evidence": ["row1", "row2"]}],
                        verdict={"conclusion": "confirmed", "reasons": []},
                        status="done")
    r = api_mod.run_ai_analysis(ctx, "T-A", "IP冲突疑似，请分析",
                                "terminal", kind="ipconflict")
    check("响应结构", r["ok"] and r["analysis_id"] > 0
          and r["response"] == "结论：疑似 IP 冲突"
          and r["model"] == "main-model", str(r)[:120])
    check("IP 冲突系统提示词", "IP 冲突智能分析引擎" in seen["system"])
    # 超时预算（ADR-006 / ADR-009）：本引擎复用 run_ai_analysis 链，原为
    # timeout=60 且 max_retries=1 —— "重试"与"模型链"是两个**独立相乘**的
    # 放大因子，真实最坏是 60×2×2 = **240s**（原估算只算了一层 120s）。
    # 2026-09-19 收紧为 10s / retries=0（最坏 20s）。固化这两个数：
    # 它们直接决定"用户等多久"，放宽等于静默劣化体验，必须有门禁盯着。
    check("分析链 timeout=10, retries=0（ADR-009）",
          seen.get("timeout") == 10 and seen.get("max_retries") == 0,
          "timeout=%r retries=%r" % (seen.get("timeout"),
                                     seen.get("max_retries")))
    check("双模型最坏耗时 ≤ 场景预算 25s（ADR-006）",
          seen.get("timeout", 0) * max(1, len(seen.get("models") or [])) <= 25)
    # 隐私边界经公共前缀统一注入（2026-09-20，SOUL 准则五）：此前 6 套 prompt
    # 各自硬编码、措辞不一（"上下文仅含"/"日志包仅含"/"证据仅含"）。现由
    # ai.build_system_prompt 统一追加。此断言防止后续有人绕过该函数直接拼
    # prompt —— 那会让隐私边界静默丢失，且不易在评审中察觉。
    check("隐私边界经公共前缀注入（SOUL 准则五）",
          "隐私边界（不可逾越）" in seen["system"]
          and "使用人个人文件" in seen["system"], seen["system"][-140:])
    check("证据硬约束三条", all(w in seen["system"] for w in
                               ("严禁编造", "原文时间戳", "证据不足")))
    check("归属推测收紧（用户纠偏）", all(
        w in seen["system"] for w in
        ("禁止按 IP 网段", "区域/VLAN/网段归属")))
    for word in ("终端冲突上报记录", "深度检测编排结果", "准入资产关联",
                 "数据源状态位"):
        check("prompt 含节：%s" % word, "【%s】" % word in seen["user"])
    check("冲突证据含 suspect_reasons", "multi_terminal" in seen["user"]
          or "疑似" in seen["user"], seen["user"][:200])
    check("deep verdict 进入 prompt", "confirmed" in seen["user"])
    check("NAD 准入名称进入 prompt", "赵吕骏的办公电脑" in seen["user"])
    row = st.ai_get(r["analysis_id"])
    ctxj = row["context"] or {}
    check("落库 kind=ipconflict", ctxj.get("kind") == "ipconflict")
    check("落库四节存证", set(ctxj.get("sections") or {}) ==
          {"conflict_reports", "deep_task", "admission", "sources"})
    check("落库 sources", (ctxj.get("sources") or {}).get("admission")
          == "connected"
          and (ctxj.get("sources") or {}).get("gateway_arp") == "multi")
    for k, v in (ctxj.get("sections") or {}).items():
        try:
            json.loads(v)
            ok = True
        except ValueError:
            ok = False
        check("存证 %s 可 json.loads" % k, ok)

    # [2] 缺源降级：新终端无 deep 任务 + NAD not_configured
    print("[2] 缺源降级")
    FakeNad.mode = "not_configured"
    st.register_terminal("T-D2", "windows", "hd", "Win", "1.0", "1.1.1.2")
    st.ipconflict_report("T-D2", "10.8.8.8", "22-33-44-55-66-77")
    r2 = api_mod.run_ai_analysis(ctx, "T-D2", "kind 分支缺源", "terminal",
                                 kind="ipconflict")
    row2 = st.ai_get(r2["analysis_id"])
    ctxj2 = row2["context"] or {}
    srcs = ctxj2.get("sources") or {}
    check("缺源如实标注", srcs.get("gateway_arp") == "no_task"
          and srcs.get("access_mac") == "no_task"
          and srcs.get("admission") == "not_configured", str(srcs))
    check("缺源 prompt 含未提供声明", "本次未提供" in seen["user"])
    check("缺源仍有冲突上报节", "【终端冲突上报记录】" in seen["user"])

    # [3] 无冲突上报 → 回退一般性分析
    print("[3] 无冲突证据回退")
    st.register_terminal("T-CLEAN", "windows", "hc", "Win", "1.0", "1.1.1.1")
    r3 = api_mod.run_ai_analysis(ctx, "T-CLEAN", "随便看看健康", "terminal",
                                 kind="ipconflict")
    check("无冲突上报回退一般性分析", "【终端诊断上下文】" in seen["user"]
          and "终端冲突上报记录" not in seen["user"])
    check("回退落库无 kind", (st.ai_get(r3["analysis_id"])["context"]
                              or {}).get("kind") is None)

    # [4] 前缀识别
    print("[4] 前缀识别")
    try:
        api_mod.dispatch(ctx, "POST", "/api/v1/ai/analyze", {},
                         {"x-etp-token": "tk"},
                         json.dumps({"terminal_id": "T-A",
                                     "issue_description":
                                     "ipconflict：冲突自动上报"}).encode(),
                         "127.0.0.1")
        ok_prefix = "【终端冲突上报记录】" in seen["user"]
        check("issue 前缀识别进入冲突分支", ok_prefix)
    except api_mod.ApiError as exc:
        check("issue 前缀识别进入冲突分支", False, str(exc.status))
    try:
        api_mod.dispatch(ctx, "POST", "/api/v1/ai/analyze", {},
                         {"x-etp-token": "tk"},
                         json.dumps({"terminal_id": "T-A",
                                     "issue_description": "冲突疑似",
                                     "kind": "ipconflict"}).encode(),
                         "127.0.0.1")
        check("kind 字段识别", "【终端冲突上报记录】" in seen["user"])
    except api_mod.ApiError as exc:
        check("kind 字段识别", False, str(exc.status))

    # [5] 预算裁剪：超大 deep_task steps → 注入受控且 JSON 可解析
    print("[5] 预算裁剪")
    big_steps = [{"step": "arp", "status": "multi", "note": "x" * 30000,
                  "name": "网关 ARP 检索", "target": "172.17.254.1"}]
    st.deep_task_create("DC-big", "T-A", "172.17.90.215",
                        "F4:F1:9E:3C:69:E4")
    st.deep_task_update("DC-big", steps=big_steps,
                        verdict={"conclusion": "confirmed",
                                 "reasons": ["y" * 20000]}, status="done")
    st.ipconflict_report("T-B", "172.17.90.215", "AA-BB-CC-DD-EE-01")
    prompt_text, ev, stats = ai_mod.build_ipconflict_context(
        "预算测试",
        api_mod._aggregate_ipconflict_context(ctx, "T-A"))
    seg_lens = [len(seg) for seg in prompt_text.split("【") if seg]
    check("单节注入 ≤16KB+开销",
          all(ln <= ai_mod.IPCONFLICT_PER_KEY + 64 for ln in seg_lens),
          str(sorted(seg_lens)))
    check("总注入 ≤32KB+开销", len(prompt_text) <=
          ai_mod.IPCONFLICT_TOTAL + 2048, "len=%d" % len(prompt_text))
    check("deep_task 存证 ≤32KB 可解析",
          len(ev["deep_task"]) <= ai_mod.IPCONFLICT_EVIDENCE_PER_KEY + 64)
    try:
        json.loads(ev["deep_task"])
        ok = True
    except ValueError:
        ok = False
    check("超长 deep_task 存证 JSON 完好", ok)
    check("stats 如实标记截断", stats["deep_task"]["truncated"] is True
          or stats["conflict_reports"]["truncated"] is True,
          str(stats))


if __name__ == "__main__":
    sys.exit(main())
