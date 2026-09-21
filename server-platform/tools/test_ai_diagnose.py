#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""终端 AI 智能诊断（ADR-023/ADR-027）本地单测：
结构感知截断/JSON 可解析性/预算优先级分配/stats/prompt 构建/LLM 失败路径/落库。

零凭据：临时库 + mock ai.llm_chat_chain；运行后自清理临时目录。"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import ai as ai_mod                                   # noqa: E402
import api as api_mod                                 # noqa: E402
import store as store_mod                             # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    line = "  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                            (" | " + detail) if detail else "")
    try:
        print(line)
    except UnicodeEncodeError:  # Windows GBK 控制台：detail 可能含乱码样本
        print("  [%s] %s | (detail 含不可打印字符)" % (
            "PASS" if cond else "FAIL", name))


class FakeSettings:
    def __init__(self, d):
        self.d = d

    def get(self, k, default=None):
        return self.d.get(k, default)


_orig_chain = ai_mod.llm_chat_chain


def main():
    tmp = tempfile.mkdtemp(prefix="etp_ai_diag_")
    try:
        run(tmp)
    finally:
        ai_mod.llm_chat_chain = _orig_chain
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== ai diagnose tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def _events(n, fill=None):
    """构造 n 条事件（含转义敏感字符），模拟终端 system_log 事件数组。"""
    out = []
    for i in range(n):
        out.append({
            "time": "2026-09-10 0%d:%02d:00" % (i % 9, i % 60),
            "level": "error" if i % 3 == 0 else "info",
            "source": "EventLog" if i % 3 else "WHEA-Logger",
            "event_id": 1000 + i,
            "message": (fill or "系统 %d 已重启, 完成 %d%% "
                        "«引用»\\路径/\"引号\"" % (i, i * 7)),
        })
    return out


def run(tmp):
    # [1] truncate_text：结构感知截断（ADR-027）
    print("[1] truncate_text 结构感知截断")
    check("短文本原样", ai_mod.truncate_text("abc", 10) == "abc")
    check("None → 空串", ai_mod.truncate_text(None, 100) == "")
    check("数值文本化", ai_mod.truncate_text(12345, 10) == "12345")
    # 非 JSON 文本回退字符截断
    t = ai_mod.truncate_text("x" * 40000, 32000)
    check("非 JSON 文本字符截断保留头部",
          t.startswith("x" * 100) and t.endswith("…[truncated]"))
    check("非 JSON 文本截断长度", len(t) == 32000 + len("\n…[truncated]"))
    # JSON 对象：裁剪后恒可解析
    obj = {"events": _events(50), "meta": {"host": "pc-1", "n": 50}}
    t2 = ai_mod.truncate_text(obj, 2000)
    ok_json = True
    try:
        back = json.loads(t2)
    except ValueError:
        ok_json, back = False, None
    check("dict 裁剪后可 json.loads", ok_json, t2[:80])
    check("dict 小键完整保留", ok_json and back.get("meta") ==
          {"host": "pc-1", "n": 50})
    check("dict 大键条目级裁剪", ok_json and isinstance(back.get("events"), list)
          and 0 < len(back["events"]) < 50)
    # JSON 字符串文本：裁剪后仍可解析
    jstr = ai_mod._json_dumps({"events": _events(80)})
    t3 = ai_mod.truncate_text(jstr, 3000)
    ok3 = True
    try:
        back3 = json.loads(t3)
    except ValueError:
        ok3, back3 = False, None
    check("JSON 字符串裁剪后可 json.loads", ok3, t3[:80])
    check("JSON 字符串裁剪保留完整条目", ok3 and
          all(isinstance(e, dict) and "message" in e for e in back3["events"]))
    # 事件内转义敏感字符无损（不撕裂转义）
    tricky = {"events": _events(40, fill="引号\"反斜杠\\斜杠/制表\t换行\n中文«»")}
    t4 = ai_mod.truncate_text(tricky, 1500)
    ok4 = back4 = None
    try:
        ok4, back4 = True, json.loads(t4)
    except ValueError:
        ok4 = False
    orig_msgs = {e["message"] for e in tricky["events"]}
    kept_msgs = {e["message"] for e in (back4 or {}).get("events", [])}
    check("转义敏感内容无损（条目完整）", ok4 and kept_msgs <= orig_msgs
          and len(kept_msgs) > 0, t4[:60])
    # list 顶层：完整条目保留
    t5 = ai_mod.truncate_text(_events(200), 4000)
    ok5 = True
    try:
        back5 = json.loads(t5)
    except ValueError:
        ok5, back5 = False, []
    check("顶层 list 裁剪后可解析且条目完整", ok5 and len(back5) < 200
          and all(isinstance(e, dict) for e in back5))
    # 裁剪不超预算太多（JSON 结果无文本尾注，长度受控）
    check("JSON 裁剪结果长度受控", len(t2) <= 2100 and len(t5) <= 4100,
          "len(t2)=%d len(t5)=%d" % (len(t2), len(t5)))
    # 中文按字符截断（ADR-027 加项2①：码点对齐，绝不产生半个字/替换符）
    cn = "专业工作站系统日志事件" * 3000
    t6 = ai_mod.truncate_text(cn, 5000)
    check("中文按字符截断无半个字", len(t6) == 5000 + len("\n…[truncated]")
          and "\ufffd" not in t6 and set(t6[:5000]) <= set(cn),
          "len=%d" % len(t6))

    # [2] build_diagnose_context：预算提升 + 优先级分配 + stats
    print("[2] build_diagnose_context 预算与分配")
    logs = {"hwinfo": {"cpu": "i7", "mem": 16},
            "os_info": "Windows 11 Pro",
            "system_log": {"events": _events(400)},   # 远超 32KB
            "network": {"ping": "1ms"}}
    prompt, ev, stats = ai_mod.build_diagnose_context("今早电脑重启了，分析原因", logs)
    check("issue 置顶", prompt.startswith("【问题概述】\n今早电脑重启了"))
    check("六类分节标题", "【硬件信息】" in prompt and "【系统日志】" in prompt
          and "【网络数据】" in prompt)
    check("缺失类别不出现", "【压测结果】" not in prompt)
    check("单类注入 ≤16KB（DIAG_PROMPT_PER_KEY）", all(
        len(seg) <= ai_mod.DIAG_PROMPT_PER_KEY + 64
        for seg in prompt.split("【") if seg),
        "per_key=%d" % ai_mod.DIAG_PROMPT_PER_KEY)
    check("日志注入总量 ≤32KB 总预算", len(prompt) <=
          ai_mod.DIAG_PROMPT_TOTAL + 2048, "len=%d" % len(prompt))
    check("存证每类 ≤32KB（与 UI 承诺一致）", all(
        len(v) <= ai_mod.DIAG_EVIDENCE_PER_KEY + 64 for v in ev.values()))
    # 存证 JSON 类可解析（需求 2 红线）
    def loads_ok(s):
        try:
            json.loads(s)
            return True
        except ValueError:
            return False
    check("存证 system_log 可 json.loads", loads_ok(ev["system_log"]))
    check("存证 hwinfo 可 json.loads", loads_ok(ev["hwinfo"]))
    check("存证只含上报类别", set(ev.keys()) == {"hwinfo", "os_info",
                                                "system_log", "network"})
    # stats 如实（需求 3 + ADR-027 加项2）
    sst = stats["system_log"]
    check("stats 含六字段", set(sst) == {"original_chars", "evidence_chars",
                                         "original_items", "kept_items",
                                         "truncated", "suspect_mojibake"},
          str(sst))
    check("stats 超长 JSON 标记 truncated", sst["truncated"] is True)
    check("stats 条数如实", sst["original_items"] == 400
          and 0 < (sst["kept_items"] or 0) < 400, str(sst))
    check("stats 短类别不标截断", stats["hwinfo"]["truncated"] is False
          and stats["hwinfo"]["original_items"] is None)
    check("stats 正常中文不标疑似乱码",
          stats["hwinfo"]["suspect_mojibake"] is False)
    # 乱码探测：analysis_id=16 取证样式双向（UTF-8↔GBK mojibake）
    bad_gbk = "专业工作站".encode("utf-8").decode("gbk", errors="replace")
    bad_rev = "专业工作站".encode("gbk").decode("utf-8", errors="ignore")
    logs_bad = {"os_info": {"os": {"caption": "Microsoft Windows 11 " + bad_rev}},
                "system_log": {"events": _events(3)}}
    _, _, stats_bad = ai_mod.build_diagnose_context("重启", logs_bad)
    check("stats GBK→UTF8 方向标记 suspect（16 号样本方向）",
          stats_bad["os_info"]["suspect_mojibake"] is True,
          bad_rev[:20])
    check("stats UTF-8→GBK 方向标记 suspect",
          ai_mod._detect_mojibake(bad_gbk) is True)
    check("stats 同包正常类不误标", stats_bad["system_log"]
          ["suspect_mojibake"] is False)
    check("英文文本不误标", ai_mod._detect_mojibake(
        "Microsoft Windows 11 Pro build 26200") is False)
    # system_log 在「今早重启」问题下第一优先拿满
    check("system_log 优先拿满单类预算",
          "【系统日志】" in prompt
          and prompt.index("【系统日志】") < prompt.index("【硬件信息】"))
    prompt2, ev2, stats2 = ai_mod.build_diagnose_context("只有概述", {})
    check("logs 空 → 缺省提示节", "未提供日志数据" in prompt2
          and ev2 == {} and stats2 == {})
    check("logs 非对象容错",
          ai_mod.build_diagnose_context("x", "bad")[1] == {})
    check("单类 None 跳过", ai_mod.build_diagnose_context(
        "x", {"hwinfo": None})[1] == {})

    # [2.1] 预算分配：优先级不被快照类挤占 / 关键词提前
    print("[2.1] 预算分配与优先级")
    big = "z" * 40000
    all_big = {k: big for k in ai_mod.DIAG_LOG_KEYS}
    p3, ev3, st3 = ai_mod.build_diagnose_context("泛泛问题", all_big)
    segs = {seg.split("】", 1)[0]: seg for seg in p3.split("【") if "】" in seg}
    sys_len = len(segs.get("系统日志", "").split("】", 1)[-1])
    check("六类全满：system_log 优先拿满 16KB",
          sys_len >= ai_mod.DIAG_PROMPT_PER_KEY - 64,
          "sys_len=%d" % sys_len)
    check("六类全满：总注入受控 ≤32KB", len(p3) <=
          ai_mod.DIAG_PROMPT_TOTAL + 2048, "len=%d" % len(p3))
    check("六类全满：尾部低优先类被裁到 0 且 prompt 声明",
          "未随本请求注入" in p3, p3[-160:])
    # 反向：快照类大、system_log 小 → system_log 需求小也先满足，快照类拿剩余
    rev = {k: big for k in ai_mod.DIAG_LOG_KEYS}
    rev["system_log"] = "EVT-1 reboot done"
    p4 = ai_mod.build_diagnose_context("x", rev)[0]
    check("system_log 需求小也全额满足", "【系统日志】\nEVT-1 reboot done" in p4)
    # 关键词优先级：网络问题 → network 提前到 perf_analysis 之前
    prio = ai_mod._diag_issue_priority("网络断网丢包严重")
    check("关键词把 network 提前", prio.index("network")
          < prio.index("perf_analysis"), str(prio))
    prio2 = ai_mod._diag_issue_priority("今早电脑重启了")
    check("system_log 恒第一", prio2[0] == "system_log", str(prio2))
    check("无命中保持默认序", ai_mod._diag_issue_priority("看看")
          == list(ai_mod.DIAG_PRIORITY_DEFAULT))

    # [2.2] RAW_LIMIT 层一致性：evidence 不再二次丢失
    print("[2.2] 32KB 承诺一致性")
    logs5 = {"system_log": {"events": _events(600)}}
    p5, ev5, st5 = ai_mod.build_diagnose_context("重启", logs5)
    ev_raw = ai_mod.truncate_text(logs5["system_log"], ai_mod.DIAG_RAW_LIMIT)
    check("evidence 与 RAW 层一致（32KB 存证不二次截断）",
          ev5["system_log"] == ev_raw)
    try:
        json.loads(ev5["system_log"])
        ok6 = True
    except ValueError:
        ok6 = False
    check("32KB 存证 JSON 完好", ok6)

    # [3] run_terminal_diagnose：成功路径（mock 链）
    print("[3] run_terminal_diagnose 成功路径")
    db = os.path.join(tmp, "t.db")
    st = store_mod.Store(db, config_token="cfg-token")
    st.register_terminal("T-TEST", "windows", "host-a", "Windows 11",
                         "1.0.0", "127.0.0.1")
    # 放行本机回环（模拟已注册终端场景；未注册终端走 404 分支需准入先通过）
    import time as _time
    c = st._conn.cursor()
    c.execute("INSERT OR IGNORE INTO whitelist(cidr,note,enabled,created_at)"
              " VALUES(?,?,1,?)", ("127.0.0.1", "test", int(_time.time())))
    st._conn.commit()
    c.close()
    ctx = api_mod.ApiContext(st, {"terminal_token": "cfg-token"},
                             settings=FakeSettings(
                                 {"llm.url": "http://llm", "llm.api_key": "k",
                                  "llm.model": "main-model",
                                  "llm.model_fallback": "backup-model"}))
    seen = {}

    def fake_chain_ok(url, key, models, messages, timeout=60, max_retries=1):
        seen.update(url=url, key=key, models=models, timeout=timeout,
                    max_retries=max_retries,
                    system=messages[0]["content"],
                    user=messages[1]["content"])
        return {"ok": True, "content": "结论：CPU 瓶颈", "model": "main-model"}

    ai_mod.llm_chat_chain = fake_chain_ok
    r = api_mod.run_terminal_diagnose(ctx, "T-TEST", "CPU 100%",
                                      {"hwinfo": {"cpu": "i7"}})
    check("返回四元组", r["ok"] and r["analysis_id"] > 0
          and r["response_text"] == "结论：CPU 瓶颈"
          and isinstance(r["duration_ms"], int), str(r))
    # 超时预算（ADR-006 / ADR-009）：这个数字直接决定"用户等多久"，放宽=静默劣化体验，
    # 所以固化它，并顺带断言"双模型最坏耗时 ≤ 场景预算"这条硬约束。
    # 2026-09-19 收紧：45 → 25（最坏 90s → 50s），覆盖实测 11.3s~30.3s 区间。
    check("走 chain 且 timeout=25（ADR-009）；最坏 50s ≤ 场景预算 60s",
          seen["timeout"] == 25 and seen["max_retries"] == 0
          and seen["timeout"] * 2 <= 60)
    # 隐私边界经公共前缀统一注入（2026-09-20，SOUL 准则五）——本引擎是唯一
    # "数据本身离机"的中心能力（终端采集后上报），隐私声明尤其不能缺。
    check("隐私边界经公共前缀注入（SOUL 准则五）",
          "隐私边界（不可逾越）" in seen["system"]
          and "使用人个人文件" in seen["system"], seen["system"][-140:])
    check("主备模型链传入", seen["models"] == ["main-model", "backup-model"])
    check("诊断系统提示词", "智能诊断引擎" in seen["system"])
    check("证据可信性硬约束三条（加项1）", all(
        w in seen["system"] for w in ("严禁编造", "原文时间戳", "证据不足")))
    row = st.ai_get(r["analysis_id"])
    check("trigger=terminal_diagnose", row["trigger"] == "terminal_diagnose")
    check("issue 落库为概述", row["issue"] == "CPU 100%")
    check("context 存证 issue+logs", row["context"].get("issue") == "CPU 100%"
          and "hwinfo" in (row["context"].get("logs") or {}))
    check("context 落库 logs_stats", isinstance(
        (row["context"].get("logs_stats") or {}).get("hwinfo"), dict)
        and row["context"]["logs_stats"]["hwinfo"]["truncated"] is False)
    check("response/model 落库", row["response_text"] == "结论：CPU 瓶颈"
          and row["model"] == "main-model" and row["status"] == "ok")

    # [4] LLM 失败路径（链全败 → status=failed，仍落库）
    print("[4] LLM 全链失败路径")

    def fake_chain_fail(url, key, models, messages, timeout=60, max_retries=1):
        return {"ok": False, "error": "HTTP 503: model_not_found",
                "can_fallback": True, "tried_models": models}

    ai_mod.llm_chat_chain = fake_chain_fail
    r2 = api_mod.run_terminal_diagnose(ctx, "T-TEST", "死机",
                                       {"system_log": "crit error"})
    check("失败返回 ok=False + error", not r2["ok"]
          and r2["error"] == "HTTP 503: model_not_found")
    row2 = st.ai_get(r2["analysis_id"])
    check("失败记录落库 status=failed", row2["status"] == "failed"
          and row2["error"] == "HTTP 503: model_not_found"
          and row2["trigger"] == "terminal_diagnose")
    check("失败记录存证保留", "system_log" in (row2["context"].get("logs") or {}))

    # [5] 终端路由分支参数校验（dispatch 级 400）
    print("[5] 路由参数校验")
    ctx2 = api_mod.ApiContext(st, {"terminal_token": "cfg-token"},
                              settings=FakeSettings({}))
    try:
        api_mod.dispatch(ctx2, "POST", "/api/v1/terminals/T-TEST/ai/diagnose",
                         {}, {"x-etp-token": "cfg-token"},
                         b'{"issue": "", "logs": {}}', "127.0.0.1")
        check("空 issue 400", False)
    except api_mod.ApiError as exc:
        check("空 issue 400", exc.status == 400)
    try:
        api_mod.dispatch(ctx2, "POST", "/api/v1/terminals/T-TEST/ai/diagnose",
                         {}, {"x-etp-token": "cfg-token"},
                         b'{"issue": "x", "logs": "not-object"}', "127.0.0.1")
        check("logs 非对象 400", False)
    except api_mod.ApiError as exc:
        check("logs 非对象 400", exc.status == 400)
    try:
        api_mod.dispatch(ctx2, "POST",
                         "/api/v1/terminals/T-NOTEXIST/ai/diagnose", {},
                         {"x-etp-token": "cfg-token"},
                         b'{"issue": "x"}', "127.0.0.1")
        check("未注册终端 404", False)
    except api_mod.ApiError as exc:
        check("未注册终端 404", exc.status == 404)
    try:
        api_mod.dispatch(ctx2, "POST",
                         "/api/v1/terminals/T-TEST/ai/other", {},
                         {"x-etp-token": "cfg-token"},
                         b'{"issue": "x"}', "127.0.0.1")
        check("非 diagnose 子路径 404", False)
    except api_mod.ApiError as exc:
        check("非 diagnose 子路径 404", exc.status == 404)


if __name__ == "__main__":
    sys.exit(main())
