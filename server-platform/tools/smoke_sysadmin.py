#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""sysadmin 模块端到端冒烟（对本地或远端服务运行，凭据全走环境变量）。

环境变量：
    ETP_API_BASE       服务基址（默认 http://127.0.0.1:18090）
    ETP_ADMIN_PWD      admin 账号口令（必填）
    ETP_TERMINAL_TOKEN 部署默认终端 token（默认 dev-token）
    ETP_TOKEN_TARGET   token 矩阵目标终端（须已注册；默认 WIN-Jun-office-PC；
                       以 WIN-SMOKE 开头则先注册——受白名单 fail-closed 约束）

覆盖：admin 登录与 role → session-info → operator 403 隔离 → 账户增删改/
口令复杂度 400/自删自降 409 → 禁用吊销会话 → LLM 读写与连通性测试 →
第三方 CRUD/toggle/非法 JSON 400 → token 生成/心跳/停用/启用/轮换全矩阵
+ config token 兼容 → 真实终端在线状态记录。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_BASE = os.environ.get("ETP_API_BASE", "http://127.0.0.1:18090").rstrip("/")
ADMIN_PWD = os.environ.get("ETP_ADMIN_PWD")
TERMINAL_TOKEN = os.environ.get("ETP_TERMINAL_TOKEN", "dev-token")
TOKEN_TARGET = os.environ.get("ETP_TOKEN_TARGET", "WIN-Jun-office-PC")

if not ADMIN_PWD:
    raise SystemExit("missing env: ETP_ADMIN_PWD")

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


def skip(name, detail=""):
    SKIPPED.append(name)
    print("  [SKIP] %s%s" % (name, (" | " + detail) if detail else ""))


def req(method, path, payload=None, headers=None, raw=False):
    url = API_BASE + path
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            body = resp.read()
            if raw:
                return resp.status, body
            try:
                return resp.status, json.loads(body.decode("utf-8"))
            except ValueError:
                return resp.status, {}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if raw:
            return exc.code, body
        try:
            return exc.code, json.loads(body.decode("utf-8"))
        except ValueError:
            return exc.code, {}


VALID_OP_PWD = "OpSmoke#2026z"


def main():
    ts = int(time.time())
    op_name = "op_smoke_%d" % ts
    print("=== EyeTerm sysadmin smoke ===")
    print("api base: %s\n" % API_BASE)

    # [1] health
    code, j = req("GET", "/api/v1/health")
    check("health 200", code == 200 and j.get("ok") is True)

    # [2] admin login + role
    print("[2] admin 登录与角色")
    code, j = req("POST", "/api/v1/console/login",
                  {"username": "admin", "password": ADMIN_PWD})
    atok = j.get("token")
    check("admin 登录 200", code == 200 and bool(atok), str(j))
    check("登录响应含 role=admin", j.get("role") == "admin", str(j.get("role")))
    ah = {"X-ETP-Console-Token": atok}

    code, j = req("GET", "/api/v1/console/session-info", headers=ah)
    check("session-info role=admin",
          code == 200 and j.get("role") == "admin" and j.get("username") == "admin")

    # [3] sysadmin 权限
    print("[3] sysadmin 路由组")
    code, j = req("GET", "/api/v1/console/sysadmin/users", headers=ah)
    check("GET users 200", code == 200 and isinstance(j.get("users"), list))
    check("password 字段脱敏",
          all(u.get("password") == "****" for u in j.get("users", [])))

    # [4] 账户管理
    print("[4] 账户管理")
    code, j = req("POST", "/api/v1/console/sysadmin/users",
                  {"username": op_name, "password": VALID_OP_PWD,
                   "role": "operator"}, headers=ah)
    check("创建 operator 200", code == 200 and j.get("ok"), str(j))
    op_id = j.get("id")
    code, j = req("POST", "/api/v1/console/sysadmin/users",
                  {"username": "weak_pwd_user", "password": "short1", "role": "operator"},
                  headers=ah)
    check("口令复杂度不足 400", code == 400, "code=%s %s" % (code, j))
    code, j = req("POST", "/api/v1/console/sysadmin/users",
                  {"username": op_name, "password": VALID_OP_PWD, "role": "operator"},
                  headers=ah)
    check("用户名重复 409", code == 409, "code=%s" % code)

    # [5] operator 隔离
    print("[5] operator 权限隔离")
    code, j = req("POST", "/api/v1/console/login",
                  {"username": op_name, "password": VALID_OP_PWD})
    otok = j.get("token")
    check("operator 登录 200", code == 200 and bool(otok), str(j))
    oh = {"X-ETP-Console-Token": otok}
    code, j = req("GET", "/api/v1/console/session-info", headers=oh)
    check("operator session-info role=operator",
          code == 200 and j.get("role") == "operator")
    code, j = req("GET", "/api/v1/console/sysadmin/users", headers=oh)
    check("operator 访问 sysadmin 403", code == 403, "code=%s" % code)
    code, j = req("GET", "/api/v1/console/terminals", headers=oh)
    check("operator 普通控制台 API 正常（隔离仅限 sysadmin）", code == 200)
    code, j = req("PUT", "/api/v1/console/sysadmin/users/%d" % op_id,
                  {"status": "active"}, headers=oh)
    check("operator 写 sysadmin 403", code == 403, "code=%s" % code)

    # [6] 禁用吊销 + 防线
    print("[6] 账户防线与吊销")
    code, j = req("PUT", "/api/v1/console/sysadmin/users/%d" % op_id,
                  {"status": "disabled"}, headers=ah)
    check("禁用 operator 200", code == 200, str(j))
    code, j = req("GET", "/api/v1/console/session-info", headers=oh)
    check("禁用后 operator 存量会话失效 401", code == 401, "code=%s" % code)
    code, j = req("POST", "/api/v1/console/login",
                  {"username": op_name, "password": VALID_OP_PWD})
    check("禁用后 operator 登录被拒", code == 403, "code=%s" % code)
    code, j = req("PUT", "/api/v1/console/sysadmin/users/%d" % op_id,
                  {"status": "active"}, headers=ah)
    check("重新启用 200", code == 200)
    code, j = req("PUT", "/api/v1/console/sysadmin/users/1",
                  {"role": "operator"}, headers=ah)
    check("自降级 409（对 admin 自身执行）", code == 409, "code=%s" % code)
    code, j = req("DELETE", "/api/v1/console/sysadmin/users/1", headers=ah)
    check("自删 409（对 admin 自身执行）", code == 409, "code=%s" % code)
    code, j = req("POST", "/api/v1/console/sysadmin/users/%d/reset-password" % op_id,
                  {"new_password": "NewOp$2026zz"}, headers=ah)
    check("重置口令 200", code == 200, str(j))
    code, j = req("POST", "/api/v1/console/login",
                  {"username": op_name, "password": "NewOp$2026zz"})
    check("新口令可登录", code == 200)
    code, j = req("DELETE", "/api/v1/console/sysadmin/users/%d" % op_id, headers=ah)
    check("删除测试账号 200", code == 200, str(j))
    code, j = req("POST", "/api/v1/console/login",
                  {"username": op_name, "password": "NewOp$2026zz"})
    check("删除后登录 401", code == 401, "code=%s" % code)

    # [7] 算力网关
    print("[7] 算力网关")
    code, j = req("GET", "/api/v1/console/sysadmin/llm", headers=ah)
    check("GET llm 200（含脱敏 key）", code == 200 and "api_key_masked" in j.get("llm", {}),
          str(j)[:120])
    code, j = req("POST", "/api/v1/console/sysadmin/llm/test", headers=ah)
    t = (j.get("test") or {}) if code == 200 else {}
    check("POST llm/test 200 且含四元组",
          code == 200 and all(k in t for k in ("ok", "status_code", "latency_ms", "error")),
          str(j)[:160])
    print("     llm/test 结果: ok=%s status=%s latency=%sms err=%s"
          % (t.get("ok"), t.get("status_code"), t.get("latency_ms"), t.get("error")))

    # [8] 第三方接口
    print("[8] 第三方接口")
    tp_name = "smoke_tp_%d" % ts
    code, j = req("POST", "/api/v1/console/sysadmin/third-party",
                  {"name": tp_name, "base_url": "https://smoke.example.local/api",
                   "method": "POST", "params_json": '{"a": 1}',
                   "headers_json": '{"X-Smoke": "1"}', "note": "smoke"}, headers=ah)
    check("创建接口 200", code == 200 and j.get("ok"), str(j))
    tpid = j.get("id")
    code, j = req("POST", "/api/v1/console/sysadmin/third-party",
                  {"name": "bad_json", "base_url": "https://x.local",
                   "params_json": '{"broken": '}, headers=ah)
    check("非法 params_json 400", code == 400, "code=%s" % code)
    code, j = req("PUT", "/api/v1/console/sysadmin/third-party/%d" % tpid,
                  {"note": "updated", "method": "GET"}, headers=ah)
    check("PUT 更新 200", code == 200)
    code, j = req("POST", "/api/v1/console/sysadmin/third-party/%d/toggle" % tpid,
                  {"enabled": False}, headers=ah)
    check("toggle 停用 200", code == 200)
    code, j = req("GET", "/api/v1/console/sysadmin/third-party", headers=ah)
    row = [a for a in j.get("apis", []) if a.get("id") == tpid]
    check("列表反映停用状态", bool(row) and row[0]["enabled"] is False)
    req("POST", "/api/v1/console/sysadmin/third-party/%d/toggle" % tpid,
        {"enabled": True}, headers=ah)
    code, j = req("DELETE", "/api/v1/console/sysadmin/third-party/%d" % tpid, headers=ah)
    check("删除 200", code == 200)
    code, j = req("GET", "/api/v1/console/sysadmin/third-party", headers=ah)
    check("删除后列表无该条", all(a.get("id") != tpid for a in j.get("apis", [])))

    # [9] 终端 token 矩阵
    print("[9] 终端 token 矩阵（目标终端 %s）" % TOKEN_TARGET)
    code, j = req("POST", "/api/v1/console/sysadmin/tokens",
                  {"label": "smoke_%d" % ts}, headers=ah)
    check("生成 token 200", code == 200 and (j.get("token") or {}).get("token"), str(j)[:120])
    tok_row = j.get("token") or {}
    t1, t1_id = tok_row.get("token"), tok_row.get("id")
    th = {"X-ETP-Token": t1}

    if TOKEN_TARGET.startswith("WIN-SMOKE"):
        code, j = req("POST", "/api/v1/terminals/register",
                      {"terminal_id": TOKEN_TARGET, "terminal_type": "windows",
                       "hostname": "smoke-token-host", "client_version": "smoke"},
                      headers=th)
        if code == 403:
            skip("注册冒烟终端被白名单拒绝（fail-closed），token 矩阵部分跳过")
    code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % TOKEN_TARGET, headers=th)
    if code == 404:
        skip("目标终端未注册，token 矩阵跳过（仅生产环境真实终端可验证）")
    else:
        check("新 token 心跳 200", code == 200, "code=%s %s" % (code, j))
        req("POST", "/api/v1/console/sysadmin/tokens/%d/disable" % t1_id, headers=ah)
        code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % TOKEN_TARGET, headers=th)
        check("停用后旧 token 心跳 401（立即失效）", code == 401, "code=%s" % code)
        req("POST", "/api/v1/console/sysadmin/tokens/%d/enable" % t1_id, headers=ah)
        code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % TOKEN_TARGET, headers=th)
        check("启用后恢复 200", code == 200, "code=%s" % code)
        code, j = req("POST", "/api/v1/console/sysadmin/tokens/%d/rotate" % t1_id, headers=ah)
        t2 = j.get("token")
        check("轮换返回新 token", code == 200 and bool(t2), str(j)[:120])
        code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % TOKEN_TARGET, headers=th)
        check("轮换后旧 token 401", code == 401, "code=%s" % code)
        code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % TOKEN_TARGET,
                      headers={"X-ETP-Token": t2})
        check("轮换后新 token 200", code == 200, "code=%s" % code)
        code, j = req("POST", "/api/v1/terminals/%s/heartbeat" % TOKEN_TARGET,
                      headers={"X-ETP-Token": TERMINAL_TOKEN})
        check("config 默认 token 兼容（心跳 200）", code == 200, "code=%s" % code)
        req("POST", "/api/v1/console/sysadmin/tokens/%d/disable" % t1_id, headers=ah)
        print("     测试 token #%s 已停用（清理）" % t1_id)

    # [10] 真实终端在线状态记录（心跳兼容性由外部驱动脚本观察 last_seen 前进）
    print("[10] 真实终端状态")
    code, j = req("GET", "/api/v1/console/terminals", headers=ah)
    row = [t for t in j.get("terminals", []) if t.get("terminal_id") == TOKEN_TARGET]
    if row:
        r = row[0]
        print("     %s online=%s last_seen=%s" % (TOKEN_TARGET, r.get("online"),
                                                  r.get("last_seen")))
        check("真实终端存在于终端列表", True)
    else:
        skip("终端列表中未见 %s（未接入或名称不同）" % TOKEN_TARGET)

    print("\n=== 结果：通过 %d / 失败 %d / 跳过 %d ==="
          % (len(PASSED), len(FAILED), len(SKIPPED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
