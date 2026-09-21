#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""白名单准入（ADR-013）隔离单测：fail-closed/放行/单 IP/网段/豁免/审计。

背景（ADR-028 延伸）：fail-closed 用例曾以「清空生产白名单」方式在生产冒烟
执行——与真实终端准入产生边缘交互（本机恰在名单内导致 403 断言 FAIL）。
本文件以隔离临时库完整覆盖准入逻辑，生产冒烟不再做破坏性白名单用例。"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))

import api as api_mod                                   # noqa: E402
import store as store_mod                               # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail else ""))


def main():
    tmp = tempfile.mkdtemp(prefix="etp_wl_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== whitelist tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


def run(tmp):
    st = store_mod.Store(os.path.join(tmp, "t.db"), config_token="tk")
    ctx = api_mod.ApiContext(st, {"terminal_token": "tk"},
                             settings={"terminal_token": "tk"})
    payload_reg = json.dumps({"terminal_id": "WIN-NEW", "terminal_type":
                              "windows", "hostname": "new-pc"}).encode()
    hb_new = "/api/v1/terminals/WIN-NEW/heartbeat"

    # [1] 空名单 fail-closed（ADR-013 核心）
    print("[1] 空名单 fail-closed")
    try:
        api_mod.dispatch(ctx, "POST", "/api/v1/terminals/register", {},
                         {"x-etp-token": "tk"}, payload_reg, "10.99.99.9")
        check("空名单拒绝新注册 403", False)
    except api_mod.ApiError as exc:
        check("空名单拒绝新注册 403", exc.status == 403)
    try:
        api_mod.dispatch(ctx, "POST", hb_new, {}, {"x-etp-token": "tk"},
                         b"{}", "10.99.99.9")
        check("未注册+未授权心跳 403", False)
    except api_mod.ApiError as exc:
        check("未注册+未授权心跳 403", exc.status == 403)
    n_audit = st._conn.execute(
        "SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    check("403 拒绝落审计", n_audit >= 2, "audit=%d" % n_audit)

    # [2] 0.0.0.0/0 全开放
    print("[2] 0.0.0.0/0 全开放")
    st.whitelist_add("0.0.0.0/0", "allow-all")
    try:
        api_mod.dispatch(ctx, "POST", "/api/v1/terminals/register", {},
                         {"x-etp-token": "tk"}, payload_reg, "10.99.99.9")
        check("全开放注册 200", True)
    except api_mod.ApiError as exc:
        check("全开放注册 200", False, "code=%d" % exc.status)

    # [3] 单 IP /32 与已注册终端豁免
    print("[3] 单 IP 条目与豁免")
    st.whitelist_delete(1)  # 移除 0.0.0.0/0（首个自增 id=1）
    st.whitelist_add("172.17.90.215/32", "known-host")
    hb_unreg = "/api/v1/terminals/WIN-UNREG/heartbeat"
    try:
        api_mod.dispatch(ctx, "POST", hb_unreg, {}, {"x-etp-token": "tk"},
                         b"{}", "10.99.99.9")
        check("未注册+名单外心跳 403", False)
    except api_mod.ApiError as exc:
        check("未注册+名单外心跳 403", exc.status == 403)
    st.register_terminal("WIN-NEW", "windows", "new-pc", "Win", "1.0.0",
                         "172.17.90.215")
    try:
        api_mod.dispatch(ctx, "POST", hb_new, {}, {"x-etp-token": "tk"},
                         b"{}", "10.99.99.9")
        check("已注册终端豁免（名单外源仍放行，ADR-013）", True)
    except api_mod.ApiError as exc:
        check("已注册终端豁免（名单外源仍放行，ADR-013）", False,
              "code=%d" % exc.status)

    # [4] 网段条目命中（名单内新终端注册）
    print("[4] 网段条目")
    st.whitelist_add("172.17.90.0/24", "office-seg")
    payload_seg = json.dumps({"terminal_id": "WIN-SEG", "terminal_type":
                              "windows", "hostname": "seg-pc"}).encode()
    try:
        api_mod.dispatch(ctx, "POST", "/api/v1/terminals/register", {},
                         {"x-etp-token": "tk"}, payload_seg, "172.17.90.77")
        check("网段命中注册 200", True)
    except api_mod.ApiError as exc:
        check("网段命中注册 200", False, "code=%d" % exc.status)

    # [5] 非法 CIDR（API 层 ip_network 校验 400；store 层不校验为已知分层）
    print("[5] 非法 CIDR")
    try:
        api_mod._console_whitelist(
            ctx, "POST", ["api", "v1", "console", "whitelist"],
            json.dumps({"cidr": "not-a-cidr", "note": "bad"}).encode())
        check("API 层拒绝非法 CIDR 400", False)
    except api_mod.ApiError as exc:
        check("API 层拒绝非法 CIDR 400", exc.status == 400)

    # [6] 精确清理幂等（对应 smoke 的 smoke-temp 精确删除语义）
    print("[6] 精确清理")
    wid = st.whitelist_add("192.0.2.9/32", "smoke-temp")
    check("添加测试条目", wid is not None)
    check("删除测试条目", st.whitelist_delete(wid) is True)
    check("重复删除 404 数据源", st.whitelist_delete(wid) is False)
    remaining = [r["cidr"] for r in st._conn.execute(
        "SELECT cidr FROM whitelist")]
    check("清理后无 smoke 残留", "192.0.2.9/32" not in remaining,
          str(remaining))


if __name__ == "__main__":
    sys.exit(main())
