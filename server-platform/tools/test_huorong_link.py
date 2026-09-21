#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ADR-033 增补单测：火绒 ↔ 平台终端关联引擎 + 分组覆盖 + 统一视图 + 路由。

覆盖（team-lead 需求变更验证门禁）：
- 匹配键三分支：MAC（归一/多网卡/同 MAC 取最新）→ IP（local_ip 优先/
  connect_ip 次之）→ 主机名（不区分大小写）
- 歧义回落：双侧任一键命中多候选即回落下一键，全歧义不关联
- manual 优先：manual 不被自动覆盖 / 指定抢占 auto / 解除写 ignore 不拉回
- 分组覆盖：同步幂等持久 / 解除回原生 / 悬空跳过
- 统一视图：三类 kind / 双视角组过滤 / 「其他」/ q 范围 / win7_eol
- 路由：assets 四端点契约 + assign 语义 + 鉴权

运行：python tools/test_huorong_link.py（临时库 + auth 临时库，零网络）
"""
import json
import os
import shutil
import sys
import tempfile
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..", "server")))
sys.path.insert(0, _HERE)

import api as api_mod                    # noqa: E402
import auth_upgrade as auth              # noqa: E402
from huorong import mirror_rows          # noqa: E402
from store import Store                  # noqa: E402
from test_huorong import BOOTSTRAP, login_token   # noqa: E402  复用鉴权夹具

PASSED, FAILED = [], []
NOW = 1726358400


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (" | " + str(detail)) if (detail and not cond)
                           else ""))


def hr_group(gid, name, parent=0):
    return {"group_id": gid, "parent_group": parent, "group_name": name}


def hr_client(cid, mac="", local_ip="", connect_ip="", computer_name="",
              group_id=1, is_online=1,
              os_version="Microsoft Windows 10 专业版", last_seen=NOW):
    return {"client_id": cid, "client_name": "终端-" + cid,
            "computer_name": computer_name, "local_ip": local_ip,
            "connect_ip": connect_ip, "mac": mac, "group_id": group_id,
            "is_online": is_online, "os_version": os_version,
            "version": "2.0.7.5", "last_connect_time": last_seen}


def reg_terminal(store, tid, hostname, ip, network_macs=(),
                 os_info="Windows 10 专业版", now=None):
    asset = {"schema": 1, "network": [{"name": "eth", "mac": m,
                                       "ipv4": [ip]} for m in network_macs]}
    store.register_terminal(terminal_id=tid, terminal_type="windows",
                            hostname=hostname, os_info=os_info,
                            client_version="4.0.0", ip=ip, hwinfo={},
                            asset=asset, now=NOW if now is None else now)


def map_pairs(store):
    return dict((r["hr_client_id"], (r["terminal_id"], r["match_type"]))
                for r in store.hr_map_list())


def hr_row(store, cid):
    return [c for c in store.hr_clients_page(page_size=200)["clients"]
            if c["client_id"] == cid][0]


def main():
    tmp = tempfile.mkdtemp(prefix="etp_huorong_link_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n=== %d passed / %d failed ===" % (len(PASSED), len(FAILED)))
    for f in FAILED:
        print("  FAILED: %s" % f)
    return 1 if FAILED else 0


def run(tmp):
    store = Store(os.path.join(tmp, "etp.db"), config_token="t0ken")
    groups = [hr_group(1, "防护组"), hr_group(2, "1F诊室", 1),
              hr_group(3, "未分组镜像组")]

    # ---- [1] 匹配键三分支 ----
    print("[1] 关联引擎：MAC/IP/主机名")
    reg_terminal(store, "WIN-A", "WIN-A", "172.17.8.11",
                 network_macs=["aa-bb-cc-dd-ee-01", "00-11-22-33-44-55"])
    reg_terminal(store, "WIN-B", "WIN-B", "172.17.8.12",
                 network_macs=["AA:BB:CC:DD:EE:02"])
    reg_terminal(store, "WIN-C", "pc-host-3", "172.17.8.13")
    reg_terminal(store, "WIN-D", "WIN-D", "10.9.9.9")
    reg_terminal(store, "WIN-AMBIG1", "AMB1", "10.9.9.1",
                 network_macs=["ff:ee:dd:cc:bb:aa"], now=NOW - 1000)
    reg_terminal(store, "WIN-AMBIG2", "AMB2", "10.9.9.2",
                 network_macs=["FF-EE-DD-CC-BB-AA"])
    clients = [
        hr_client("c1", mac="AA:BB:CC:DD:EE:01", local_ip="172.17.8.11",
                  group_id=2),
        # 同 MAC 双条（last_seen 新旧），取最新参与 MAC 键
        hr_client("c2a", mac="AA:BB:CC:DD:EE:02", local_ip="172.30.0.1",
                  last_seen=NOW - 500),
        hr_client("c2b", mac="AA:BB:CC:DD:EE:02", local_ip="172.17.8.12",
                  last_seen=NOW),
        # 无 MAC、IP 不命中 → 主机名回落（大小写不敏感）
        hr_client("c3", mac="", computer_name="PC-Host-3"),
        # MAC 歧义（两平台终端同 MAC）→ local_ip 唯一命中回落 IP
        hr_client("c4", mac="ff:ee:dd:cc:bb:aa", local_ip="10.9.9.9"),
        # 全不命中
        hr_client("c9", mac="99:88:77:66:55:44", local_ip="10.5.5.5"),
    ]
    store.hr_replace_snapshot(*mirror_rows(groups, clients, NOW))
    stats = store.hr_relink(now=NOW)
    pairs = map_pairs(store)
    check("MAC 归一命中（小写连字符 vs 大写冒号）",
          pairs.get("c1") == ("WIN-A", "mac"), pairs.get("c1"))
    check("同 MAC 取 last_seen 最新（c2b 胜出）",
          pairs.get("c2b") == ("WIN-B", "mac") and "c2a" not in pairs,
          (pairs.get("c2b"), pairs.get("c2a")))
    check("MAC 歧义回落 IP（c4→WIN-D）",
          pairs.get("c4") == ("WIN-D", "ip"), pairs.get("c4"))
    check("无 MAC 无 IP → 主机名回落", pairs.get("c3")
          == ("WIN-C", "hostname"), pairs.get("c3"))
    check("全不命中不关联", "c9" not in pairs)
    check("统计口径", stats["mac"] == 2 and stats["ip"] == 1
          and stats["hostname"] == 1 and stats["total"] == 4, stats)
    check("歧义双终端均未匹配",
          "WIN-AMBIG1" not in [v[0] for v in pairs.values()]
          and "WIN-AMBIG2" not in [v[0] for v in pairs.values()])

    # connect_ip 次选
    store2 = Store(os.path.join(tmp, "etp2.db"), config_token="t0ken")
    reg_terminal(store2, "WIN-X", "WIN-X", "10.1.1.1")
    store2.hr_replace_snapshot(*mirror_rows(
        [hr_group(1, "G1")],
        [hr_client("cx", mac="12:34:56:78:9a:bc", local_ip="10.2.2.2",
                   connect_ip="10.1.1.1")], NOW))
    store2.hr_relink(now=NOW)
    check("connect_ip 次选命中",
          map_pairs(store2).get("cx") == ("WIN-X", "ip"))

    # ---- [2] 幂等 ----
    print("[2] relink 幂等")
    s2 = store.hr_relink(now=NOW + 10)
    auto1 = dict((k, v) for k, v in pairs.items() if v[1] != "manual")
    auto2 = dict((k, v) for k, v in map_pairs(store).items()
                 if v[1] != "manual")
    check("两次重算结果一致", auto1 == auto2 and s2["total"] == stats["total"],
          (stats, s2))

    # ---- [3] manual 优先 / 抢占 / ignore ----
    print("[3] manual 优先与 ignore")
    reg_terminal(store, "WIN-E", "WIN-E", "172.17.8.14",
                 network_macs=["11:22:33:44:55:66"])
    store.hr_replace_snapshot(*mirror_rows(
        groups, clients + [hr_client("c6", mac="11:22:33:44:55:66",
                                     local_ip="172.17.8.14")], NOW))
    store.hr_relink(now=NOW)
    check("c6→WIN-E 自动关联", map_pairs(store).get("c6")
          == ("WIN-E", "mac"), map_pairs(store).get("c6"))
    r = store.hr_map_manual_set("c3", "WIN-E", now=NOW)
    check("manual 指定抢占 auto", r == "ok"
          and map_pairs(store).get("c3") == ("WIN-E", "manual"))
    store.hr_relink(now=NOW)
    check("manual 不被自动覆盖", map_pairs(store).get("c3")
          == ("WIN-E", "manual"))
    check("被抢占端 c6 释放", map_pairs(store).get("c6") is None)
    deleted = store.hr_map_delete("c3")
    check("解除返回被删映射", deleted
          and deleted["terminal_id"] == "WIN-E"
          and deleted["match_type"] == "manual", deleted)
    store.hr_ignore_add("c3", "WIN-E")
    store.hr_relink(now=NOW)
    # c3 主机名本就命中 WIN-C（hostname 回落），但绝不再拉回 WIN-E
    check("ignore 对不拉回（回落 WIN-C）", map_pairs(store).get("c3")
          == ("WIN-C", "hostname"), map_pairs(store).get("c3"))
    store.hr_map_manual_set("c1", "WIN-A", now=NOW)
    check("manual 撞 manual conflict",
          store.hr_map_manual_set("c9", "WIN-A", now=NOW) == "conflict")
    store.hr_ignore_add("c1", "WIN-A")
    store.hr_map_manual_set("c1", "WIN-A", now=NOW)   # 正向指定清除否决
    store.hr_relink(now=NOW)
    check("manual 优先持续生效", map_pairs(store).get("c1")
          == ("WIN-A", "manual"))

    # ---- [4] 分组覆盖 ----
    print("[4] 分组覆盖（assign-group 语义）")
    store.hr_override_set("c1", 1, now=NOW)
    store.hr_replace_snapshot(*mirror_rows(
        groups, clients + [hr_client("c6", mac="11:22:33:44:55:66",
                                     local_ip="172.17.8.14")], NOW))
    store.hr_apply_overrides()
    row = hr_row(store, "c1")
    check("覆盖生效（覆盖后 group_id=1）", row["group_id"] == 1
          and row["group_name"] == "防护组", row)
    check("覆盖后重同步仍持久（镜像重写+再应用）",
          store.hr_apply_overrides() >= 1
          and hr_row(store, "c1")["group_id"] == 1)
    store.hr_override_set("c9", 99, now=NOW)
    store.hr_apply_overrides()
    check("悬空覆盖跳过", hr_row(store, "c9")["group_id"] == 1)
    store.hr_override_set("c1", None, now=NOW)
    native = store.hr_override_reset_native("c1")
    check("解除覆盖回原生分组", native == 2
          and hr_row(store, "c1")["group_id"] == 2, native)

    # ---- [5] 统一视图 ----
    print("[5] 统一视图三类条目")
    items = store.hr_unified_items(group_source="huorong", hb_timeout=180,
                                   now=NOW)
    kinds = dict(((it["huorong"] or {}).get("client_id")
                  or (it["platform"] or {}).get("terminal_id"),
                  it["kind"]) for it in items)
    check("matched 条目（c1）", kinds.get("c1") == "matched")
    check("huorong_only 条目（c9）", kinds.get("c9") == "huorong_only")
    check("platform_only 条目（WIN-AMBIG1 歧义未匹配）",
          kinds.get("WIN-AMBIG1") == "platform_only")
    m = [it for it in items if it["kind"] == "matched"][0]
    check("matched 双侧字段合并", m["platform"]["terminal_id"] == "WIN-A"
          and m["huorong"]["client_id"] == "c1"
          and m["match_type"] == "manual"
          and "cpu_model" in m["platform"]["asset"])
    check("win7_eol 徽章可判定", all(
        isinstance(it["huorong"]["win7_eol"], bool)
        for it in items if it["huorong"]))
    check("platform_only 在线派生（last_seen 距 now）",
          [it for it in items
           if (it["platform"] or {}).get("terminal_id") == "WIN-AMBIG1"][0]
          ["platform"]["online"] is False)
    other_items = store.hr_unified_items(group_source="huorong",
                                         group_id="other", hb_timeout=180,
                                         now=NOW)
    check("「其他」只装 platform_only",
          other_items and all(it["kind"] == "platform_only"
                              for it in other_items))
    g2 = store.hr_unified_items(group_source="huorong", group_id="2",
                                hb_timeout=180, now=NOW)
    check("huorong 视角组过滤", g2 and all(
        it["huorong"] and it["huorong"]["group_id"] == 2 for it in g2))
    pv = store.hr_unified_items(group_source="platform", hb_timeout=180,
                                now=NOW)
    check("platform 视角含 matched+platform_only",
          any(it["kind"] == "matched" for it in pv)
          and any(it["kind"] == "platform_only" for it in pv))
    qm = store.hr_unified_items(q="AA:BB:CC:DD:EE:01", hb_timeout=180,
                                now=NOW)
    check("q 命中火绒 MAC", any(
        it["huorong"] and it["huorong"]["client_id"] == "c1" for it in qm))
    qh = store.hr_unified_items(q="pc-host", hb_timeout=180, now=NOW)
    check("q 命中平台 hostname", any(
        it["platform"] and it["platform"]["terminal_id"] == "WIN-C"
        for it in qh))

    # ---- [6] 路由 ----
    print("[6] assets 路由")
    auth.DB_PATH = os.path.join(tmp, "auth.db")
    auth._local = threading.local()
    conn = auth.get_conn()
    conn.executescript(BOOTSTRAP)
    conn.commit()
    auth.ensure_admin_role()
    try:
        auth.invalidate_policy_cache()
    except Exception:
        pass
    ctx = api_mod.ApiContext(store, {"terminal_token": "t0ken",
                                     "heartbeat_timeout_sec": 180})
    admin_token = login_token("admin", "Legacy@Plain2026")
    op_token = login_token("op", "OpPlain2026")
    check("测试登录就绪", bool(admin_token and op_token))

    def call(method, path, token=None, body=b"", query=None):
        headers = {"x-etp-console-token": token} if token else {}
        try:
            return api_mod.dispatch(ctx, method, path, query or {}, headers,
                                    body, "127.0.0.1", scope="console")
        except api_mod.ApiError as exc:
            return exc.status, b"", ""

    st, payload, _ = call("GET", "/api/v1/console/assets/groups", admin_token)
    gj = json.loads(payload.decode("utf-8"))
    check("groups 两段树契约", st == 200 and "platform_groups" in gj
          and "huorong_groups" in gj
          and set(gj["huorong_groups"][0].keys())
          == {"id", "name", "parent", "total", "online", "matched"}
          and set(gj["other"].keys())
          == {"platform_total", "platform_online"})

    st, payload, _ = call("GET", "/api/v1/console/assets/terminals",
                          admin_token,
                          query={"group_source": "huorong", "page": "1",
                                 "page_size": "20"})
    tj = json.loads(payload.decode("utf-8"))
    check("terminals 契约（items 四键）", st == 200 and tj["total"] > 0
          and set(tj["items"][0].keys())
          == {"kind", "match_type", "platform", "huorong"}, tj["total"])
    st, _, _ = call("GET", "/api/v1/console/assets/terminals", admin_token,
                    query={"group_source": "bad"})
    check("非法 group_source 400", st == 400)

    st, payload, _ = call("POST", "/api/v1/console/assets/assign-link",
                          admin_token,
                          body=json.dumps({"huorong_client_id": "c6",
                                           "terminal_id": "WIN-E"})
                          .encode("utf-8"))
    check("assign-link manual 200", st == 200
          and map_pairs(store).get("c6") == ("WIN-E", "manual"))
    st, _, _ = call("POST", "/api/v1/console/assets/assign-link",
                    admin_token,
                    body=json.dumps({"huorong_client_id": "c9",
                                     "terminal_id": "WIN-A"}).encode("utf-8"))
    check("manual 撞 manual 占用 409（WIN-A 归 c1）", st == 409)
    st, payload, _ = call("POST", "/api/v1/console/assets/assign-link",
                          admin_token,
                          body=json.dumps({"huorong_client_id": "c9",
                                           "terminal_id": "WIN-D"})
                          .encode("utf-8"))
    check("manual 抢占 auto 200", st == 200
          and map_pairs(store).get("c9") == ("WIN-D", "manual"))
    st, payload, _ = call("POST", "/api/v1/console/assets/assign-link",
                          admin_token,
                          body=json.dumps({"huorong_client_id": "c6",
                                           "terminal_id": None})
                          .encode("utf-8"))
    check("解除关联 200", st == 200 and map_pairs(store).get("c6") is None)
    store.hr_relink(now=NOW)
    check("解除后重算不拉回", map_pairs(store).get("c6") is None)
    st, _, _ = call("POST", "/api/v1/console/assets/assign-link",
                    admin_token,
                    body=json.dumps({"huorong_client_id": "nope",
                                     "terminal_id": None}).encode("utf-8"))
    check("未知火绒 client 400", st == 400)
    st, _, _ = call("POST", "/api/v1/console/assets/assign-link",
                    admin_token,
                    body=json.dumps({"huorong_client_id": "c9",
                                     "terminal_id": "WIN-NOPE"})
                    .encode("utf-8"))
    check("未知平台终端 400", st == 400)

    st, payload, _ = call("POST", "/api/v1/console/assets/assign-group",
                          admin_token,
                          body=json.dumps({"client_id": "c9",
                                           "group_id": 3}).encode("utf-8"))
    check("assign-group 200 生效", st == 200
          and hr_row(store, "c9")["group_id"] == 3)
    st, payload, _ = call("POST", "/api/v1/console/assets/assign-group",
                          admin_token,
                          body=json.dumps({"client_id": "c9",
                                           "group_id": 0}).encode("utf-8"))
    check("assign-group 解除回原生", st == 200
          and hr_row(store, "c9")["group_id"] == 1)
    st, _, _ = call("POST", "/api/v1/console/assets/assign-group",
                    admin_token,
                    body=json.dumps({"client_id": "c9",
                                     "group_id": 999}).encode("utf-8"))
    check("未知火绒组 400", st == 400)
    st, _, _ = call("POST", "/api/v1/console/assets/assign-group",
                    admin_token,
                    body=json.dumps({"client_id": "nope",
                                     "group_id": 1}).encode("utf-8"))
    check("未知 client 400", st == 400)
    st, _, _ = call("GET", "/api/v1/console/assets/groups", op_token)
    check("operator 只读放行", st == 200)
    st, _, _ = call("POST", "/api/v1/console/assets/assign-link", op_token,
                    body=json.dumps({"huorong_client_id": "c9",
                                     "terminal_id": None}).encode("utf-8"))
    check("operator assign 放行（资产日常操作）", st == 200)
    st, _, _ = call("GET", "/api/v1/console/assets/groups", None)
    check("未登录 401", st == 401)

    # ---- [7] 弹窗火绒块（context + 病毒统计镜像） ----
    print("[7] 第三方数据源信息·火绒块")
    from huorong import virus_row
    vr = virus_row({"client_id": "c1", "count": 3, "success": 2,
                    "fail": 1, "ignored": 0, "trusted": 0}, now=NOW)
    check("病毒行归一（count 键名宽容）", vr and vr["client_id"] == "c1"
          and vr["total"] == 3 and vr["success"] == 2 and vr["fail"] == 1, vr)
    check("病毒行空 client_id 拒绝",
          virus_row({"count": 1}) is None)
    store.hr_virus_replace_snapshot([vr], NOW)
    ctx_blk = store.hr_context_block("WIN-A", now=NOW)
    check("context linked 契约", ctx_blk.get("linked") is True
          and ctx_blk.get("match_type") == "manual"
          and ctx_blk["client"]["client_id"] == "c1"
          and ctx_blk["client"]["ip"] == "172.17.8.11", ctx_blk)
    check("组命名路径（父链反序）", ctx_blk.get("group_path")
          == "防护组/1F诊室", ctx_blk.get("group_path"))
    check("病毒统计并入", ctx_blk.get("virus")
          and ctx_blk["virus"]["total"] == 3
          and ctx_blk["virus"]["snapshot_ts"] == NOW, ctx_blk.get("virus"))
    check("无快照终端 virus=null", store.hr_context_block(
        "WIN-D", now=NOW).get("virus") is None)
    check("未关联 terminal → linked False",
          store.hr_context_block("WIN-NOPE", now=NOW) == {"linked": False})
    st, payload, _ = call("GET", "/api/v1/console/huorong/context",
                          admin_token,
                          query={"terminal_id": "WIN-A"})
    cj = json.loads(payload.decode("utf-8"))
    check("context 路由 200", st == 200 and cj["ok"] is True
          and cj["huorong"]["linked"] is True)
    st, _, _ = call("GET", "/api/v1/console/huorong/context", admin_token)
    check("context 缺参 400", st == 400)
    st, _, _ = call("GET", "/api/v1/console/huorong/context", None,
                    query={"terminal_id": "WIN-A"})
    check("context 未登录 401", st == 401)


if __name__ == "__main__":
    sys.exit(main())
