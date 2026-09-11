# -*- coding: utf-8 -*-
"""临时脚本: 向 server-platform-dev 收件箱投递 AI 验证结果与缺陷通报"""
import json
import os

BASE = r"c:\Users\10604\CodeBuddy\20260522083146\.codebuddy\teams\_auto_760601e4064b405ba71f5310f1d93e27\inboxes"
NOW = "2026-09-08T09:05:00.000Z"

MSG = """【AI 真实调用验证已由 main 完成 ✅ + 两个待修缺陷通报】
网络放通后验证链完成：TCP/TLS 通 → smoke 45/46 → 定位两处配置被覆盖 → 修复后 AI 真实调用 ok:True（analysis_id=5，response 4874 字符，模型 AntAngelMed，完整运维分析输出）。
两个缺陷需你修复：
1. smoke[9] 覆盖生产配置（事故根因）：加密自测用 llm.api_key 写随机测试 key，且同套件覆盖了 llm.model=mock-model——直接打挂生产 AI。修复：自测改用隔离键（llm.api_key_smoke_test / llm.model_smoke_test），不触碰真实条目；修复后更新 smoke 并 commit。
2. 分析列表端点不一致：GET /api/v1/ai/analyses 返回 404——确认分析列表正确路径并与控制台/文档对齐。
当前生产配置（已恢复/设置）：llm.url=https://llm.eye.ac.cn、llm.api_key=真实 key（加密）、llm.model=AntAngelMed（/v1/models 共 8 个可用：Qwen3.6/deepseekeye/kimi-code2.7/minimax/qwen32b 等），如需换模型改 settings llm.model。
另外：两次冒烟的 WIN-SMOKE-* 测试数据（2 终端+指标+事件+3 条 AI 分析记录）待清理（你有 cleanup 工具）。收到后 ADR 补记（模型配置/冒烟隔离键），验证证据 analysis_id=5。"""

path = os.path.join(BASE, "server-platform-dev.json")
with open(path, encoding="utf-8") as f:
    box = json.load(f)
box.append({
    "id": "msg-main-ai-verified-1",
    "from": "team-lead",
    "to": "server-platform-dev",
    "type": "message",
    "content": MSG,
    "timestamp": NOW,
    "read": False,
})
with open(path, "w", encoding="utf-8") as f:
    json.dump(box, f, ensure_ascii=False, indent=2)
print("delivered to server-platform-dev | inbox size:", len(box))
