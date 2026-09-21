#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌面管控服务端单测（零凭据零网络，临时库；ADR-036）。

覆盖：图片解析（PNG/JPEG/坏格式）/ 壁纸库 CRUD 与引用拒绝 / 匹配推荐三分支 /
四类策略校验 / 发布与生效策略 / 终端拉取与下发记录 upsert / report 状态机
（applied/warn/partial/failed）/ overview。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.normpath(os.path.join(HERE, "..", "server"))
sys.path.insert(0, SERVER)

import desktop_policy as dp  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + detail) if detail and not cond else ""))


def png_bytes(w, h, extra=b""):
    # 最小 PNG：签名 + IHDR（长度 13 + 类型 + w/h + 其余 9 字节 + CRC 占位）
    ihdr = b"\x00\x00\x00\x0dIHDR" + w.to_bytes(4, "big") \
        + h.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4
    return dp._PNG_SIG + ihdr + extra


def jpeg_bytes(w, h):
    # 最小 JPEG：SOI + SOF0（8 高度 4 宽度）+ EOI
    sof = b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" \
        + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" \
        + b"\x01\x22\x00" * 3
    return b"\xff\xd8" + sof + b"\xff\xd9"


def main():
    tmp = tempfile.mkdtemp(prefix="dp_test_")
    db = os.path.join(tmp, "test.db")
    store = dp.DesktopPolicyStore(db, tmp)
    conn = store._conn
    # 最小 terminals / asset_groups 表（dp 只读依赖）
    conn.execute("CREATE TABLE IF NOT EXISTS terminals ("
                 "terminal_id TEXT PRIMARY KEY, group_id INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS asset_groups ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)")
    conn.execute("INSERT INTO asset_groups(name) VALUES('办公区')")
    conn.execute("INSERT INTO terminals VALUES('T-A', 1)")
    conn.execute("INSERT INTO terminals VALUES('T-B', NULL)")
    conn.execute("INSERT INTO terminals VALUES('T-C', 1)")
    conn.commit()

    print("== 1. 图片解析 ==")
    check("png size", dp._image_size(png_bytes(1920, 1080)) == (1920, 1080,
                                                                "png"))
    check("jpeg size", dp._image_size(jpeg_bytes(2560, 1440)) == (2560, 1440,
                                                                  "jpg"))
    for bad in (b"", b"notanimage", b"\x89PNG\r\n\x1a\nshort"):
        try:
            dp._image_size(bad)
            check("bad image rejected", False)
        except ValueError:
            check("bad image rejected", True)
    check("monitors brief parse",
          dp.parse_monitors_brief("0,1920,1080,1;1,1280,720,0") ==
          [{"monitor_index": 0, "width": 1920, "height": 1080,
            "primary": True},
           {"monitor_index": 1, "width": 1280, "height": 720,
            "primary": False}])
    check("monitors brief bad seg skipped",
          dp.parse_monitors_brief("x,1,2;0,1920,1080") ==
          [{"monitor_index": 0, "width": 1920, "height": 1080,
            "primary": False}])

    print("== 2. 壁纸库 ==")
    w1 = store.add_wallpaper(png_bytes(1920, 1080), "科室背景-1080P",
                             "科室", None, "admin")
    check("add wallpaper png", w1["width"] == 1920 and w1["height"] == 1080
          and w1["aspect"] == 1.777778 and w1["size_bytes"] > 0)
    w2 = store.add_wallpaper(jpeg_bytes(1920, 1080), "宣传-1920", "宣传",
                             None, "admin")
    w3 = store.add_wallpaper(png_bytes(3840, 2160), "默认-4K", "default",
                             None, "admin")
    w4 = store.add_wallpaper(jpeg_bytes(1280, 1024), "默认-5x4", "default",
                             None, "admin")
    try:
        store.add_wallpaper(png_bytes(1920, 1080), "重复内容", "科室",
                            None, "admin")
        check("dup sha rejected", False)
    except dp.DpError as e:
        check("dup sha rejected", "相同内容" in e.message and
              e.http_status == 400)
    try:
        store.add_wallpaper(b"notimage", "坏图", "x", None, "admin")
        check("bad format rejected", False)
    except dp.DpError as e:
        check("bad format rejected", "PNG" in e.message)
    try:
        store.add_wallpaper(png_bytes(1920, 1080), "x" * 65, "科室", None,
                            "admin")
        check("name too long rejected", False)
    except dp.DpError as e:
        check("name too long rejected", "名称过长" in e.message)
    lst = store.list_wallpapers(None, 1, 2)
    check("list wallpapers paging",
          lst["total"] == 4 and len(lst["wallpapers"]) == 2
          and set(lst["categories"]) == {"default", "宣传", "科室"})
    lst2 = store.list_wallpapers("default", 1, 50)
    check("list filter category", lst2["total"] == 2)
    data, mime, sha = store.get_wallpaper_file(w1["id"])
    check("get wallpaper file", data == png_bytes(1920, 1080)
          and mime == "image/png" and len(sha) == 64)

    print("== 3. 匹配推荐三分支 ==")
    cands = store._enabled_candidates()
    wid, match = store.match_wallpaper(1920, 1080, cands)
    check("match exact", match == "exact"
          and wid in (w1["id"], w2["id"]))
    wid, match = store.match_wallpaper(1600, 900, cands)   # 同 16:9 更低分
    check("match aspect_higher_res", match == "aspect_higher_res"
          and wid in (w1["id"], w2["id"]))   # 面积最小者优先（非 4K）
    wid, match = store.match_wallpaper(1024, 768, cands)   # 4:3 无同比
    check("match default_fallback prefers default category",
          match == "default_fallback" and wid in (w3["id"], w4["id"]))
    check("match none on empty pool",
          store.match_wallpaper(1024, 768, []) == (None, None))

    print("== 4. 策略校验 ==")
    good = {"desktop_wallpaper": {
                "enabled": True, "mode": "stretch",
                "wallpaper_ids": [w1["id"], w2["id"], w3["id"]],
                "rotation": {"freq": "daily", "anchor_date": "2026-10-01"}},
            "lock_screen": {"enabled": True, "wallpaper_id": w1["id"]},
            "power_plan": {"enabled": True, "plan": "custom",
                           "custom": {"display_off_ac": 600,
                                      "display_off_dc": 300,
                                      "sleep_ac": 1800, "sleep_dc": 900,
                                      "disk_off_ac": 1200,
                                      "disk_off_dc": 720,
                                      "power_button_ac": "sleep",
                                      "power_button_dc": "shutdown"}},
            "idle_lock": {"enabled": True, "minutes": 15,
                          "screen_saver_secure": True}}
    clean = store.validate_payload(good)
    check("validate good payload", set(clean.keys()) == set(good.keys()))
    bad_cases = [
        ("unknown type", {"foo": {"enabled": True}}),
        ("missing enabled", {"idle_lock": {"minutes": 5}}),
        ("bad mode", {"desktop_wallpaper": {"enabled": True,
                                            "mode": "zoom",
                                            "wallpaper_ids": [w1["id"]]}}),
        ("bad wallpaper_ids", {"desktop_wallpaper": {
            "enabled": True, "wallpaper_ids": ["w12"]}}),
        ("nonexistent wallpaper", {"lock_screen": {
            "enabled": True, "wallpaper_id": 99999}}),
        ("custom out of range", {"power_plan": {
            "enabled": True, "plan": "custom",
            "custom": {"display_off_ac": 1, "display_off_dc": 300,
                       "sleep_ac": 1800, "sleep_dc": 900,
                       "disk_off_ac": 1200, "disk_off_dc": 720,
                       "power_button_ac": "sleep",
                       "power_button_dc": "shutdown"}}}),
        ("bad power button", {"power_plan": {
            "enabled": True, "plan": "custom",
            "custom": {"display_off_ac": 600, "display_off_dc": 300,
                       "sleep_ac": 1800, "sleep_dc": 900,
                       "disk_off_ac": 1200, "disk_off_dc": 720,
                       "power_button_ac": "explode",
                       "power_button_dc": "shutdown"}}}),
        ("idle minutes out of range", {"idle_lock": {
            "enabled": True, "minutes": 0}}),
    ]
    for label, p in bad_cases:
        try:
            store.validate_payload(p)
            check("validate rejects %s" % label, False)
        except dp.DpError:
            check("validate rejects %s" % label, True)

    print("== 5. 策略 CRUD + 发布 ==")
    p1 = store.save_policy("办公区基准", 1, good, "admin")
    check("create policy", p1["revision"] == 0 and p1["group_id"] == 1)
    _names = [p["group_name"] for p in store.list_policies()["policies"]]
    check("group name resolved", _names == ["办公区"], repr(_names))
    good2 = {"desktop_wallpaper": {"enabled": True, "mode": "fill",
                                   "wallpaper_ids": [w4["id"]]}}
    p2 = store.save_policy("会议室方案", None, good2, "admin")
    _names = [p["group_name"] for p in store.list_policies()["policies"]]
    check("global policy group name", _names == ["全部终端", "办公区"],
          repr(_names))
    p1b = store.save_policy("办公区基准v2", 1, good, "admin", policy_id=1)
    check("update policy keeps revision", p1b["revision"] == 0
          and p1b["name"] == "办公区基准v2")
    pub1 = store.publish_policy(1, "admin")
    check("publish revision=1 (global)", pub1["revision"] == 1)
    pub2 = store.publish_policy(p2["id"], "admin")
    check("publish revision=2 (global monotonic)", pub2["revision"] == 2)

    print("== 6. 终端拉取与下发记录 ==")
    r = store.get_policy_for_terminal("T-A", 0, "0,1920,1080,1")
    check("pull has update", not r.get("unchanged") and r["revision"] == 1
          and r["server_time"] > 0)
    dw = r["policies"]["desktop_wallpaper"]
    check("per_monitor matched",
          dw["per_monitor"] == [{"monitor_index": 0,
                                 "wallpaper_id": w2["id"],
                                 "match": "exact",
                                 "checksum": w2["sha256"]}])
    check("lock_screen checksum carried",
          r["policies"]["lock_screen"]["checksum"] == w1["sha256"])
    check("payload classes carried",
          set(r["policies"].keys()) == {"desktop_wallpaper", "lock_screen",
                                        "power_plan", "idle_lock"})
    row = conn.execute("SELECT status FROM dp_deliveries").fetchone()
    check("delivery delivered on pull", row["status"] == "delivered")
    r0 = store.get_policy_for_terminal("T-A", 1, "0,1920,1080,1")
    check("pull unchanged when same revision", r0.get("unchanged") is True
          and r0["revision"] == 1)
    # 无组终端 → 吃全局策略（group_id NULL = 全部终端，v1.1 裁定）
    r2 = store.get_policy_for_terminal("T-B", 0, "0,1024,768,1")
    check("global policy fallback for ungrouped terminal",
          r2.get("unchanged") is not True and r2["revision"] == 2
          and r2["policies"]["desktop_wallpaper"]["mode"] == "fill")
    # 编辑后未发布仍 revision=1；再发布 → revision=3，拉取有更新
    store.save_policy("办公区基准v3", 1, good, "admin", policy_id=1)
    r1 = store.get_policy_for_terminal("T-A", 1, "0,1920,1080,1")
    check("edit without publish no bump", r1.get("unchanged") is True)
    pub3 = store.publish_policy(1, "admin")
    check("publish revision=3", pub3["revision"] == 3)
    r3 = store.get_policy_for_terminal("T-A", 1, "0,1920,1080,1")
    check("repull after publish", r3["revision"] == 3)
    # 回传后重复拉取不重置状态
    store.save_report("T-A", {"revision": 3, "results": [
        {"policy": "desktop_wallpaper", "ok": True,
         "detail": {"mode": "stretch", "verify": "grab_ok"}}]})
    store.get_policy_for_terminal("T-A", 2, "0,1920,1080,1")
    st = conn.execute("SELECT status FROM dp_deliveries WHERE revision=3"
                      " AND terminal_id='T-A'").fetchone()["status"]
    check("re-pull keeps terminal state", st == "applied")

    print("== 7. report 状态机 ==")
    store.get_policy_for_terminal("T-A", 0, "0,1920,1080,1")   # → rev3 delivered
    store.save_report("T-A", {"revision": 3, "results": [
        {"policy": "desktop_wallpaper", "ok": True,
         "detail": {"mode": "stretch", "monitors": 1, "verify": "grab_ok"}},
        {"policy": "idle_lock", "ok": True, "detail": {"minutes": 15}}]})
    st = conn.execute("SELECT status, error_code FROM dp_deliveries WHERE"
                      " revision=3 AND terminal_id='T-A'").fetchone()
    check("report applied", st["status"] == "applied"
          and st["error_code"] is None)
    # warn 用独立终端（状态机为同行覆盖式，独立行保留 warn 终态供 overview）
    store.get_policy_for_terminal("T-C", 0, "0,1920,1080,1")
    store.save_report("T-C", {"revision": 3, "results": [
        {"policy": "desktop_wallpaper", "ok": True,
         "detail": {"match": "default_fallback"}}]})
    st = conn.execute("SELECT status, error_code FROM dp_deliveries WHERE"
                      " revision=3 AND terminal_id='T-C'").fetchone()
    check("report warn on default_fallback", st["status"] == "warn"
          and st["error_code"] == "default_fallback")
    store.save_report("T-A", {"revision": 3, "results": [
        {"policy": "desktop_wallpaper", "ok": True},
        {"policy": "lock_screen", "ok": False,
         "error": {"code": "no_admin", "message": "锁屏设置需管理员权限"}}]})
    st = conn.execute("SELECT status, error_code, error_detail FROM"
                      " dp_deliveries WHERE revision=3 AND"
                      " terminal_id='T-A'").fetchone()
    check("report partial", st["status"] == "partial"
          and st["error_code"] == "no_admin"
          and "no_admin" in st["error_detail"])
    store.save_report("T-A", {"revision": 3, "results": [
        {"policy": "desktop_wallpaper", "ok": False,
         "error": {"code": "blocked_by_security",
                   "message": "终端安全软件拦截壁纸变更"}}]})
    st = conn.execute("SELECT status, error_code FROM dp_deliveries WHERE"
                      " revision=3 AND terminal_id='T-A'").fetchone()
    check("report failed with blocked_by_security",
          st["status"] == "failed"
          and st["error_code"] == "blocked_by_security")
    try:
        store.save_report("T-A", {"revision": 999, "results": [
            {"policy": "x", "ok": True}]})
        check("report unknown revision 404", False)
    except dp.DpError as e:
        check("report unknown revision 404", e.http_status == 404)
    try:
        store.save_report("T-A", {"revision": 3, "results": []})
        check("report empty results rejected", False)
    except dp.DpError:
        check("report empty results rejected", True)

    print("== 8. 下架保护 + overview ==")
    try:
        store.delete_wallpaper(w1["id"])
        check("delete referenced wallpaper rejected", False)
    except dp.DpError as e:
        check("delete referenced wallpaper rejected", e.http_status == 409
              and "策略" in e.message)
    ov = store.overview()
    check("overview counts", ov["wallpapers"] == 4 and ov["policies"] == 2
          and ov["blocked_7d"] == 1 and ov["warn_7d"] == 1,
          json.dumps(ov, ensure_ascii=False))
    # 策略 1 移除对 w2 的引用后可下架
    good3 = {"desktop_wallpaper": {
        "enabled": True, "mode": "stretch",
        "wallpaper_ids": [w1["id"], w3["id"]],
        "rotation": {"freq": "daily", "anchor_date": "2026-10-01"}},
        "lock_screen": {"enabled": True, "wallpaper_id": w1["id"]},
        "power_plan": good["power_plan"],
        "idle_lock": good["idle_lock"]}
    store.save_policy("办公区基准v4", 1, good3, "admin", policy_id=1)
    store.delete_wallpaper(w2["id"])
    check("delete unreferenced wallpaper",
          store.get_wallpaper(w2["id"]) is None
          and not os.path.isfile(w2["path"]))

    store.close()
    print("\n=== test_desktop_policy ===")
    print("PASS: %d  FAIL: %d" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failed: %s" % ", ".join(FAILED))
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
