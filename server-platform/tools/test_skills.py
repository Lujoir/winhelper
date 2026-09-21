#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Skill 校验层（P1-5）本地单测：输入契约 / 输出契约 / 污染检测 / JSON 结构。

零凭据零外联：纯函数校验，无 IO、无网络、无 LLM。

重点验证两件事（这两件做错了校验层就是有害的）：
  ① **严进宽出不能变成"宽进严出"** —— LLM 输出天然有变体
     （全角括号 / 加粗 / 冒号有无），正常输出必须被判为通过，
     否则校验层会把好结果全拦掉，比没有校验层更糟。
  ② **丢弃必须结构化** —— 每个问题都带 kind + detail，能归因。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import skills                                          # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


GOOD_ANALYZE = (
    "【故障原因分析】CPU 持续 92%，进程 svchost 异常，见 09:15 的 perf 记录。\n"
    "【处理意见】立即处理：检查 Windows Update 计划任务；建议观察：连续 3 天。\n"
    "【风险提示】缺少磁盘 IO 数据，如需定位请补充采集。"
)


def main():
    print("=== 一、输出契约：正常输出应通过 ===")
    ok, issues = skills.validate_output("analyze", GOOD_ANALYZE)
    check("标准格式通过", ok, str(issues))

    variant = ("**故障原因分析**\nCPU 高。\n\n"
               "**处理意见**\n重启服务。\n\n"
               "**风险提示**\n证据有限。")
    ok, issues = skills.validate_output("analyze", variant)
    check("加粗/无括号变体通过（容错不过度）", ok, str(issues))

    variant2 = ("故障原因分析：CPU 高，见 09:15 记录。\n\n"
                "处理意见：立即重启。\n\n"
                "风险提示：数据不足。")
    ok, issues = skills.validate_output("analyze", variant2)
    check("纯冒号变体通过", ok, str(issues))

    fullwidth = ("故障原因分析\nCPU 高。\n处理意见\n重启。\n风险提示\n证据有限。")
    ok, issues = skills.validate_output("analyze", fullwidth)
    check("无装饰纯标题通过", ok, str(issues))

    print("=== 二、输出契约：缺段落/空/过短应拦下 ===")
    ok, issues = skills.validate_output("analyze", "【故障原因分析】只有这一段，内容够长了不短。")
    check("缺段落被拦", not ok and any(i["kind"] == "missing_section" for i in issues),
          str(issues))

    ok, issues = skills.validate_output("analyze", "")
    check("空内容被拦", not ok and issues[0]["kind"] == "empty", str(issues))

    ok, issues = skills.validate_output("analyze", "太短")
    check("过短被拦", any(i["kind"] == "too_short" for i in issues), str(issues))

    print("=== 三、污染检测 ===")
    mojibake = "【故障原因分析】\ufffd\ufffd 数据损坏 【处理意见】x 【风险提示】y"
    ok, issues = skills.validate_output("analyze", mojibake)
    check("乱码被检出", any(i["kind"] == "pollution_mojibake" for i in issues),
          str(issues))

    privacy = (GOOD_ANALYZE + "\n另：该用户聊天记录显示其经常加班。")
    ok, issues = skills.validate_output("analyze", privacy)
    check("隐私越界被检出",
          any(i["kind"] == "pollution_privacy_violation" for i in issues),
          str(issues))

    offtopic = (GOOD_ANALYZE + "\n作为一个AI，我无法访问真实设备。")
    ok, issues = skills.validate_output("analyze", offtopic)
    check("无关内容被检出", any(i["kind"] == "pollution_offtopic" for i in issues),
          str(issues))

    check("污染项带结构化 detail",
          all("detail" in i and i["detail"] for i in issues), str(issues))

    print("=== 四、输入契约 ===")
    ok, dropped = skills.validate_input("diagnose", {"system_log": "x", "hwinfo": None})
    check("合法输入通过", ok and not dropped, str(dropped))

    ok, dropped = skills.validate_input("diagnose", {"system_log": "x", "bogus_key": "y"})
    check("未知键回执（不报错但要归因）",
          ok and any(d["reason"] == "unknown_input_key" for d in dropped),
          str(dropped))

    ok, dropped = skills.validate_input("analyze", {"issue": "开机慢"})
    check("必填缺失被拦",
          not ok and any(d["reason"] == "required_missing" for d in dropped),
          str(dropped))

    ok, dropped = skills.validate_input("diagnose", {"system_log": 12345})
    check("非法值类型回执",
          any(d["reason"] == "unusable_value_type" for d in dropped),
          str(dropped))

    ok, dropped = skills.validate_input("no-such-skill", {})
    check("未知 skill 被拦",
          not ok and dropped[0]["reason"] == "unknown_skill", str(dropped))

    ok, dropped = skills.validate_input("diagnose", "not a dict")
    check("非 dict 载荷被拦",
          not ok and dropped[0]["reason"] == "payload_not_dict", str(dropped))

    print("=== 五、asset-locate：严格 JSON 契约 ===")
    good_json = ('{"inferences":['
                 '{"field":"floor","value":"3","confidence":"high","evidence":["hr.floor=3"]},'
                 '{"field":"room","value":null,"confidence":"none","evidence":[],"note":"证据不足"},'
                 '{"field":"department","value":null,"confidence":"none","evidence":[]},'
                 '{"field":"user","value":null,"confidence":"none","evidence":[]}]}')
    ok, issues = skills.validate_output("asset-locate", good_json)
    check("合法 JSON 通过", ok, str(issues))

    fenced = "```json\n" + good_json + "\n```"
    ok, issues = skills.validate_output("asset-locate", fenced)
    check("```json 围栏可解析", ok, str(issues))

    with_prose = "好的，分析如下：\n" + good_json + "\n以上。"
    ok, issues = skills.validate_output("asset-locate", with_prose)
    check("前后夹带说明可解析", ok, str(issues))

    ok, issues = skills.validate_output("asset-locate", "这不是 JSON，只是一段话而已，长度够了。")
    check("非 JSON 被拦",
          not ok and any(i["kind"] == "invalid_json" for i in issues), str(issues))

    bad_conf = good_json.replace('"high"', '"veryHigh"')
    ok, issues = skills.validate_output("asset-locate", bad_conf)
    check("非法 confidence 被拦",
          any("confidence 非法" in i["detail"] for i in issues), str(issues))

    none_with_value = ('{"inferences":['
                       '{"field":"floor","value":"3","confidence":"none","evidence":["x"]},'
                       '{"field":"room","value":null,"confidence":"none","evidence":[]},'
                       '{"field":"department","value":null,"confidence":"none","evidence":[]},'
                       '{"field":"user","value":null,"confidence":"none","evidence":[]}]}')
    ok, issues = skills.validate_output("asset-locate", none_with_value)
    check("confidence=none 却给 value 被拦（证据可信性硬约束）",
          any(i["kind"] == "contract_violation" for i in issues), str(issues))

    no_evidence = ('{"inferences":['
                   '{"field":"floor","value":"3","confidence":"high","evidence":[]},'
                   '{"field":"room","value":null,"confidence":"none","evidence":[]},'
                   '{"field":"department","value":null,"confidence":"none","evidence":[]},'
                   '{"field":"user","value":null,"confidence":"none","evidence":[]}]}')
    ok, issues = skills.validate_output("asset-locate", no_evidence)
    check("有 value 无 evidence 被拦（防凭常识填值）",
          any(i["kind"] == "contract_violation" for i in issues), str(issues))

    missing_field = ('{"inferences":['
                     '{"field":"floor","value":null,"confidence":"none","evidence":[]}]}')
    ok, issues = skills.validate_output("asset-locate", missing_field)
    check("字段覆盖不全被拦（四字段必须都在）",
          any(i["kind"] == "missing_section" for i in issues), str(issues))

    print("=== 六、契约表完整性 ===")
    check("五个引擎均有契约",
          set(skills.CONTRACTS) == {"analyze", "diagnose", "ipconflict",
                                    "routetrace", "asset-locate"},
          str(sorted(skills.CONTRACTS)))
    check("文本引擎均有输出段落要求",
          all(skills.CONTRACTS[k]["output_sections"]
              for k in ("analyze", "diagnose", "ipconflict", "routetrace")))

    print("\n---- 结果：通过 %d，失败 %d ----" % (len(PASSED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  FAILED: %s" % f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
