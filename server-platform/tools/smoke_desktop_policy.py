#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面管控模块 mock 冒烟（本地隔离环境，零凭据；ADR-036）。

链路：登录 → 壁纸上传(PUT raw bytes) → 列表/分类 → 策略创建(校验 400 矩阵)
→ 发布 → 终端 policy 拉取（有/无更新 + per_monitor 匹配）→ 壁纸下载
（X-DP-Checksum）→ report 回传（applied/blocked_by_security）→ deliveries
筛选 → overview → operator 权限矩阵 → 页面 200。

环境变量：ETP_API_BASE（默认 http://127.0.0.1:18090）、
ETP_CONSOLE_PASSWORD（默认 dev-console）、ETP_TERMINAL_TOKEN（默认 dev-token）。
"""
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API_BASE = os.environ.get("ETP_API_BASE", "http://127.0.0.1:18090").rstrip("/")
PASSWORD = os.environ.get("ETP_CONSOLE_PASSWORD", "dev-console")
TOKEN = os.environ.get("ETP_TERMINAL_TOKEN", "dev-token")

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def http(method, path, token=None, payload=None, raw=None, headers=None,
         terminal=None):
    h = dict(headers or {})
    if token:
        h["X-ETP-Console-Token"] = token
    if terminal:
        h["X-ETP-Token"] = terminal
    if payload is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    elif raw is not None:
        body = raw
    else:
        body = None
    req = urllib.request.Request(API_BASE + path, data=body, headers=h,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            hdrs = dict(resp.headers)
            try:
                return resp.status, json.loads(data.decode("utf-8")), hdrs
            except ValueError:
                return resp.status, data, hdrs
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data.decode("utf-8")), {}
        except ValueError:
            return e.code, data, {}


def png_bytes(w, h):
    ihdr = b"\x00\x00\x00\x0dIHDR" + w.to_bytes(4, "big") \
        + h.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"\x00" * 16


def main():
    print("=== desktop-policy mock smoke ===")
    # 注册一个冒烟终端（供 report/pull；上行需 X-ETP-Token）
    st, r, _ = http("POST", "/api/v1/terminals/register", terminal=TOKEN,
                    payload={
                        "terminal_id": "WIN-DP-SMOKE",
                        "terminal_type": "windows",
                        "hostname": "DP-SMOKE",
                        "os_info": "Windows 11 专业版",
                        "client_version": "test"})
    check("register terminal", st == 200 and r.get("ok"))

    # 登录（admin）
    st, r, _ = http("POST", "/api/v1/console/login", payload={
        "username": "admin", "password": PASSWORD})
    check("admin login", st == 200 and r.get("ok"))
    admin = r["token"]

    # 幂等准备 operator 测试账户（存在则 admin 强制重置口令，否则创建）
    st, r, _ = http("GET", "/api/v1/console/sysadmin/users", token=admin)
    print("  [dbg] GET users ->", st, str(r)[:200])
    op_id = None
    for u in r.get("users", []):
        if u.get("username") == "operator":
            op_id = u["id"]
    if op_id is None:
        st, r, _ = http("POST", "/api/v1/console/sysadmin/users", token=admin,
                        payload={"username": "operator",
                                 "password": "DpSmoke#2026",
                                 "role": "operator"})
        check("create operator account", st == 200 and r.get("ok"), str(r))
        op_id = r.get("id")
    st, r, _ = http("POST",
                    "/api/v1/console/sysadmin/users/%d/reset-password" % op_id,
                    token=admin, payload={"new_password": "DpSmoke#2026"})
    check("reset operator password", st == 200 and r.get("ok"), str(r))
    st, r, _ = http("POST", "/api/v1/console/login", payload={
        "username": "operator", "password": "DpSmoke#2026"})
    check("operator login", st == 200 and r.get("ok"), str(r))
    operator = r["token"]
    if r.get("must_change_password"):
        import time as _t
        fresh_pwd = "DpSmoke#%d" % (_t.time() * 1000)
        st, r, _ = http("POST", "/api/v1/console/password",
                        token=operator,
                        payload={"old_password": "DpSmoke#2026",
                                 "new_password": fresh_pwd,
                                 "confirm_password": fresh_pwd})
        check("operator first-login password change", st == 200
              and r.get("ok"), str(r))
        st, r, _ = http("POST", "/api/v1/console/login", payload={
            "username": "operator", "password": fresh_pwd})
        check("operator relogin", st == 200 and r.get("ok"))
        operator = r["token"]

    # 1. 壁纸上传（PUT raw bytes；URL 路径中文需 quote；随机尾字节保证可重复运行）
    q = urllib.parse.quote
    st, r, _ = http("PUT",
                    "/api/v1/console/desktoppolicy/wallpapers/"
                    + q("大屏主视觉")
                    + "?category=" + q("宣传") + "&mime=image/png",
                    token=admin,
                    raw=png_bytes(1920, 1080) + os.urandom(8))
    check("wallpaper upload", st == 200 and r.get("ok")
          and r["wallpaper"]["width"] == 1920
          and r["wallpaper"]["height"] == 1080)
    w1 = r["wallpaper"]
    st, r, _ = http("PUT",
                    "/api/v1/console/desktoppolicy/wallpapers/"
                    + q("默认兜底") + "?category=default&mime=image/png",
                    token=admin,
                    raw=png_bytes(3840, 2160) + os.urandom(8))
    w2 = r["wallpaper"]
    check("wallpaper upload second", st == 200)
    st, r, _ = http("PUT", "/api/v1/console/desktoppolicy/wallpapers/"
                    + q("重复内容"), token=admin,
                    raw=png_bytes(1920, 1080) + b"dup-fixed")
    check("dup content rejected", st == 400 and "相同内容" in r.get("error", ""))
    st, r, _ = http("PUT", "/api/v1/console/desktoppolicy/wallpapers/bad",
                    token=admin, raw=b"notimage")
    check("bad format rejected", st == 400 and "PNG" in r.get("error", ""))

    # 2. 列表
    st, r, _ = http("GET", "/api/v1/console/desktoppolicy/wallpapers", token=admin)
    check("wallpaper list", st == 200 and r["total"] >= 2
          and "宣传" in r["categories"])

    # 3. 策略创建（400 矩阵）
    payload = {"desktop_wallpaper": {
        "enabled": True, "mode": "stretch",
        "wallpaper_ids": [w1["id"]],
        "rotation": {"freq": "daily", "anchor_date": "2026-10-01"}},
        "lock_screen": {"enabled": True, "wallpaper_id": w1["id"]},
        "power_plan": {"enabled": False, "plan": "balanced"},
        "idle_lock": {"enabled": True, "minutes": 15,
                      "screen_saver_secure": True}}
    st, r, _ = http("POST", "/api/v1/console/desktoppolicy/policies", token=admin,
                    payload={"name": "冒烟基准", "group_id": None,
                             "payload": payload})
    check("policy created", st == 200 and r["policy"]["revision"] == 0)
    pid = r["policy"]["id"]
    st, r, _ = http("POST", "/api/v1/console/desktoppolicy/policies", token=admin,
                    payload={"name": "坏策略", "payload": {"bad": {}}})
    check("bad payload rejected", st == 400)
    st, r, _ = http("POST", "/api/v1/console/desktoppolicy/policies", token=admin,
                    payload={"name": "缺壁纸", "payload": {
                        "lock_screen": {"enabled": True,
                                        "wallpaper_id": 99999}}})
    check("nonexistent wallpaper rejected", st == 400)

    # 4. 发布 + 终端拉取
    st, r, _ = http("POST",
                    "/api/v1/console/desktoppolicy/policies/%d/publish" % pid,
                    token=admin)
    check("publish bumps revision", st == 200
          and r["policy"]["revision"] >= 1)
    rev = r["policy"]["revision"]
    st, r, _ = http("GET",
                    "/api/v1/terminals/WIN-DP-SMOKE/desktoppolicy/policy"
                    "?revision=0&mi=0,1920,1080,1", terminal=TOKEN)
    check("terminal pull has update", st == 200 and r.get("revision") == rev
          and not r.get("unchanged"), str(r)[:200])
    dw = r["policies"]["desktop_wallpaper"]
    check("per_monitor exact match",
          dw["per_monitor"] == [{"monitor_index": 0,
                                 "wallpaper_id": w1["id"],
                                 "match": "exact",
                                 "checksum": w1["sha256"]}])
    check("lock_screen checksum", r["policies"]["lock_screen"]
          .get("checksum") == w1["sha256"])
    st, r, _ = http("GET",
                    "/api/v1/terminals/WIN-DP-SMOKE/desktoppolicy/policy"
                    "?revision=%d" % rev, terminal=TOKEN)
    check("pull unchanged", st == 200 and r.get("unchanged") is True)

    # 5. 壁纸下载（X-DP-Checksum）
    st, data, hdrs = http(
        "GET",
        "/api/v1/terminals/WIN-DP-SMOKE/desktoppolicy/wallpaper/%d"
        % w1["id"], terminal=TOKEN)
    check("wallpaper download + checksum",
          st == 200
          and hashlib.sha256(data).hexdigest() == w1["sha256"]
          and hdrs.get("X-DP-Checksum") == w1["sha256"])
    st, r, _ = http("GET",
                    "/api/v1/terminals/WIN-DP-SMOKE/desktoppolicy/wallpaper"
                    "/99999", terminal=TOKEN)
    check("download missing wallpaper 404", st == 404)

    # 6. report 回传
    st, r, _ = http("POST",
                    "/api/v1/terminals/WIN-DP-SMOKE/desktoppolicy/report",
                    terminal=TOKEN,
                    payload={"revision": rev, "reported_at": 1760000000,
                             "monitors": [{"index": 0, "width": 1920,
                                           "height": 1080, "primary": True}],
                             "session_type": "console",
                             "results": [
                                 {"policy": "desktop_wallpaper", "ok": True,
                                  "detail": {"verify": "grab_ok"}}]})
    check("report applied", st == 200 and r["status"] == "applied")
    st, r, _ = http("GET",
                    "/api/v1/console/desktoppolicy/deliveries?status=applied",
                    token=admin)
    check("deliveries filter applied",
          st == 200 and any(d["terminal_id"] == "WIN-DP-SMOKE"
                            for d in r["deliveries"]))
    st, r, _ = http("POST",
                    "/api/v1/terminals/WIN-DP-SMOKE/desktoppolicy/report",
                    terminal=TOKEN,
                    payload={"revision": rev, "results": [
                        {"policy": "desktop_wallpaper", "ok": False,
                         "error": {"code": "blocked_by_security",
                                   "message": "终端安全软件拦截壁纸变更"}}]})
    check("report blocked_by_security", st == 200 and r["status"] == "failed")
    st, r, _ = http("GET",
                    "/api/v1/console/desktoppolicy/deliveries"
                    "?error_code=blocked_by_security", token=admin)
    check("deliveries filter blocked",
          st == 200 and r["total"] >= 1
          and r["deliveries"][0]["error_code"] == "blocked_by_security")

    # 7. overview
    st, r, _ = http("GET", "/api/v1/console/desktoppolicy/overview", token=admin)
    check("overview", st == 200 and r["wallpapers"] >= 2
          and r["policies"] >= 1 and r["blocked_7d"] >= 1)

    # 8. operator 权限矩阵（读 OK / 写 403）
    st, r, _ = http("GET", "/api/v1/console/desktoppolicy/wallpapers",
                    token=operator)
    check("operator read ok", st == 200)
    st, r, _ = http("PUT",
                    "/api/v1/console/desktoppolicy/wallpapers/x?mime=image/png",
                    token=operator, raw=png_bytes(800, 600))
    check("operator upload 403", st == 403)
    st, r, _ = http("POST", "/api/v1/console/desktoppolicy/policies",
                    token=operator, payload={"name": "x", "payload": {}})
    check("operator policy 403", st == 403)
    st, r, _ = http("POST",
                    "/api/v1/console/desktoppolicy/policies/%d/publish" % pid,
                    token=operator)
    check("operator publish 403", st == 403)

    # 9. 页面 200
    st, r, _ = http("GET", "/", token=admin)
    check("console page 200", st == 200)

    print("\n=== smoke summary ===")
    print("PASS: %d  FAIL: %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        sys.exit(1)
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
