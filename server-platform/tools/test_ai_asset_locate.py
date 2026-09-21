#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资产定位 AI 推断函数（ADR-046）本地单测：形状/依据引用完整性/编造拒绝/降级。

零凭据零外联：llm_chat_chain mock、临时库运行后自清理。"""
import json
import os
import shutil
import sys
import tempfile

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


PROFILE_FULL = {
    "terminal": {"terminal_id": "T-A", "hostname": "WIN-13F-35-3",
                 "ip": "172.17.13.35", "mac": "AA-BB-CC-DD-EE-FF",
                 "os_info": "Windows 11"},
    "sources": [
        {"source": "huorong", "source_label": "火绒登记信息", "matched": True,
         "fields": {"楼层": "13", "具体位置": "1310房", "工号": "1036",
                    "姓名": "张三", "使用科室": "眼科"}},
        {"source": "admission", "source_label": "准入登记", "matched": True,
         "fields": {"登记名": "赵吕骏的办公电脑", "部门": "信息管理部"}},
    ],
    "hints": {"name_pattern_peers": ["WIN-13F-35-1", "WIN-13F-35-3"],
              "vlan_kb": {"172.17.13.0/24": "13楼"}},
}

GOOD_JSON = """```json
{"inferences":[
 {"field":"楼层","value":"13楼","confidence":"高",
  "evidence":["火绒登记信息.楼层=13"]},
 {"field":"room","value":"1310房","confidence":"medium",
  "evidence":"火绒登记信息.具体位置=1310房"},
 {"field":"department","value":"眼科","confidence":"high",
  "evidence":["火绒登记信息.使用科室=眼科",
              "根据院区编码规则Z13表示眼科"]},
 {"field":"user","value":null,"confidence":"none","evidence":[],
  "note":"证据不足，无法推断"}
]}
```"""

FABRICATED_ALL_JSON = """{"inferences":[
 {"field":"floor","value":"99楼","confidence":"high",
  "evidence":["根据院区编码规则Z99表示99楼"]},
 {"field":"room","value":"9901房","confidence":"high",
  "evidence":["按惯例楼层首位推断"]},
 {"field":"department","value":" fictitious","confidence":"high",
  "evidence":["医院科室编码常识"]}
]}"""


def main():
    tmp = tempfile.mkdtemp(prefix="etp_ai_asset_")
    try:
        run(tmp)
    finally:
        ai_mod.llm_chat_chain = ORIG_CHAIN
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== ai asset-locate tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


ORIG_CHAIN = ai_mod.llm_chat_chain


def run(tmp):
    st = store_mod.Store(os.path.join(tmp, "t.db"), config_token="tk")
    st.register_terminal("T-A", "windows", "WIN-13F-35-3", "Windows 11",
                         "4.1.5", "172.17.13.35")
    ctx = api_mod.ApiContext(st, {"terminal_token": "tk"},
                             settings=FakeSettings(
                                 {"llm.url": "http://llm",
                                  "llm.api_key": "k",
                                  "llm.model": "main-model",
                                  "llm.model_fallback": "bk"}))
    seen = {}
    calls = {"n": 0}

    def fake_chain(url, key, models, messages, timeout=60, max_retries=1):
        calls["n"] += 1
        seen.update(system=messages[0]["content"],
                    user=messages[1]["content"])
        return {"ok": True, "content": seen.get("reply", ""),
                "model": "main-model"}

    ai_mod.llm_chat_chain = fake_chain

    # [1] prompt 构建：全源
    print("[1] prompt 构建（全源）")
    prompt_text, ev, stats = ai_mod.build_asset_locate_context(PROFILE_FULL)
    check("节：多源登记证据", "【多源登记证据】" in prompt_text)
    check("节：终端标识", "【终端标识】" in prompt_text)
    check("节：对照线索", "【对照线索】" in prompt_text)
    check("来源标签与命中态", "「火绒登记信息」（huorong，已命中）"
          in prompt_text and "「准入登记」（admission，已命中）" in prompt_text)
    check("原始键值保留", "楼层=13" in prompt_text and "使用科室=眼科"
          in prompt_text and "登记名=赵吕骏的办公电脑" in prompt_text)
    check("终端标识进 prompt", "hostname=WIN-13F-35-3" in prompt_text)
    check("线索进 prompt", "172.17.13.0/24" in prompt_text
          and "13楼" in prompt_text and "WIN-13F-35-1" in prompt_text)
    check("stats 无缺失", all(not stats[k]["missing"]
                             for k in ("sources", "terminal", "hints")))
    check("总注入 ≤32KB+开销", len(prompt_text)
          <= ai_mod.ASSET_LOCATE_TOTAL + 2048, "len=%d" % len(prompt_text))

    # [2] prompt 构建：缺源声明
    print("[2] prompt 构建（缺源声明）")
    p2, _, s2 = ai_mod.build_asset_locate_context(
        {"terminal": {"hostname": "host-x"}})
    check("缺源节不出现在 prompt", "【多源登记证据】" not in p2)
    check("缺源尾部声明", "本次未提供" in p2 and "多源登记证据" in p2)
    check("stats 缺失标记", s2["sources"]["missing"] is True
          and s2["hints"]["missing"] is True)

    # [3] 正常推断：围栏 JSON 解析 + 别名归一 + 编造剔除 + null 条保留
    print("[3] 正常推断与校验层")
    seen["reply"] = GOOD_JSON
    r = ai_mod.run_asset_locate_inference(ctx, PROFILE_FULL, terminal_id="T-A")
    check("status ok", r["status"] == "ok", str(r)[:160])
    check("disclaimer 定稿", r["disclaimer"] == "AI 推测，非权威数据")
    check("四字段恒齐全且顺序固定",
          [i["field"] for i in r["inferences"]]
          == ["floor", "room", "department", "user"])
    floor_i = r["inferences"][0]
    check("field 别名归一（楼层→floor）", floor_i["field"] == "floor")
    check("confidence 别名归一（高→high）", floor_i["confidence"] == "high")
    check("value 保留", floor_i["value"] == "13楼")
    check("依据接地（引用输入字段值）", floor_i["evidence"]
          == ["火绒登记信息.楼层=13"])
    room_i = r["inferences"][1]
    check("evidence 字符串形态容错", room_i["evidence"]
          == ["火绒登记信息.具体位置=1310房"])
    dept_i = r["inferences"][2]
    check("编造依据条剔除（院区编码规则）", len(dept_i["evidence"]) == 1
          and dept_i["evidence"][0] == "火绒登记信息.使用科室=眼科")
    user_i = r["inferences"][3]
    check("证据不足 null 条如实保留", user_i["value"] is None
          and user_i["confidence"] == "none"
          and user_i["note"] == "证据不足，无法推断")
    check("dropped 计数", r["dropped"] == 1, "dropped=%s" % r.get("dropped"))
    check("model 透传", r["model"] == "main-model")
    check("analysis_id 落库", r["analysis_id"] and r["analysis_id"] > 0)
    row = st.ai_get(r["analysis_id"])
    ctxj = row["context"] or {}
    check("落库 trigger=asset_locate", row["trigger"] == "asset_locate")
    check("落库 kind=asset_locate", ctxj.get("kind") == "asset_locate")
    check("落库 dropped", ctxj.get("dropped") == 1)
    check("落库存证 sections 三键",
          set(ctxj.get("sections") or {}) == {"sources", "terminal", "hints"})
    check("落库 response 原文", "院区编码规则" in (row["response_text"] or ""))

    # [4] 系统提示词硬约束
    print("[4] 系统提示词硬约束")
    check("硬约束：严禁编造", "严禁编造" in seen["system"])
    check("硬约束：证据不足如实声明", "证据不足" in seen["system"])
    check("硬约束：禁止按 IP 网段推测", "禁止按 IP 网段" in seen["system"])
    check("硬约束：只基于给定数据", "只基于给定数据" in seen["system"])
    check("知识注入：登记字段参考", "登记字段参考" in seen["system"])
    check("输出协议：严格 JSON", "只输出严格 JSON" in seen["system"])

    # [5] 全编造 → 降级 unavailable
    print("[5] 全编造降级")
    seen["reply"] = FABRICATED_ALL_JSON
    r5 = ai_mod.run_asset_locate_inference(ctx, PROFILE_FULL, terminal_id="T-A")
    check("status unavailable", r5["status"] == "unavailable")
    check("inferences 空（不出存疑块）", r5["inferences"] == [])
    check("unavailable_reason=validation", "validation"
          in r5.get("unavailable_reason", ""), r5.get("unavailable_reason"))
    check("disclaimer 仍在", r5["disclaimer"] == "AI 推测，非权威数据")
    row5 = st.ai_get(r5["analysis_id"])
    check("降级落库 status=failed", row5["status"] == "failed")

    # [6] 输出不可解析 → 降级
    print("[6] 不可解析降级")
    seen["reply"] = "我觉得楼层大概是 13 楼吧。"
    r6 = ai_mod.run_asset_locate_inference(ctx, PROFILE_FULL, terminal_id="T-A")
    check("status unavailable", r6["status"] == "unavailable")
    check("unavailable_reason=invalid", r6.get("unavailable_reason")
          == "model_output_invalid")

    # [7] LLM 全链失败 → 降级 unavailable + 原因
    print("[7] LLM 全链失败降级")

    def fail_chain(url, key, models, messages, timeout=60, max_retries=1):
        calls["n"] += 1
        return {"ok": False, "error": "HTTP 503: gateway down",
                "can_fallback": True, "model": "bk", "tried_models": models}

    ai_mod.llm_chat_chain = fail_chain
    r7 = ai_mod.run_asset_locate_inference(ctx, PROFILE_FULL, terminal_id="T-A")
    check("status unavailable", r7["status"] == "unavailable")
    check("unavailable_reason 含 503", "503" in r7.get("unavailable_reason", ""))
    check("inferences 空", r7["inferences"] == [])
    check("disclaimer 仍在", r7["disclaimer"] == "AI 推测，非权威数据")
    row7 = st.ai_get(r7["analysis_id"])
    check("失败落库 status=failed", row7["status"] == "failed"
          and "503" in (row7["error"] or ""))
    ai_mod.llm_chat_chain = fake_chain

    # [8] 零证据短路：不调模型
    print("[8] 零证据短路")
    before = calls["n"]
    r8 = ai_mod.run_asset_locate_inference(ctx, {})
    check("不消耗模型调用", calls["n"] == before)
    check("status ok", r8["status"] == "ok")
    check("四字段证据不足",
          [i["field"] for i in r8["inferences"]]
          == ["floor", "room", "department", "user"]
          and all(i["value"] is None and i["note"] == "证据不足，无法推断"
                  for i in r8["inferences"]))
    check("shortcircuit 标记", r8.get("shortcircuit") == "no_evidence")
    check("无 terminal_id 不落库", r8["analysis_id"] is None)

    # [9] 预算裁剪：多字段超量登记证据（单字段值 200 字符钳制不触发，需总量超限）
    print("[9] 预算裁剪")
    big_profile = {"sources": [{"source": "huorong",
                                "source_label": "火绒登记信息",
                                "matched": True,
                                "fields": dict(
                                    ("字段%03d" % i, "值" * 200)
                                    for i in range(150))}]}
    p9, ev9, s9 = ai_mod.build_asset_locate_context(big_profile)
    seg_lens = [len(seg) for seg in p9.split("【") if seg]
    check("总注入 ≤32KB+开销", len(p9) <= ai_mod.ASSET_LOCATE_TOTAL + 2048,
          "len=%d" % len(p9))
    check("sources 注入受单类上限约束", all(
        ln <= ai_mod.ASSET_LOCATE_CAPS["sources"] + 64 for ln in seg_lens),
        str(sorted(seg_lens)))
    check("sources 存证 ≤32KB", len(ev9["sources"])
          <= ai_mod.ASSET_LOCATE_EVIDENCE_PER_KEY + 64)
    check("stats 如实标记截断", s9["sources"]["truncated"] is True)

    # [10] 校验层直接验证：非法字段/非法置信度/重复字段
    print("[10] 校验层直验")
    atoms = ai_mod._asset_atoms(PROFILE_FULL)
    kept, dropped = ai_mod._validate_asset_inferences(
        [{"field": "notakey", "value": "x", "confidence": "high",
          "evidence": ["楼层=13"]},
         {"field": "floor", "value": "13楼", "confidence": "荒谬",
          "evidence": ["楼层=13"]},
         {"field": "floor", "value": "13楼", "confidence": "low",
          "evidence": ["楼层=13"]},
         {"field": "floor", "value": "13楼", "confidence": "low",
          "evidence": ["楼层=13"]}],
        prompt_text, atoms)
    check("非法 field 剔除", len(kept) == 1 and dropped == 3,
          "kept=%d dropped=%d" % (len(kept), dropped))
    check("重复字段保留首条", kept and kept[0]["confidence"] == "low")

    # [11] 管线契约适配层（ai_analysis.infer_asset_locate）
    print("[11] 管线契约适配")
    import ai_analysis
    seen["reply"] = GOOD_JSON
    payload = {
        "asset_profile": {
            "computer_name": "WIN-13F-35-3",
            "source_platform": "platform+huorong+nad",
            "identity": {"mac": "AA-BB-CC-DD-EE-FF",
                         "hostname": "win-13f-35-3",
                         "current_ip": "172.17.13.35"},
            "registration": {"楼层": "13", "具体位置": "1310房",
                             "工号": "1036", "姓名": "张三",
                             "使用科室": "眼科"},
            "admission": {"terminal_name": "赵吕骏的办公电脑",
                          "alias": "眼科门诊", "owner": "赵吕骏"},
        },
        "matches": [{"score": "high", "keys": ["mac", "ip"],
                     "identity": {},
                     "platform": {"terminal_id": "T-A",
                                  "hostname": "WIN-13F-35-3",
                                  "ip": "172.17.13.35",
                                  "group": {"id": 3, "name": "门诊楼13F"}},
                     "huorong": {}, "nad": {}}],
        "sources": {"platform": "ok", "huorong": "ok", "nad": "ok"},
    }
    identifiers = [{"type": "ip", "value": "172.17.13.35",
                    "raw": "172.17.13.35"},
                   {"type": "mac", "value": "AA-BB-CC-DD-EE-FF",
                    "raw": "AA-BB-CC-DD-EE-FF"}]
    r11 = ai_analysis.infer_asset_locate(ctx, "13楼眼科的电脑", identifiers,
                                         payload)
    check("available=True", r11["available"] is True)
    check("model 透传", r11["model"] == "main-model")
    check("error=None", r11["error"] is None)
    check("blocks 三块（楼层/房间/科室）",
          [b["title"] for b in r11["blocks"]] == ["楼层", "房间", "科室"],
          str([b["title"] for b in r11["blocks"]]))
    b0 = r11["blocks"][0]
    check("block text 含值与中文置信度",
          "13楼" in b0["text"] and "置信度：高" in b0["text"], b0["text"])
    check("block evidence 透传", b0["evidence"] == ["火绒登记信息.楼层=13"])
    check("user 含登记投影", "楼层=13" in seen["user"]
          and "使用科室=眼科" in seen["user"])
    check("user 含准入块", "登记名=赵吕骏的办公电脑" in seen["user"]
          and "使用人=赵吕骏" in seen["user"])
    check("user 含检索标识与资产组", "检索标识" in seen["user"]
          and "门诊楼13F" in seen["user"])
    check("user 含数据源状态", "「数据源状态」（sources_state" in seen["user"]
          and "platform=ok" in seen["user"])
    check("payload 未带 hints 时 prompt 无 hints 标签",
          "知识库网段映射" not in seen["user"])
    c = st._conn.cursor()
    c.execute("SELECT terminal_id, trigger FROM ai_analyses"
              " ORDER BY id DESC LIMIT 1")
    row11 = c.fetchone()
    c.close()
    check("落库 terminal_id=T-A（来自最佳命中）",
          row11 and row11["terminal_id"] == "T-A"
          and row11["trigger"] == "asset_locate", str(tuple(row11)))
    check("结构化 inferences 附加键",
          [i["field"] for i in r11["inferences"]]
          == ["floor", "room", "department", "user"])

    # [11b] 全部字段证据不足 → 单块推断结论
    seen["reply"] = ('{"inferences":[{"field":"floor","value":null,'
                     '"confidence":"none","evidence":[],"note":'
                     '"证据不足，无法推断"}]}')
    r11b = ai_analysis.infer_asset_locate(ctx, "查询", identifiers, payload)
    check("全不足 available=True", r11b["available"] is True)
    check("单块推断结论", r11b["blocks"] ==
          [{"title": "推断结论", "text": "证据不足，无法推断", "evidence": []}])

    # [11c] LLM 失败透传 available=False
    ai_mod.llm_chat_chain = fail_chain
    r11c = ai_analysis.infer_asset_locate(ctx, "查询", identifiers, payload)
    check("失败 available=False", r11c["available"] is False
          and "503" in (r11c["error"] or ""), str(r11c))
    check("失败 blocks 空", r11c["blocks"] == [])
    ai_mod.llm_chat_chain = fake_chain

    # [11d] 内部异常兜底 available=False
    r11d = ai_analysis.infer_asset_locate(ctx, "查询", identifiers,
                                          {"asset_profile": None,
                                           "matches": "bad",
                                           "sources": 3})
    check("畸形 payload 兜底不抛", isinstance(r11d, dict)
          and r11d["available"] in (True, False))

    # [11e] hints 直通（管线 vlan_kb 权威库接线）：进 prompt 且可被接地引用
    print("[11e] hints 直通")
    seen["reply"] = ('{"inferences":[{"field":"floor","value":"13楼",'
                     '"confidence":"low","evidence":'
                     '["知识库网段映射: 172.17.13.0/24→13楼"]}]}')
    payload_h = dict(payload, hints={"vlan_kb": {"172.17.13.0/24": "13楼"}})
    r11e = ai_analysis.infer_asset_locate(ctx, "查询", identifiers, payload_h)
    check("hints 直通进 prompt", "知识库网段映射" in seen["user"]
          and "172.17.13.0/24" in seen["user"] and "13楼" in seen["user"])
    check("依据引用 vlan_kb 接地", r11e["available"] is True
          and r11e["blocks"]
          and r11e["blocks"][0]["title"] == "楼层"
          and r11e["blocks"][0]["evidence"]
          == ["知识库网段映射: 172.17.13.0/24→13楼"], str(r11e)[:160])


if __name__ == "__main__":
    sys.exit(main())
