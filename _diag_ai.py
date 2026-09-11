# -*- coding: utf-8 -*-
"""临时脚本: SSH 自助诊断服务端 AI 模块（路由/错误字段/模型配置/设置缓存）"""
import os
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.17.5.215", port=21232, username="root",
          password=os.environ["ETP_PW"], timeout=10)

cmds = [
    ("server 模块清单", "ls -la /data/terminal-platform/app/server/"),
    ("AI 路由定义", "grep -rn 'analyses\\|/ai/' /data/terminal-platform/app/server/*.py | grep -i 'route\\|path\\|def handle\\|api/v1' | head -20"),
    ("分析记录表结构", "sqlite3 /data/terminal-platform/data/eyeterm.db '.schema ai_analyses' 2>/dev/null || /data/terminal-platform/venv/bin/python -c \"import sqlite3; c=sqlite3.connect('/data/terminal-platform/data/eyeterm.db'); print([r for r in c.execute(\\\"SELECT name FROM sqlite_master WHERE type='table'\\\")])\""),
    ("analysis_id=4 错误详情", "/data/terminal-platform/venv/bin/python -c \"import sqlite3,json; c=sqlite3.connect('/data/terminal-platform/data/eyeterm.db'); c.row_factory=sqlite3.Row; rows=[dict(r) for r in c.execute('SELECT * FROM ai_analyses ORDER BY id DESC LIMIT 3')]; print(json.dumps(rows, ensure_ascii=False, default=str)[:1500])\" 2>&1"),
    ("LLM 调用配置（模型名/URL/key 来源）", "grep -rn 'llm\\.\\|model\\|api_key\\|chat/completions\\|eye.ac.cn' /data/terminal-platform/app/server/*.py | grep -v '^Binary' | head -30"),
    ("settings 读取方式（缓存?）", "grep -rn 'def get_settings\\|_settings_cache\\|load_settings\\|settings(' /data/terminal-platform/app/server/*.py | head -15"),
]

for name, cmd in cmds:
    stdin, stdout, stderr = c.exec_command(cmd, timeout=30)
    out = (stdout.read().decode() + stderr.read().decode()).strip()
    print("===== %s =====" % name)
    print(out[:1800] or "(empty)")
    print()

c.close()
print("DIAG_DONE")
