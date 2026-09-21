# -*- coding: utf-8 -*-
"""P0 spike C：powercfg 备份还原实测（观枢终端平台｜EyeTerm · 桌面管控专项）。

验证点（对应 ADR-002）：
1. /getactivescheme 提取当前方案 GUID
2. /q 全量快照（子组/设置/AC/DC 索引 JSON 化）
3. 实测修改：显示器关闭超时（SUB_VIDEO/VIDEOIDLE）AC 通道 → 600 秒，
   /setactive 生效后 /q 断言
4. 从快照全量还原 → /q 再快照与初始快照全等 → PASS（可回滚保证）
5. 附加：/export 全方案 .pow 备份 + /import 还原链路验证（导入副本验证后删除）

全程非破坏：结束态 == 开始态。中文/英文系统输出双兼容。
用法：
  python tools/spike_powercfg_backup.py
输出：tools/spike_out/spikeC_report.txt + spikeC_snapshot.json
"""
import json
import os
import re
import subprocess
import sys

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike_out")
SUB_VIDEO_GUID = "7516b95f-f776-4464-8c53-06167f40cc99"
VIDEOIDLE_GUID = "3c0bc021-c8a8-4e07-a973-6b14cbcb2b7e"  # 实测确认（GUID 别名 VIDEOIDLE）
GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_report_lines = []


def log(msg):
    print(msg)
    _report_lines.append(msg)


def run_powercfg(*args):
    """执行 powercfg；输出按 gbk→utf-8→latin-1 三级兜底解码（中文系统 GBK）。"""
    proc = subprocess.run(["powercfg", *args], capture_output=True)
    out = proc.stdout or b""
    err = proc.stderr or b""
    text = None
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            text = out.decode(enc)
            errt = err.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = out.decode("utf-8", "replace")
        errt = err.decode("utf-8", "replace")
    return proc.returncode, text, errt


def get_active_scheme():
    rc, out, err = run_powercfg("/getactivescheme")
    if rc != 0:
        raise RuntimeError("getactivescheme rc=%d err=%s" % (rc, err.strip()))
    m = GUID_RE.search(out)
    if not m:
        raise RuntimeError("未能从输出解析 GUID: %r" % out)
    return m.group(0).lower(), out.strip()


def query_snapshot(guid):
    """/q 解析为 {subgroup: {setting: {"ac": int, "dc": int}}}，
    另返回 {subgroup/setting: 别名} 的 aliases 映射。
    兼容中文（当前交流电源设置索引）与英文（Current AC Power Setting Index）。"""
    rc, out, err = run_powercfg("/q", guid)
    if rc != 0:
        raise RuntimeError("query rc=%d err=%s" % (rc, err.strip()))
    snap = {}
    aliases = {}
    cur_sub = None
    cur_setting = None
    re_sub = re.compile(r"子组\s*GUID[:：]\s*([0-9a-fA-F-]{36})")
    re_sub_en = re.compile(r"Subgroup\s+GUID[:：]\s*([0-9a-fA-F-]{36})")
    re_set = re.compile(r"电源设置\s*GUID[:：]\s*([0-9a-fA-F-]{36})")
    re_set_en = re.compile(r"Power Setting\s+GUID[:：]\s*([0-9a-fA-F-]{36})")
    re_alias = re.compile(r"GUID\s*别名[:：]\s*(\S+)")
    re_alias_en = re.compile(r"GUID\s+Alias[:：]\s*(\S+)")
    re_ac = re.compile(r"(?:当前交流电源设置索引|Current AC Power Setting "
                       r"Index)[:：]\s*0x([0-9a-fA-F]+)")
    re_dc = re.compile(r"(?:当前直流电源设置索引|Current DC Power Setting "
                       r"Index)[:：]\s*0x([0-9a-fA-F]+)")
    for line in out.splitlines():
        line = line.strip()
        m = re_sub.search(line) or re_sub_en.search(line)
        if m:
            cur_sub = m.group(1).lower()
            snap.setdefault(cur_sub, {})
            continue
        m = re_set.search(line) or re_set_en.search(line)
        if m:
            cur_setting = m.group(1).lower()
            snap.setdefault(cur_sub, {})[cur_setting] = {"ac": None,
                                                         "dc": None}
            continue
        m = re_alias.search(line) or re_alias_en.search(line)
        if m:
            if cur_setting:
                aliases[cur_setting] = m.group(1).upper()
            elif cur_sub:
                aliases[cur_sub] = m.group(1).upper()
            continue
        m = re_ac.search(line)
        if m and cur_sub and cur_setting:
            snap[cur_sub][cur_setting]["ac"] = int(m.group(1), 16)
            continue
        m = re_dc.search(line)
        if m and cur_sub and cur_setting:
            snap[cur_sub][cur_setting]["dc"] = int(m.group(1), 16)
    return snap, aliases


def find_videoidle_entry(snap, aliases):
    """优先按 GUID 别名 VIDEOIDLE 定位（实测最稳），兜底标准 GUID。"""
    for sg, settings in snap.items():
        for st in settings:
            if aliases.get(st) == "VIDEOIDLE":
                return sg, st
    if SUB_VIDEO_GUID in snap and VIDEOIDLE_GUID in snap[SUB_VIDEO_GUID]:
        return SUB_VIDEO_GUID, VIDEOIDLE_GUID
    raise RuntimeError("快照中未找到 VIDEOIDLE（别名与标准 GUID 均未命中）")


def set_index(guid, sub, setting, value, channel):
    arg = "/setacvalueindex" if channel == "ac" else "/setdcvalueindex"
    rc, out, err = run_powercfg(arg, guid, sub, setting, str(value))
    if rc != 0:
        raise RuntimeError("%s rc=%d err=%s" % (arg, rc, err.strip()))


def set_active(guid):
    rc, out, err = run_powercfg("/setactive", guid)
    if rc != 0:
        raise RuntimeError("setactive rc=%d err=%s" % (rc, err.strip()))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    log("== spike C：powercfg 备份还原实测 ==")
    all_fails = []
    try:
        guid, scheme_text = get_active_scheme()
        log("[1] 当前方案: %s" % scheme_text.replace("\n", " "))
        snap0, aliases0 = query_snapshot(guid)
        n_settings = sum(len(v) for v in snap0.values())
        log("[2] 快照: %d 子组 / %d 设置项" % (len(snap0), n_settings))
        with open(os.path.join(OUT_DIR, "spikeC_snapshot.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"guid": guid, "snapshot": snap0, "aliases": aliases0},
                      f, indent=2, ensure_ascii=False)
        sub, setting = find_videoidle_entry(snap0, aliases0)
        old_ac = snap0[sub][setting]["ac"]
        # 测试值必须避开原值（否则修改无差异，断言假 PASS）
        target = 601 if old_ac != 601 else 602
        log("    目标设置: 关闭显示器超时 AC 原值=%s 秒 → 测试值=%s 秒"
            % (old_ac, target))

        # 3. 修改 → 生效 → 断言
        set_index(guid, sub, setting, target, "ac")
        set_active(guid)
        snap1, _ = query_snapshot(guid)
        got = snap1.get(sub, {}).get(setting, {}).get("ac")
        log("[3] 修改验证: AC=%d → 实测=%s %s"
            % (target, got, "PASS" if got == target else "FAIL"))
        if got != target:
            all_fails.append("modify")

        # 4. 还原 → 比对全等
        for sg, settings in snap0.items():
            for st, vals in settings.items():
                if vals["ac"] is not None:
                    set_index(guid, sg, st, vals["ac"], "ac")
                if vals["dc"] is not None:
                    set_index(guid, sg, st, vals["dc"], "dc")
        set_active(guid)
        snap2, _ = query_snapshot(guid)
        restored = (snap2 == snap0)
        log("[4] 快照还原比对: %s（还原后快照与初始快照全等）"
            % ("PASS" if restored else "FAIL"))
        if not restored:
            all_fails.append("restore")
            # 差异定位
            for sg in snap0:
                for st in snap0[sg]:
                    if snap0[sg][st] != snap2.get(sg, {}).get(st):
                        log("    差异: %s/%s %r→%r"
                            % (sg, st, snap0[sg][st], snap2.get(sg, {})
                               .get(st)))

        # 5. /export + /import 全方案级备份链路
        pow_path = os.path.abspath(os.path.join(OUT_DIR, "spikeC_scheme.pow"))
        rc, out, err = run_powercfg("/export", pow_path, guid)
        ok_export = rc == 0 and os.path.exists(pow_path) \
            and os.path.getsize(pow_path) > 0
        log("[5a] /export 全方案备份: %s (%s)"
            % ("PASS" if ok_export else "FAIL",
               "%.1f KB" % (os.path.getsize(pow_path) / 1024.0)
               if ok_export else err.strip()))
        if not ok_export:
            all_fails.append("export")
        else:
            rc, out, err = run_powercfg("/import", pow_path)
            m = GUID_RE.search((out or "") + " " + (err or ""))
            ok_import = rc == 0 and m
            log("[5b] /import 链路: %s（rc=%d, 新副本 GUID=%s, err=%s）"
                % ("PASS" if ok_import else "FAIL", rc,
                   m.group(0) if m else "未解析", err.strip()[:80]))
            if ok_import:
                dup = m.group(0).lower()
                rc, out, err = run_powercfg("/delete", dup)
                log("[5c] 导入副本清理: %s"
                    % ("PASS" if rc == 0 else "FAIL " + err.strip()))
                if rc != 0:
                    all_fails.append("dup_cleanup")
            else:
                all_fails.append("import")

    except RuntimeError as exc:
        log("[X] 异常: %s" % exc)
        all_fails.append("exception:%s" % exc)

    log("== spike C 结果: %s ==" % ("FAIL" if all_fails else "PASS"))
    with open(os.path.join(OUT_DIR, "spikeC_report.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(_report_lines) + "\n")
    return 1 if all_fails else 0


if __name__ == "__main__":
    sys.exit(main())
