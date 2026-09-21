# -*- coding: utf-8 -*-
"""WoL 跨网段优化部署 + M720t 08:30 注册 + timer 停用（凭据注入，用后即删）。

执行方式（id=7 模式，main 会话后台注入）：
    set TP_SSH_HOST=172.17.5.215
    set TP_SSH_PORT=21232
    set TP_SSH_USER=root
    set TP_SSH_PWD=<口令>
    python _tp_wol_optimize.py

动作清单：
  [1] 备份远端 server/wol.py（时间戳目录）
  [2] SFTP 上传本地 server/wol.py → 远端 app/server/wol.py（md5 双侧对拍）
  [3] 重启 terminal-platform → systemctl is-active + /api/v1/health 轮询
  [4] journal 确认 wol scheduler on (tick 20s) + 远端 py_compile
  [5] 注册 M720t（WIN-13F-xx-3）每日 08:30 唤醒计划（服务器侧
      power_control.wol_schedule_create 直调，幂等：同 tid+time 跳过）
  [6] 停用 wol-win13f.timer + service（跨网段直发已被对照实验证明无效；
      unit 文件保留留档），复查 timer-list
  [7] 计划台账 + audit 尾查汇总
"""
import hashlib
import os
import sys
import time

import paramiko

HOST = os.environ["TP_SSH_HOST"]
PORT = int(os.environ.get("TP_SSH_PORT", "21232"))
USER = os.environ["TP_SSH_USER"]
PWD = os.environ["TP_SSH_PWD"]

ROOT = os.path.dirname(os.path.abspath(__file__))
LOCAL_WOL = os.path.join(ROOT, "server", "wol.py")
APP = "/data/terminal-platform/app"
BK = APP + "/../backups/pre_wolopt_%s" % time.strftime("%Y%m%d_%H%M%S")
VENV_PY = "/data/terminal-platform/venv/bin/python"
DB = "/data/terminal-platform/data/eyeterm.db"

SCHED_TID = "WIN-13F-xx-3"
SCHED_TIME = "07:30"
SCHED_NAME = "每日自动开机"

SEED_PY = """# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "%(app)s/server")
import power_control
pc = power_control.get_pc("%(db)s")
hit = [s for s in pc.wol_schedule_list()
       if s["terminal_id"] == "%(tid)s" and s["time_hhmm"] == "%(time)s"]
if hit:
    print("EXISTS id=%%s" %% hit[0]["id"])
else:
    sid = pc.wol_schedule_create("%(tid)s", "", "%(time)s",
                                 name="%(name)s", method="auto",
                                 operator="wol-opt")
    print("CREATED id=%%s" %% sid)
""" % {"app": APP, "db": DB, "tid": SCHED_TID, "time": SCHED_TIME,
       "name": SCHED_NAME}

_buf = []


def out(s=""):
    print(s, flush=True)
    _buf.append(str(s))


def run(ssh, desc, cmd, timeout=90, must_ok=False):
    out("$ %s\n  > %s" % (desc, cmd))
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    rc = stdout.channel.recv_exit_status()
    o = stdout.read().decode("utf-8", "replace").strip()
    e = stderr.read().decode("utf-8", "replace").strip()
    if o:
        out("  " + "\n  ".join(o.splitlines()[:24]))
    if e:
        out("  [stderr] " + e[:400])
    if must_ok and rc != 0:
        out("FATAL: %s rc=%d" % (desc, rc))
        ssh.close()
        sys.exit(1)
    return rc, o, e


def main():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER, password=PWD, timeout=20)
    out("== connected %s:%s ==" % (HOST, PORT))

    # [1] 备份
    run(cli, "备份远端 wol.py",
        "mkdir -p %s && cp -a %s/server/wol.py %s/wol.py" % (BK, APP, BK),
        must_ok=True)
    out("backup dir: " + BK)

    # [2] 上传 wol.py（md5 双侧对拍）
    with open(LOCAL_WOL, "rb") as fh:
        blob = fh.read()
    md5_local = hashlib.md5(blob).hexdigest()
    sftp = cli.open_sftp()
    sftp.put(LOCAL_WOL, APP + "/server/wol.py")
    sftp.close()
    _rc, o, _e = run(cli, "md5 对拍", "md5sum %s/server/wol.py" % APP)
    out("md5 local : " + md5_local)
    if md5_local not in o:
        out("FATAL: md5 mismatch after upload")
        cli.close()
        sys.exit(1)

    # [3] 重启 + 健康检查
    run(cli, "重启服务",
        "systemctl restart terminal-platform && sleep 2 && "
        "systemctl is-active terminal-platform", must_ok=True)
    ok = False
    for _ in range(15):
        _rc, o, _e = run(cli, "health 轮询",
                         "curl -s -m 4 http://127.0.0.1:18090/api/v1/health")
        if o.startswith("{"):
            ok = True
            break
        time.sleep(1)
    if not ok:
        out("FATAL: health not ready")
        cli.close()
        sys.exit(1)

    # [4] journal + 远端语法
    run(cli, "journal wol 调度器",
        "journalctl -u terminal-platform -n 40 --no-pager | grep -i wol"
        " | tail -4")
    run(cli, "远端 py_compile",
        "%s -m py_compile %s/server/wol.py && echo COMPILE_OK" % (VENV_PY, APP),
        must_ok=True)

    # [5] 注册 M720t 08:30 计划（幂等）
    sftp = cli.open_sftp()
    with sftp.open("/tmp/_wol_seed.py", "w") as fh:
        fh.write(SEED_PY)
    sftp.close()
    run(cli, "注册 08:30 计划",
        "%s /tmp/_wol_seed.py && rm -f /tmp/_wol_seed.py" % VENV_PY,
        must_ok=True)
    run(cli, "计划台账复查",
        "sqlite3 %s \"SELECT id, terminal_id, time_hhmm, method, enabled,"
        " run_state FROM wol_schedules;\"" % DB)

    # [6] 停用 systemd timer（跨网段无效，防误导；unit 留档）
    run(cli, "停用 wol-win13f.timer",
        "systemctl disable --now wol-win13f.timer 2>&1; "
        "systemctl list-timers --no-pager | grep -i wol || echo NO-WOL-TIMER")
    run(cli, "timer enabled 复查",
        "systemctl is-enabled wol-win13f.timer 2>&1 || true")

    # [7] 汇总
    run(cli, "audit 尾查",
        "sqlite3 %s \"SELECT ts, action, target FROM audit_log"
        " ORDER BY id DESC LIMIT 3;\"" % DB)
    out("== WOL-OPT DONE ==")

    cli.close()
    with open(os.path.join(ROOT, "_tp_wolopt_out.txt"), "w",
              encoding="utf-8") as fh:
        fh.write("\n".join(_buf))


if __name__ == "__main__":
    main()
