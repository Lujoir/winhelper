# -*- coding: utf-8 -*-
"""画方准入联通性验证：服务端发起 HMAC-SHA256 签名请求（凭据经环境变量，不落盘）。"""
import os

import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ["ETP_SSH_HOST"], port=int(os.environ["ETP_SSH_PORT"]),
            username=os.environ["ETP_SSH_USER"],
            password=os.environ["ETP_SSH_PASS"], timeout=20)

code = r'''
import hashlib, hmac, json, os, time, random, ssl, urllib.request

HOST = "https://172.17.254.250:9002"
APPKEY = os.environ["NAD_APPKEY"]
APPSECRET = os.environ["NAD_APPSECRET"]

def call(path, data=None):
    t = int(time.time())
    nonce = random.randint(10**9, 10**10 - 1)
    s = "appkey=%s&nonce=%d&time=%d" % (APPKEY, nonce, t)
    sign = hmac.new(APPSECRET.encode(), s.encode(), hashlib.sha256).hexdigest()
    body = {"appkey": APPKEY, "sign": sign, "time": t, "nonce": nonce,
            "enctype": 0, "version": "v2.0"}
    if data is not None:
        body["data"] = data
    req = urllib.request.Request(
        HOST + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json;charset=utf-8"},
        method="POST")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return -1, {"error": str(e)[:200]}

# 1. 终端字典（最轻接口，验证签名与联通）
rc, resp = call("/httpapi/term/dict")
print("term/dict:", rc, json.dumps(resp, ensure_ascii=False)[:400])

# 2. 终端列表（IP 冲突校验的核心数据源）
rc, resp = call("/httpapi/term/get", {"where": {}, "curpage": 1, "limit": 3})
print("term/get:", rc, json.dumps(resp, ensure_ascii=False)[:800])
'''
sftp = ssh.open_sftp()
with sftp.open("/tmp/_nadtest.py", "w") as fh:
    fh.write(code)
sftp.close()
cmd = ("NAD_APPKEY='026400065b0866fe0a561a54de8f' "
       "NAD_APPSECRET='e3762336d12d3c8d' "
       "python3 /tmp/_nadtest.py; rm -f /tmp/_nadtest.py")
_, out, err = ssh.exec_command(cmd, timeout=60)
print(out.read().decode("utf-8", "replace"))
e = err.read().decode("utf-8", "replace").strip()
if e:
    print("[stderr]", e[:300])
ssh.close()
