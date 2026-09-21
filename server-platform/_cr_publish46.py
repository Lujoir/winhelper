# -*- coding: utf-8 -*-
"""4.1.6 发布全流程（临时用后即删）：上传→set-current→对拍→消费观察→diag。
凭据经环境变量注入（CR_ADMIN_PWD），口令零落盘。"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("CR_TARGET_BASE",
                      "http://172.17.5.215:18090").rstrip("/")
ADMIN_PWD = os.environ["CR_ADMIN_PWD"]
TOKEN = os.environ.get("CR_TERMINAL_TOKEN", "")
TARGET = os.environ.get("CR_TARGET_TID", "WIN-13F-xx-3")

ART = (r"c:\Users\10604\CodeBuddy\20260522083146\installer\Output"
       r"\EyeTerm_Setup_x64_4.1.6_20260918_1623.exe")
ART_MD5 = "CA4FF329E5797606CD8D56C7F8AA9D26"
ART_SHA = ("24764BFC732BD5D10C6B4B0377E354D3EC16F78AA1F59754CD7144583"
           "3CD73D3")
ART_SIZE = 219209079
VERSION = "4.1.6"
FNAME = "EyeTerm_Setup_x64_4.1.6_20260918_1623.exe"
NOTE = ("4.1.6（单实例约束：命名互斥+置前秒退+--replace 接管"
        "+ 4.1.5 全部内容）")


def http(method, path, token=None, terminal=None, timeout=60, payload=None,
         raw_body=None, raw=False):
    h = {}
    if token:
        h["X-ETP-Console-Token"] = token
    if terminal:
        h["X-ETP-Token"] = terminal
    body = None
    if payload is not None:
        h["Content-Type"] = "application/json"
        body = json.dumps(payload).encode()
    elif raw_body is not None:
        body = raw_body
    req = urllib.request.Request(BASE + path, data=body, headers=h,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if raw:
                return resp.status, data, dict(resp.headers)
            try:
                return resp.status, json.loads(data.decode("utf-8")), {}
            except ValueError:
                return resp.status, data, {}
    except urllib.error.HTTPError as e:
        data = e.read()
        try:
            return e.code, json.loads(data.decode("utf-8")), {}
        except ValueError:
            return e.code, data, {}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def main():
    print("== [0] 产物本地预验 ==", flush=True)
    if not os.path.isfile(ART):
        print("ARTIFACT MISSING:", ART)
        return 1
    sz = os.path.getsize(ART)
    md5 = md5_file(ART)
    sha = sha256_file(ART)
    ok = (sz == ART_SIZE and md5.casefold() == ART_MD5.casefold()
          and sha.casefold() == ART_SHA.casefold())
    print("  size=%d/%d md5_ok=%s sha_ok=%s"
          % (sz, ART_SIZE, md5.casefold() == ART_MD5.casefold(),
             sha.casefold() == ART_SHA.casefold()), flush=True)
    if not ok:
        return 1

    st, r, _ = http("POST", "/api/v1/console/login",
                    payload={"username": "admin", "password": ADMIN_PWD})
    if st != 200 or not r.get("ok"):
        print("LOGIN FAIL", st)
        return 1
    admin = r["token"]
    print("== [1] login ok ==", flush=True)

    print("== [2] PUT upload（219MB，耐心） ==", flush=True)
    with open(ART, "rb") as fh:
        blob = fh.read()
    qnote = urllib.parse.quote(NOTE)
    st, r, _ = http(
        "PUT", "/api/v1/console/client/releases/" + VERSION
        + "?filename=" + FNAME + "&note=" + qnote,
        token=admin, raw_body=blob, timeout=1200)
    del blob
    print("  upload:", st, str(r)[:130], flush=True)
    if st != 200 or not r.get("ok"):
        return 1
    rid = r["release"]["id"]
    srv_sha = str(r["release"].get("sha256") or "")
    print("  release id=%s sha_casefold_match=%s"
          % (rid, srv_sha.casefold() == sha.casefold()), flush=True)

    print("== [3] set-current ==", flush=True)
    st, r, _ = http("POST",
                    "/api/v1/console/client/releases/%d/set-current" % rid,
                    token=admin)
    print("  set-current:", st, "version=%s"
          % (r.get("release") or {}).get("version"), flush=True)
    if st != 200:
        return 1

    print("== [4] manifest 验证 ==", flush=True)
    st, r, _ = http("GET", "/api/v1/client/manifest", terminal=TOKEN)
    m = r.get("manifest") or {}
    print("  latest=%s sha_ok=%s size=%s"
          % (m.get("latest_version"),
             str(m.get("sha256")).casefold() == sha.casefold(),
             m.get("size") == ART_SIZE), flush=True)
    if m.get("latest_version") != VERSION:
        return 1

    print("== [5] /download 独立对拍（全量回读，casefold 统一） ==",
          flush=True)
    st, body, hdrs = http("GET", "/download/client/setup", timeout=1200,
                          raw=True)
    dl_sha = hashlib.sha256(body).hexdigest()
    dl_md5 = hashlib.md5(body).hexdigest().upper()
    print("  bytes=%d sha_ok=%s md5_ok=%s"
          % (len(body), dl_sha.casefold() == sha.casefold(),
             dl_md5.casefold() == ART_MD5.casefold()), flush=True)
    if dl_sha.casefold() != sha.casefold():
        return 1

    print("== [6] 消费观察（15 分钟，全终端扫描+单实例锚点） ==", flush=True)
    deadline = time.time() + 900
    stable_needed = 2          # 目标机连续两拍 cv=目标版才视为回执稳定
    target_stable = 0
    seen = {}
    n = 0
    while time.time() < deadline:
        n += 1
        st, r, _ = http("GET", "/api/v1/console/terminals", token=admin)
        terms = r.get("terminals") or []
        # 单实例约束锚点：同一 tid 不得出现重复注册行
        ids = [str(t.get("terminal_id") or "") for t in terms]
        dups = sorted(set(i for i in ids if ids.count(i) > 1))
        if dups:
            print("  !! DUPLICATE tid REGISTERED: %s" % ",".join(dups),
                  flush=True)
        for t in terms:
            tid = str(t.get("terminal_id") or "")
            cv = t.get("client_version")
            online = t.get("online")
            prev = seen.get(tid)
            if prev != (online, cv):
                seen[tid] = (online, cv)
                print("  [%02d] %s tid=%s online=%s cv=%s"
                      % (n, time.strftime("%H:%M:%S"), tid, online, cv),
                      flush=True)
                if cv == VERSION:
                    print("  >> CONSUMED %s: %s (prev cv=%s)"
                          % (VERSION, tid, prev and prev[1]), flush=True)
            if tid == TARGET:
                if cv == VERSION:
                    target_stable += 1
                else:
                    target_stable = 0
        if target_stable >= stable_needed:
            print("UPGRADE OBSERVED (target %s stable x%d) at %s"
                  % (TARGET, target_stable, time.strftime("%H:%M:%S")),
                  flush=True)
            break
        time.sleep(30)
    else:
        print("OBSERVATION WINDOW EXHAUSTED（发布+对拍已完成，"
              "升级待终端上线/用户点击黄条）", flush=True)
        return 2

    print("== [7] diag（wmi_surface + uplink state + effective_interval） ==",
          flush=True)
    st, r, _ = http("POST", "/api/v1/console/terminals/"
                    + urllib.parse.quote(TARGET) + "/diag", token=admin)
    print("  diag launch:", st, str(r)[:120], flush=True)
    if st != 200:
        return 1
    diag_id = r["diag_id"]
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(20)
        st, r, _ = http("GET", "/api/v1/console/powercontrol/diags/"
                        + str(diag_id), token=admin)
        d = r.get("diag") or {}
        print("  diag #%s status=%s" % (diag_id, d.get("status")),
              flush=True)
        if d.get("status") in ("completed", "failed"):
            print("==== DIAG RESULT FULL JSON ====", flush=True)
            print(json.dumps(d.get("result"), ensure_ascii=False,
                             indent=1), flush=True)
            print("==== END ====", flush=True)
            return 0
    print("DIAG POLLING EXHAUSTED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
