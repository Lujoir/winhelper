-- desktop-policy 联调测试残留清理（BRG-067 修复后由 main 在服务端 console.db 执行）
-- 原则：保留 policy #3（联调活策略）；清理创建失败的 #1/#2 残行。
-- 执行前先备份：cp data/console.db data/console.db.bak.<ts>

-- 1. 策略表：清创建失败/重复发布的测试残留（保留 #3）
DELETE FROM dp_policies WHERE id IN (1, 2);

-- 2. 下发记录：#1/#2 若有关联行一并清理（审计需要则注释掉这两行保留）
DELETE FROM dp_deliveries WHERE policy_id IN (1, 2);

-- 3. 校验：剩余策略与下发记录
-- SELECT id, name, revision, enabled FROM dp_policies;
-- SELECT id, policy_id, revision, terminal_id, status, error_code FROM dp_deliveries;

-- 注：#3 的 rev3 failed/apply_failed 历史行建议保留（BRG-066/067 修复核验对照）；
--     新 exe 换装后轮询重拉 rev3 生成新行，预期 warn/blocked_by_security 或 applied。
