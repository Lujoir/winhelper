# -*- coding: utf-8 -*-
"""临时脚本: 恢复真实 LLM key（被冒烟[9]测试值覆盖）并单次验证 AI 真实调用"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:18090"
REAL_KEY = "sk-2ue5VEMBc1boUYMU9ixmFtwI6PsDsG7nhpVJOybCapsixJJH"
CONSOLE_PW = "597Ub_YEAyC5Pu2h"
TERMINAL_TOKEN = "fb77d34a28e82cdba4e887a04add4b6d3809c36f1166504b"
SMOKE_TERMINAL = "WIN-SMOKE-1788838577"


def post(path, obj, headers=None):
    data = json.dumps(obj).encode("utf-8") if obj is not None else b"{}"
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


# 1) 控制台登录
code, body = post("/api/v1/console/login", {"password": CONSOLE_PW})
j = json.loads(body)
ctoken = ""
for k in ("console_token", "token", "session", "ctoken"):
    if isinstance(j.get(k), str) and j[k]:
        ctoken = j[k]
        break
if not ctoken:
    # 兜底: 扫描响应里第一个像 token 的字符串字段
    for k, v in j.items():
        if isinstance(v, str) and len(v) > 20:
            ctoken = v
            break
print("[1] console login:", code, "| token acquired:", bool(ctoken))
ch = {"X-ETP-Console-Token": ctoken}

# 2) 恢复真实 LLM key
code, body = post("/api/v1/console/settings",
                  {"settings": {"llm.api_key": REAL_KEY}}, headers=ch)
print("[2] restore llm.api_key:", code, "(200=已重新加密落库)")

# 3) 单次 AI 真实调用验证
code, body = post("/api/v1/ai/analyze",
                  {"terminal_id": SMOKE_TERMINAL,
                   "issue_description": "冒烟验证：CPU 占用持续 95% 且系统卡顿，请简要分析可能原因并给出处理建议。"},
                  headers={"X-ETP-Token": TERMINAL_TOKEN})
print("[3] ai analyze:", code)
try:
    j2 = json.loads(body)
    resp_text = j2.get("response") or ""
    print("    ok:", j2.get("ok"), "| analysis_id:", j2.get("analysis_id"),
          "| response_len:", len(resp_text))
    print("    response head:", resp_text[:280].replace("\n", " "))
except Exception as ex:
    print("    parse fail:", ex, "| body head:", body[:280])
print("VERIFY_DONE")
