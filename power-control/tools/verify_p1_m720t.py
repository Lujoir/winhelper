# -*- coding: utf-8 -*-
"""power-control · P1 真机验证脚本（ThinkCentre M720t，用户配合执行）。

分步执行、每步打印回读并等待人工确认；全程只改「定时开机/定时关机」目标项，
验证后还原。** shutdown 仅作为计划任务动作参数生成（/t 90 且创建即停用），
脚本自身绝不执行 shutdown。**（ADR-007/009；派单验证流程）

用法（管理员 PowerShell 或按提示 UAC）：
  python tools/verify_p1_m720t.py read          # ① 读原值（Wake Up on Alarm/Alarm Time）
  python tools/verify_p1_m720t.py bios-write    # ② 写入 07:30（Daily）→ 回读校验
  python tools/verify_p1_m720t.py bios-restore  # ③ 还原为 ① 快照 → 回读校验
  python tools/verify_p1_m720t.py schtasks      # ④ P1b：创建(/t 90,停用)→查询→删除
  python tools/verify_p1_m720t.py full          # ①→④ 连续执行（每步确认）
"""
import ctypes
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import power_control as pc  # noqa: E402

_TEST_TIME = "07:30"


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_elevated():
    """非管理员 → UAC 拉起自身继续（exe 态拉自身 exe，python 态拉 python+脚本）。"""
    params = " ".join('"%s"' % a for a in sys.argv[1:])
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1)   # SW_SHOWNORMAL
    return int(ret) > 32


def _pause(prompt):
    try:
        ans = input("%s [y/N] " % prompt).strip().lower()
    except EOFError:
        return False
    return ans == "y"


def _print_rtc(items):
    for k in sorted(items):
        print("    %-28s = %s" % (k, items.get(k)))


def step_read():
    print("== ① 读原值快照 ==")
    items = pc.read_rtc_map()
    _print_rtc(items)
    backup = pc.save_bios_backup(items, machine=pc.collect_machine())
    print("  -> 原值已本地存档: %s（%d 项，%s）"
          % (pc._bios_backup_path(), len(items), backup["saved_at"]))
    try:
        pc.PlatformReporter().report_snapshot(pc.collect_snapshot())
        print("  -> 平台双存档: 已上报")
    except Exception as e:
        print("  -> 平台双存档: 未上报（%s；不影响还原）" % str(e)[:80])
    return items


def step_bios_write():
    print("== ② 测试写入（Daily %s；两轮值格式尝试）==" % _TEST_TIME)
    targets, summary = pc.build_bios_targets({"mode": "daily",
                                              "time": _TEST_TIME})
    print("  目标项:")
    for i, v in targets:
        print("    %-28s = %s" % (i, v))
    if not _pause("  确认写入？"):
        print("  已跳过")
        return False
    res = pc.bios_apply_targets(targets)
    print("  回读:")
    _print_rtc(res.get("readback") or {})
    print("  尝试明细: %s" % json.dumps(res.get("attempts"), ensure_ascii=False))
    if res.get("ok"):
        print("  -> 校验通过（读回=目标）")
        print("  !! 提醒：测试值 %s 已生效，请继续执行 bios-restore 还原初始值"
              "（full 模式下一步即为还原）" % _TEST_TIME)
        return True
    print("  -> 校验失败: %s" % json.dumps(res.get("failed"), ensure_ascii=False))
    return False


def step_weekly_probe():
    """③b 问题③实测：写 Weekly Event + 逐日开关 → 回读判定「逐日项在
    Weekly 模式下是否可写」→ 结论决定 UI 自定义星期是否 BIOS 层可行。"""
    print("== ③b Weekly 写入探测（结论回填 ADR-006）==")
    items = pc.read_rtc_map()
    print("  写入前 Alarm Day of Week: %s" % items.get("Alarm Day of Week"))
    targets = [("Wake Up on Alarm", "Weekly Event"),
               ("Monday", "Enabled")]
    for day in ("Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday"):
        targets.append((day, "Disabled"))
    print("  目标: Wake Up on Alarm=Weekly Event; Monday=Enabled; 其余日=Disabled")
    if not _pause("  确认写入（写前自动快照，结束后建议执行 bios-restore）？"):
        print("  已跳过")
        return False
    bak = pc.save_bios_backup(items, machine=pc.collect_machine())
    print("  写前快照: %s（%d 项）" % (bak["saved_at"], len(items)))
    res = pc.bios_apply_targets(targets)
    rb = res.get("readback") or {}
    print("  回读:")
    for k in ("Wake Up on Alarm", "Monday", "Tuesday", "Sunday",
              "Alarm Day of Week"):
        print("    %-28s = %s" % (k, rb.get(k)))
    print("  尝试明细: %s" % json.dumps(res.get("attempts"), ensure_ascii=False))
    if res.get("ok"):
        print("  -> 结论 A：Weekly Event 与逐日开关**写入生效** →")
        print("     「自定义一周哪几天」BIOS 层可行，UI 保留周一~五多选直写。")
        print("     注意观察上方 Alarm Day of Week 是否随 Monday=Enabled 变化。")
        return True
    print("  -> 结论 B：写入未生效（%s）→" %
          json.dumps(res.get("failed"), ensure_ascii=False)[:160])
    print("     「自定义一周哪几天」不可行，UI 需降级为「每天+软件回关」。")
    return False


def step_bios_restore():
    print("== ③ 一键还原为 ① 快照 ==")
    bak = pc.load_bios_backup()
    if not bak or not bak.get("items"):
        print("  -> 无本地备份，先执行 read")
        return False
    targets = list(bak["items"].items())
    print("  还原目标:")
    for i, v in targets:
        print("    %-28s = %s" % (i, v))
    if not _pause("  确认还原？"):
        print("  已跳过")
        return False
    res = pc.bios_apply_targets(targets)
    _print_rtc(res.get("readback") or {})
    if res.get("ok"):
        print("  -> 还原校验通过（与初始快照一致）")
        return True
    print("  -> 校验失败: %s" % json.dumps(res.get("failed"), ensure_ascii=False))
    return False


def step_schtasks():
    print("== ④ P1b 定时关机任务（创建 /t 90 且创建即停用 → 查询 → 删除）==")
    xml = pc.build_shutdown_task_xml("daily", "23:59", enabled=False,
                                     args=pc.PC_SHUTDOWN_ARGS_VERIFY)
    xml_file = os.path.join(pc.data_dir(), "_verify_task.xml")
    with open(xml_file, "w", encoding="utf-16") as f:
        f.write(xml)
    print("  动作: %s %s（Enabled=false，仅登记不生效）"
          % (pc.PC_SHUTDOWN_ACTION, pc.PC_SHUTDOWN_ARGS_VERIFY))
    if not _pause("  确认创建任务（需管理员）？"):
        print("  已跳过")
        return True
    r = subprocess.run(["schtasks", "/Create", "/F", "/TN",
                        pc.PC_SHUTDOWN_TASK, "/XML", xml_file],
                       capture_output=True, timeout=60,
                       creationflags=pc.CREATE_NO_WINDOW)
    out = pc._decode(r.stdout) + pc._decode(r.stderr)
    print("  创建 rc=%s %s" % (r.returncode, out.strip()[:120]))
    ok_create = r.returncode == 0
    q = subprocess.run(["schtasks", "/Query", "/TN", pc.PC_SHUTDOWN_TASK],
                       capture_output=True, timeout=60,
                       creationflags=pc.CREATE_NO_WINDOW)
    print("  查询 rc=%s（存在性验证）" % q.returncode)
    d = subprocess.run(["schtasks", "/Delete", "/TN", pc.PC_SHUTDOWN_TASK,
                        "/F"], capture_output=True, timeout=60,
                       creationflags=pc.CREATE_NO_WINDOW)
    print("  删除 rc=%s %s" % (d.returncode,
                               pc._decode(d.stdout).strip()[:120]))
    try:
        os.remove(xml_file)
    except OSError:
        pass
    return ok_create and q.returncode == 0 and d.returncode == 0


def main():
    steps = {"read": [step_read],
             "bios-write": [step_bios_write],
             "bios-restore": [step_bios_restore],
             "weekly": [step_weekly_probe, step_bios_restore],
             "schtasks": [step_schtasks],
             "full": [step_read, step_bios_write, step_bios_restore,
                      step_schtasks]}
    which = sys.argv[1] if len(sys.argv) > 1 else ""
    if which not in steps:
        print(__doc__)
        return 1
    if not _is_admin():
        # BIOS 写与 schtasks 需管理员；UAC 拉起自身继续（新控制台窗口）
        if _relaunch_elevated():
            print("已请求管理员权限，请在弹出的新窗口中继续。本窗口可关闭。")
            return 0
        print("管理员权限请求被取消或失败：请右键本程序选择「以管理员身份运行」"
              "后重试（读取可免提权，但写入/计划任务步骤必须提权）。")
        return 1
    print("真机验证（M720t）· 每步需人工确认；Ctrl+C 随时中止\n")
    n_fail = 0
    for fn in steps[which]:
        try:
            if fn() is False:
                n_fail += 1
        except KeyboardInterrupt:
            print("\n用户中止")
            return 130
        except Exception as e:
            print("  [异常] %s" % e)
            n_fail += 1
        print()
    print("== 验证完成（失败 %d 步）==" % n_fail)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
