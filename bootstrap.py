# -*- coding: utf-8 -*-
"""EyeTerm 安装包预置注册（三大改造①，2026-09-17）。

链路：安装包文件名携带配置（协议 main 定稿）：
    EyeTerm_Setup_x64_{ver}_{cfg64}.exe
    cfg64 = base64url( zlib( json({"s": server, "t": token}) ) )
Inno 安装器 [Code] 从 {srcexe} 提取 cfg64 → 原样写入 {app}\\config_bootstrap.json
（raw 透传，不解析——zlib/base64 解码统一在客户端完成，Inno Pascal 不做解压）；
desktop.py 启动最早处调用 apply_on_startup()：
    解码/校验成功 且 uplink_config 尚无中心地址 → 写入 uplink 配置（enabled=true），
    客户端直接进入注册流程（跳过手动配置页）；
    解析失败 / 字段不全 / 已有配置 → 静默返回，**完全回退现有手动流程，零行为变化**。

选型裁定（ADR-010）：文件方案（{app}\\config_bootstrap.json）而非 HKLM 注册表——
  卸载随目录清除无残留、明文可审计便于批量运维（管理员可直接替换该文件）、
  Inno 无需扩展注册表写入；token 明文可见性与既有 uplink_config.json 同级（接受）。
"""

import base64
import json
import os
import re
import sys
import zlib

_BOOTSTRAP_NAME = "config_bootstrap.json"
# 版本段不含下划线且惰性匹配 → cfg64 组贪婪吃满（base64url 含下划线）
_SETUP_NAME_RE = re.compile(
    r"^EyeTerm_Setup_x64_[0-9][0-9A-Za-z.\-]*?_([A-Za-z0-9_\-]+)\.exe$",
    re.I)


def app_dir():
    """安装目录定位：PyInstaller 态 = exe 同目录；python 态 = 本文件目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def extract_cfg64(filename):
    """安装包文件名 → cfg64 段（固定前缀后、.exe 之前）；不匹配返回 None。

    注意 cfg64（base64url）字符集含下划线，不能按末个下划线切分——
    与 installer Pascal 侧固定前缀切分语义一致。"""
    m = _SETUP_NAME_RE.match(os.path.basename(filename or ""))
    if not m:
        return None
    return m.group(1)


def decode_cfg(cfg64):
    """cfg64 → {"server","token"}；任何一步失败抛 ValueError（调用方静默回退）。"""
    if not cfg64:
        raise ValueError("cfg64 empty")
    b64 = str(cfg64).replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    try:
        raw = base64.b64decode(b64)
        obj = json.loads(zlib.decompress(raw).decode("utf-8"))
    except Exception as e:
        raise ValueError("cfg64 decode failed: %s" % e)
    if not isinstance(obj, dict):
        raise ValueError("cfg64 payload not an object")
    server = str(obj.get("s") or "").strip()
    token = str(obj.get("t") or "").strip()
    if not server.startswith(("http://", "https://")):
        raise ValueError("server url invalid")
    if not token:
        raise ValueError("token empty")
    return {"server": server, "token": token}


def read_bootstrap(exe_dir=None):
    """读 {app}\\config_bootstrap.json → {"server","token"} 或 None。

    文件两形态兼容：{"cfg64": "..."}（installer raw 透传，本端解码）或
    {"server": "..", "token": ".."}（运维手工预置明文）。"""
    base = exe_dir or app_dir()
    path = os.path.join(base, _BOOTSTRAP_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("cfg64"), str) and obj["cfg64"]:
        try:
            return decode_cfg(obj["cfg64"])
        except ValueError:
            return None
    server = str(obj.get("server") or "").strip()
    token = str(obj.get("token") or "").strip()
    if server.startswith(("http://", "https://")) and token:
        return {"server": server, "token": token}
    return None


def apply_on_startup():
    """desktop.py 启动最早处调用：预置配置落 uplink_config（仅当中心未配置时
    采用——避免升级重装覆盖用户既有正确配置）。返回 True=已采用。失败静默。"""
    try:
        import uplink
        conf = read_bootstrap()
        if not conf:
            return False
        cfg = uplink.load_config()
        if (cfg.get("server_url") or "").strip():
            return False   # 已有中心配置：不覆盖（升级重装场景保既有）
        cfg["server_url"] = conf["server"]
        cfg["token"] = conf["token"]
        cfg["enabled"] = True
        uplink.save_config(cfg)
        return True
    except Exception:
        return False
