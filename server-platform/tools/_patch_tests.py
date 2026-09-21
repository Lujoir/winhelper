# -*- coding: utf-8 -*-
"""一次性补丁：smoke.py 与 e2e_console.py 扩展功能1测试（执行后删除）。"""
import io

SMOKE = r"c:\Users\10604\CodeBuddy\20260522083146\server-platform\tools\smoke.py"
E2E = r"c:\Users\10604\CodeBuddy\20260522083146\server-platform\tools\e2e_console.py"

# ---------- smoke.py ----------

with io.open(SMOKE, "r", encoding="utf-8") as fh:
    src = fh.read()

anchor = "    _summary()\n\n\ndef _summary():"
insert = '''    # 7. 白名单准入（功能1：fail-closed + 精准放行）
    print("[7] whitelist admission")
    code, j = req("GET", "/api/v1/console/whitelist", headers=ch)
    initial = [(w["cidr"], w["note"], w["enabled"]) for w in j.get("whitelist", [])]
    removed = []
    for w in j.get("whitelist", []):
        if w["cidr"] in ("0.0.0.0/0", "::/0"):
            req("DELETE", "/api/v1/console/whitelist/%d" % w["id"], headers=ch)
            removed.append(w["id"])
    tid2 = "WIN-ADMIT-%d" % ts
    code2, _ = req("POST", "/api/v1/terminals/register", {
        "terminal_id": tid2, "terminal_type": "windows", "hostname": "ADMIT"},
        headers={"X-ETP-Token": TOKEN})
    check("empty whitelist rejects new register 403", code2 == 403,
          "code=%s" % code2)
    code2, _ = req("POST", "/api/v1/terminals/%s/heartbeat" % tid2,
                   {}, headers={"X-ETP-Token": TOKEN})
    check("unregistered+unwhitelisted heartbeat 403", code2 == 403,
          "code=%s" % code2)
    code2, _ = req("POST", "/api/v1/console/whitelist",
                   {"cidr": "0.0.0.0/0", "note": "smoke-temp"},
                   headers=ch)
    check("whitelist add 0.0.0.0/0", code2 == 200, "code=%s" % code2)
    code2, j2 = req("POST", "/api/v1/terminals/register", {
        "terminal_id": tid2, "terminal_type": "windows", "hostname": "ADMIT",
        "os_info": "smoke", "client_version": "smoke-1.0"},
        headers={"X-ETP-Token": TOKEN})
    check("whitelisted register ok", code2 == 200 and j2.get("ok") is True,
          "code=%s" % code2)
    # 恢复白名单原状：删临时全开条目 + 重建 initial
    code2, j2 = req("GET", "/api/v1/console/whitelist", headers=ch)
    for w in j2.get("whitelist", []):
        if w["cidr"] in ("0.0.0.0/0", "::/0") and w["id"] not in removed:
            req("DELETE", "/api/v1/console/whitelist/%d" % w["id"], headers=ch)
    for cidr, note, enabled in initial:
        req("POST", "/api/v1/console/whitelist", {"cidr": cidr, "note": note},
            headers=ch)
    print("  (whitelist restored to %d entries)" % len(initial))

    # 8. 敏感配置加密存储与脱敏（功能1）
    print("[8] settings encryption")
    import base64 as _b64
    import secrets as _secrets
    import tempfile as _tf
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "server"))
    from secretsbox import SecretsBox as _Box
    _tmp = _tf.mkdtemp()
    _box = _Box(os.path.join(_tmp, "machine.key"))
    _plain = "sk-" + _secrets.token_hex(12)
    _token = _box.encrypt(_plain)
    check("secretsbox roundtrip", _box.decrypt(_token) == _plain)
    _bad = _token[:-6] + ("AAAAAA" if not _token.endswith("AAAAAA") else "BBBBBB")
    try:
        _box.decrypt(_bad)
        _tamper_ok = False
    except ValueError:
        _tamper_ok = True
    check("secretsbox tamper rejected", _tamper_ok)

    code2, j2 = req("POST", "/api/v1/console/settings",
                    {"settings": {"llm.api_key": _plain}}, headers=ch)
    check("settings write llm.api_key", code2 == 200, "code=%s" % code2)
    code2, body = req("GET", "/api/v1/console/settings", headers=ch, raw=True)
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    check("api key not echoed in plaintext", _plain not in text)
    check("api key masked in list", ("tes****" in text) or ("sk-****" in text)
          or ("****" in text), text[-200:])
    code2, _ = req("POST", "/api/v1/console/settings",
                   {"settings": {"llm.api_key": ""}}, headers=ch)
    check("settings clear llm.api_key", code2 == 200)

    # 9. 上传登记（功能1）
    print("[9] upload registration")
    code2, j2 = req("POST", "/api/v1/terminals/%s/uploads" % tid, {
        "files": [{"filename": "smoke_syslog.txt", "size": 1234,
                   "sha256": "ab" * 32, "ts": ts}]},
        headers={"X-ETP-Token": TOKEN})
    check("terminal upload registration accepted",
          code2 == 200 and j2.get("registered") == 1, str(j2))
    code2, j2 = req("GET", "/api/v1/console/uploads?limit=10", headers=ch)
    names = [u["filename"] for u in j2.get("uploads", [])]
    check("upload listed in console", "smoke_syslog.txt" in names, str(names)[:120])
    code2, j2 = req("GET", "/api/v1/console/storage/status", headers=ch)
    check("storage status ok", code2 == 200 and "mount" in j2 and "ftp" in j2,
          str(j2)[:120])
    code2, j2 = req("POST", "/api/v1/console/storage/scan", {}, headers=ch)
    check("storage scan ok", code2 == 200 and j2.get("ok") is True, str(j2)[:120])

    _summary()


def _summary():'''
assert anchor in src, "smoke anchor missing"
src = src.replace(anchor, insert, 1)
with io.open(SMOKE, "w", encoding="utf-8") as fh:
    fh.write(src)
print("smoke.py patched")

# ---------- e2e_console.py ----------

with io.open(E2E, "r", encoding="utf-8") as fh:
    src = fh.read()

anchor = '        # 9. 页面 JS 零错误\n        check("no page errors", len(errors) == 0, "; ".join(errors[:3]))'
insert = '''        # 8.5 配置清单页（功能1）
        page.locator('.tab[data-page="config"]').click()
        page.wait_for_selector("#wlCidr", timeout=5000)
        check("config page rendered", page.is_visible("#wlCidr")
              and page.is_visible("#llmUrl"))
        check("whitelist table present", page.locator("#wlBody").count() == 1)
        page.fill("#wlCidr", "192.0.2.0/24")
        page.fill("#wlNote", "e2e-temp")
        page.click("text=添加")
        page.wait_for_selector("#wlBody tr td:has-text('192.0.2.0/24')",
                               timeout=5000)
        check("whitelist entry added via UI",
              page.locator("#wlBody tr td:has-text('192.0.2.0/24')").count() >= 1)
        page.locator("#wlBody tr td:has-text('192.0.2.0/24')")
            .xpath("following-sibling::td[3]//button[contains(., '删除')]").first.click()
        page.wait_for_timeout(600)
        check("whitelist entry deleted via UI",
              page.locator("#wlBody tr td:has-text('192.0.2.0/24')").count() == 0)
        # 回监控页避免影响后续断言
        page.locator('.tab[data-page="monitor"]').click()
        page.wait_for_timeout(400)

        # 9. 页面 JS 零错误
        check("no page errors", len(errors) == 0, "; ".join(errors[:3]))'''
assert anchor in src, "e2e anchor missing"
src = src.replace(anchor, insert, 1)
with io.open(E2E, "w", encoding="utf-8") as fh:
    fh.write(src)
print("e2e_console.py patched")
