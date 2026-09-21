# -*- coding: utf-8 -*-
"""资产定位管线门禁 v2（asset-mgmt-dev）。

覆盖：extract 杂乱文本样例 / 三源命中与未命中 / 聚合关联正确性 /
LLM 失败降级（not_ready/unavailable/ok=false/成功透传）/ handle 鉴权
（403/400/405）/ 数据源故障隔离（不 5xx）。

运行：python tools/test_asset_locate.py
外部依赖：无（Store 用 :memory:，nad/ai_analysis/auth 以 stub 注入）。"""

import json
import os
import sys
import time
import types
from types import SimpleNamespace

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(BASE, "server")
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)

import asset_locate
from store import Store

_PASS = [0]
_FAIL = [0]


def check(name, cond, detail=""):
    if cond:
        _PASS[0] += 1
    else:
        _FAIL[0] += 1
        print("  [FAIL] %s %s" % (name, detail))


def section(title):
    print("-- %s" % title)


# ----------------------------------------------------------------------
# auth stub（handle 内 import auth 命中 sys.modules 缓存）
# ----------------------------------------------------------------------
_auth = types.ModuleType("auth")
_auth.require_admin = lambda s: bool(s and s.get("role") == "admin")
_auth.audit = lambda *a, **k: None
_auth.Ev = types.SimpleNamespace(ACCESS_DENIED="denied")
sys.modules["auth"] = _auth

ADMIN = {"username": "a", "user_id": 1, "role": "admin"}
OPERATOR = {"username": "o", "user_id": 2, "role": "operator"}


def make_ctx(store):
    return SimpleNamespace(store=store, config={}, settings={})


def call_handle(ctx, text=None, sess=ADMIN, method="POST", raw_body=None):
    body = raw_body if raw_body is not None else (
        json.dumps({"text": text}).encode("utf-8")
        if text is not None else b"{}")
    return asset_locate.handle(ctx, method,
                               "/api/v1/console/home/asset-locate", {},
                               headers=None, body=body, sess=sess,
                               client_ip="127.0.0.1")


def parse(resp):
    status, payload, ctype = resp
    return status, json.loads(payload.decode("utf-8"))


# ----------------------------------------------------------------------
# nad 数据与 patch 工具
# ----------------------------------------------------------------------
NOW = int(time.time())
NAD_TERM = {
    "oid": "o1",
    "name": "赵吕骏的办公电脑",
    "ou": {"namepath": "总部-3楼-东区"},
    "ttype": "PC", "manfct": "Lenovo", "model": "M720t",
    "online": True, "block": False,
    "reginfo": {"stat": "1"},
    "owner": {"name": "赵吕骏", "uuid": "1036"},
    "onlts": NOW - 60,
    "macs": [{"mac": "AA:BB:CC:DD:EE:FF",
              "ips": [{"ip": "172.17.90.215"}],
              "macports": [{"nasoid": 1, "nasif": "Gi0/1",
                            "nasname": "SW-3F", "manip": "Gi0/1"}]}],
}


def patch_nad(terms, reason=None):
    import nad_client
    nad_client.nad_terminals_cached = lambda store: (terms, reason)


def unpatch_ai():
    sys.modules.pop("ai_analysis", None)


def patch_ai(fn=None, result=None, raises=None):
    mod = types.ModuleType("ai_analysis")
    if raises is not None:
        def fn2(*a, **k):
            raise raises
        mod.infer_asset_locate = fn2
    else:
        mod.infer_asset_locate = fn or (lambda *a, **k: result)
    sys.modules["ai_analysis"] = mod


def seed(store):
    """标准命中场景：platform T1 + 火绒 c1（关联 T1 + 登记）+ nad o1。"""
    store.register_terminal(
        "T1", "windows", "PC-01", "Windows 10", "4.1.5", "172.17.90.215",
        asset={"network": [{"mac": "AA:BB:CC:DD:EE:FF"}]}, now=NOW - 30)
    gid = store.asset_group_create("眼科")
    with store._lock:
        store._conn.execute(
            "UPDATE terminals SET group_id=? WHERE terminal_id=?", (gid, "T1"))
        store._conn.commit()
    store.hr_replace_snapshot(
        groups=[{"group_id": 1, "name": "眼科", "parent": None}],
        clients=[{"client_id": "c1", "name": "PC-01-火绒",
                  "computer_name": "PC-01", "ip": "172.17.90.215",
                  "connect_ip": "", "mac": "AA-BB-CC-DD-EE-FF",
                  "group_id": 1, "online": 1, "os": "Win10",
                  "version": "10.0", "last_seen": NOW - 120,
                  "first_seen": NOW - 86400, "last_off": NOW - 900,
                  "this_on": NOW - 800}])
    store.hr_assets_replace({"c1": {"楼层": "3", "具体位置": "门诊楼3F东区",
                                    "工号": "1036", "姓名": "张三",
                                    "使用科室": "眼科", "自定义字段": "x"}},
                            NOW)
    store.hr_map_manual_set("c1", "T1")
    patch_nad([NAD_TERM])


# ----------------------------------------------------------------------
# A. extract 杂乱文本样例
# ----------------------------------------------------------------------
section("A. extract 杂乱文本提取")


def ex(text):
    return asset_locate._extract(text)


def kinds(e):
    return [(i["type"], i["value"]) for i in e["identifiers"]]


r = ex("172.17.90.215")
check("A1 裸IP", kinds(r) == [("ip", "172.17.90.215")], kinds(r))
r = ex("172.17.90.215/24 掩码")
check("A2 IP带掩码", kinds(r) == [("ip", "172.17.90.215")], kinds(r))
r = ex("10.0.0.1:8080 端口尾巴")
check("A3 IP带端口", kinds(r) == [("ip", "10.0.0.1")], kinds(r))
r = ex("999.5.5.5 非法段")
check("A4 非法IP入ambiguous", r["identifiers"] == []
      and r["ambiguous"][0]["suspect_type"] == "ip", r)
r = ex("1.2.3.4.5 超段")
check("A5 超段IP入ambiguous", r["identifiers"] == []
      and r["ambiguous"][0]["suspect_type"] == "ip", r)
r = ex("AA:BB:CC:DD:EE:FF")
check("A6 冒号MAC", kinds(r) == [("mac", "aabbccddeeff")], kinds(r))
r = ex("aa-bb-cc-dd-ee-ff")
check("A7 连字符MAC", kinds(r) == [("mac", "aabbccddeeff")], kinds(r))
r = ex("aabb.ccdd.eeff")
check("A8 点分MAC", kinds(r) == [("mac", "aabbccddeeff")], kinds(r))
r = ex("aabbccddeeff")
check("A9 裸MAC", kinds(r) == [("mac", "aabbccddeeff")], kinds(r))
r = ex("123456789012")
check("A10 12位纯数字不认MAC", r["identifiers"] == []
      and r["ambiguous"][0]["suspect_type"] == "mac", r)
r = ex("工号 10362 找一下")
check("A11 工号", kinds(r) == [("empid", "10362")], kinds(r))
r = ex("WIN-13F-XX-3 这台机器")
check("A12 连字符主机名", kinds(r) == [("hostname", "WIN-13F-XX-3")],
      kinds(r))
r = ex("PC01")
check("A13 字母数字混合主机名", kinds(r) == [("hostname", "PC01")],
      kinds(r))
r = ex("help me please")
check("A14 纯字母词不算主机名", r["identifiers"] == [], r["identifiers"])
r = ex("172.17.90.215 是 AA-BB-CC-DD-EE-FF 的机器")
check("A15 IP+MAC复合", sorted(kinds(r)) ==
      [("ip", "172.17.90.215"), ("mac", "aabbccddeeff")], kinds(r))
r = ex("v4.1.5 版本")
check("A16 版本号不误报", r["identifiers"] == [], r["identifiers"])
r = ex("机房找 IP 172.17.90.215（MAC AA:BB:CC:DD:EE:FF），工号1036，"
       "机器名 DESKTOP-3FOP1，之前 10.0.0.256 是错的")
k = dict(kinds(r))
check("A17 杂乱复合提取", k.get("ip") == "172.17.90.215"
      and k.get("mac") == "aabbccddeeff" and k.get("empid") == "1036"
      and k.get("hostname") == "DESKTOP-3FOP1", k)
check("A17b 非法IP进ambiguous",
      any(a["value"] == "10.0.0.256" for a in r["ambiguous"]),
      r["ambiguous"])
r = ex("3F 12 2026")
check("A18 短数字/日期不误报", r["identifiers"] == [], r["identifiers"])
r = ex("192.168.1.1 与 192.168.1.1")
check("A19 重复去重", kinds(r) == [("ip", "192.168.1.1")], kinds(r))

# ----------------------------------------------------------------------
# B. 三源命中 + 聚合 + 融合视图
# ----------------------------------------------------------------------
section("B. 三源命中与聚合")
store = Store(":memory:")
seed(store)
ctx = make_ctx(store)
unpatch_ai()
# AI 环境隔离（sys.modules 置 None 强制 import 失败 → not_ready，
# 不受 server/ 下真 ai_analysis.py 是否落盘影响；须在 call_handle 前打桩）
sys.modules["ai_analysis"] = None
status, data = parse(call_handle(ctx, "172.17.90.215 或 AA-BB-CC-DD-EE-FF"))
check("B1 http200", status == 200)
check("B2 ok+not_found_false", data["ok"] is True
      and data["not_found"] is False)
srcs = data["search"]["sources"]
check("B3 三源全命中", srcs["platform"]["hits"] == 1
      and srcs["huorong"]["hits"] == 1 and srcs["nad"]["hits"] == 1, srcs)
check("B4 单簇high", len(data["matches"]) == 1
      and data["matches"][0]["score"] == "high", data["matches"])
m = data["matches"][0]
check("B5 mac主键", "mac" in m["keys"] and m["identity"]["mac"]
      == "aabbccddeeff", m["keys"])
check("B6 current_ip", m["identity"]["current_ip"] == "172.17.90.215")
check("B7 平台命中含组", m["platform"]["group"]["name"] == "眼科")
check("B8 火绒关联T1", m["huorong"]["linked_terminal_id"] == "T1"
      and m["huorong"]["link_match_type"] == "manual")
check("B9 登记信息透出", m["huorong"]["registration"]["楼层"] == "3")
p = data["asset_profile"]
check("B10 profile计算机名", p["computer_name"] == "PC-01")
check("B11 profile登记投影", p["registration"] ==
      {"楼层": "3", "具体位置": "门诊楼3F东区", "工号": "1036",
       "姓名": "张三", "使用科室": "眼科"}, p["registration"])
check("B12 准入信息", p["admission"]["terminal_name"] == "赵吕骏的办公电脑"
      and p["admission"]["owner"] == "赵吕骏"
      and p["admission"]["access_location"] == "SW-3F", p["admission"])
check("B13 IP交叉一致", p["cross_check"]["ip_consistent"] is True)
check("B14 时间线latest", p["timeline"]["latest"] is not None
      and p["timeline"]["latest"]["ts"] > 0)
check("B15 source_platform", p["source_platform"] == "platform+huorong+nad")
check("B16 AI未就绪降级", data["inference"]["available"] is False
      and data["inference"]["status"] == "not_ready", data["inference"])
check("B17 extract回显", data["extract"]["counts"]["ip"] == 1
      and data["extract"]["counts"]["mac"] == 1)

# ----------------------------------------------------------------------
# C. 未命中如实返回
# ----------------------------------------------------------------------
section("C. 未命中")
status, data = parse(call_handle(ctx, "10.200.200.200"))
check("C1 not_found", data["not_found"] is True
      and data["matches"] == [] and data["asset_profile"] is None)
check("C2 AI跳过", data["inference"]["status"] == "skipped_not_found")

# ----------------------------------------------------------------------
# D. 数据源故障隔离
# ----------------------------------------------------------------------
section("D. 数据源故障隔离")
store2 = Store(":memory:")
seed(store2)
ctx2 = make_ctx(store2)


def boom(*a, **k):
    raise RuntimeError("mirror down")


store2.hr_locate_clients = boom
patch_nad(None, reason="not_configured")
status, data = parse(call_handle(ctx2, "172.17.90.215"))
check("D1 恒200", status == 200 and data["ok"] is True)
check("D2 火绒error", data["search"]["sources"]["huorong"]["status"]
      .startswith("error:"), data["search"]["sources"])
check("D3 nad未配置", data["search"]["sources"]["nad"]["status"]
      == "not_configured")
check("D4 平台仍命中", data["search"]["sources"]["platform"]["hits"] == 1
      and len(data["matches"]) == 1)
patch_nad([NAD_TERM])

# ----------------------------------------------------------------------
# E/F. AI 推断编排：降级与成功
# ----------------------------------------------------------------------
section("E/F. AI 推断编排")
ctx3 = make_ctx(store)


def profile_of():
    _, d = parse(call_handle(ctx3, "AA:BB:CC:DD:EE:FF"))
    return d


d = profile_of()
check("E1 确定数据在", d["not_found"] is False
      and d["asset_profile"] is not None)

patch_ai(raises=RuntimeError("llm 503"))
d = profile_of()
check("E2 抛异常→unavailable", d["inference"]["available"] is False
      and d["inference"]["status"] == "unavailable"
      and "llm 503" in (d["inference"]["error"] or ""))
check("E2b 确定数据不受影响", d["asset_profile"] is not None
      and d["matches"])

patch_ai(result={"available": False, "error": "llm down"})
d = profile_of()
check("E3 ok=false→unavailable", d["inference"]["status"] == "unavailable"
      and d["inference"]["error"] == "llm down")

AI_BLOCKS = [{"title": "位置与使用人推测", "text": "推测在3楼眼科，使用人张三",
              "evidence": ["huorong.registration.楼层=3",
                           "nad.access_points[0].nasname=SW-3F"]}]
patch_ai(result={"available": True, "model": "Qwen3.6", "blocks": AI_BLOCKS})
d = profile_of()
check("F1 成功透传", d["inference"]["available"] is True
      and d["inference"]["status"] == "ok"
      and d["inference"]["model"] == "Qwen3.6")
check("F2 blocks形状", d["inference"]["blocks"] == AI_BLOCKS
      and d["inference"]["disclaimer"])
check("F3 确定与推断分离", d["asset_profile"]["registration"]["姓名"]
      == "张三" and "推测" in d["inference"]["blocks"][0]["text"])
unpatch_ai()

# ----------------------------------------------------------------------
# G/H. handle 鉴权与参数
# ----------------------------------------------------------------------
section("G/H. handle 鉴权与参数")
sctx = make_ctx(store)


def expect_err(fn, code):
    try:
        fn()
        return False, "no error raised"
    except Exception as exc:
        code_actual = getattr(exc, "code", None) or getattr(exc, "status",
                                                            None)
        return code_actual == code, repr(exc)


ok, detail = expect_err(lambda: call_handle(sctx, "1.2.3.4", sess=None),
                        403)
check("G1 未登录403", ok, detail)
ok, detail = expect_err(lambda: call_handle(sctx, "1.2.3.4",
                                            sess=OPERATOR), 403)
check("G2 operator403", ok, detail)
ok, detail = expect_err(lambda: call_handle(sctx, None, sess=ADMIN,
                                            raw_body=b"{}"), 400)
check("H1 缺text400", ok, detail)
ok, detail = expect_err(lambda: call_handle(sctx, "  \n ", sess=ADMIN), 400)
check("H2 空text400", ok, detail)
ok, detail = expect_err(lambda: call_handle(sctx, "1.2.3.4", sess=ADMIN,
                                            method="GET"), 404)
check("H3 GET404", ok, detail)
ok, detail = expect_err(lambda: call_handle(sctx, "x" * 2001, sess=ADMIN),
                        400)
check("H4 超长text400", ok, detail)

# ----------------------------------------------------------------------
# I. hostname 兜底检索 nad 登记名
# ----------------------------------------------------------------------
section("I. nad 登记名兜底")
ctxi = make_ctx(store)
status, data = parse(call_handle(ctxi, "赵吕骏的办公电脑"))
check("I1 登记名命中nad", data["search"]["sources"]["nad"]["hits"] == 1
      and len(data["matches"]) == 1
      and "hostname" in data["matches"][0]["keys"], data["matches"])
check("I2 hostname-only=low",
      data["matches"][0]["score"] == "low", data["matches"][0]["score"])
check("I3 not_found=false", data["not_found"] is False)

# ----------------------------------------------------------------------
# J. 多命中
# ----------------------------------------------------------------------
section("J. 多命中簇")
store4 = Store(":memory:")
seed(store4)
patch_nad([])   # J 场景测「同 IP 多火绒候选」歧义，须在 seed 后隔离 nad
store4.hr_replace_snapshot(
    groups=[{"group_id": 1, "name": "眼科", "parent": None}],
    clients=[{"client_id": "c1", "name": "A", "computer_name": "PC-01",
              "ip": "172.17.90.215", "connect_ip": "",
              "mac": "AA-BB-CC-DD-EE-FF", "group_id": 1, "online": 1,
              "os": "Win10", "version": "x", "last_seen": NOW - 120},
             {"client_id": "c2", "name": "B", "computer_name": "PC-99",
              "ip": "172.17.90.215", "connect_ip": "",
              "mac": "11-22-33-44-55-66", "group_id": 1, "online": 0,
              "os": "Win7", "version": "y", "last_seen": NOW - 3600}])
status, data = parse(call_handle(make_ctx(store4), "172.17.90.215"))
check("J1 双簇", len(data["matches"]) == 2, data["matches"])
check("J2 均medium/ip", all(m["score"] == "medium"
                            and "ip" in m["keys"]
                            for m in data["matches"]),
      json.dumps([{"score": m["score"], "keys": m["keys"]}
                  for m in data["matches"]], ensure_ascii=False))
check("J3 最佳簇=火绒在线者", data["asset_profile"]["computer_name"]
      == "PC-01", data["asset_profile"]["computer_name"])

# ----------------------------------------------------------------------
# K. 真链路降级冒烟（真 ai_analysis + 空 llm 配置 → LLM 不可用降级）
# ----------------------------------------------------------------------
section("K. 真链路降级冒烟")
sys.modules.pop("ai_analysis", None)     # 移除假模块 → import 真模块
kctx = make_ctx(store)                   # ctx.settings={} 无 llm 配置
_, d = parse(call_handle(kctx, "AA:BB:CC:DD:EE:FF"))
check("K1 真模块LLM不可用降级", d["inference"]["available"] is False
      and d["inference"]["status"] == "unavailable"
      and (d["inference"]["error"] or ""), d["inference"])
check("K2 确定数据照常", d["asset_profile"] is not None
      and d["asset_profile"]["registration"]["姓名"] == "张三")

# ----------------------------------------------------------------------
# L. 真实鉴权模块解析冒烟（防 mock 掩盖 ModuleNotFoundError 回归：
#    门禁全程注入 auth stub，曾漏过 handle 内裸 import auth —— 生产
#    环境无独立 auth 模块，本仓库真名是 auth_upgrade，api.py 同款别名）
# ----------------------------------------------------------------------
section("L. 真实鉴权模块解析")
import inspect
sys.modules.pop("auth", None)            # 移除 stub，恢复真实解析环境
import auth_upgrade as auth_real
check("L1 真实模块可解析且契约齐备",
      hasattr(auth_real, "require_admin")
      and hasattr(auth_real, "audit")
      and hasattr(auth_real, "Ev"))
check("L2 管线无裸 import auth",
      "import auth\n" not in inspect.getsource(asset_locate))
check("L3 handle使用auth_upgrade别名",
      "import auth_upgrade as auth"
      in inspect.getsource(asset_locate.handle))

# ----------------------------------------------------------------------
print("")
print("门禁结果: %d 通过 / %d 失败" % (_PASS[0], _FAIL[0]))
sys.exit(1 if _FAIL[0] else 0)
