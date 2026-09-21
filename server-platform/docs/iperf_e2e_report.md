# EyeTerm 打流测试端到端联调报告

- 日期：2026-09-09
- 链路：控制台/服务端（172.17.5.215:18090）↔ winhelper 终端（WIN-Jun-office-PC，172.17.90.215）
- 结论：**4/4 测试类型全部打通**

## 端到端链路

```
控制台 launch（任务槽 + 临时端口 18200 起 iperf3 -s -1）
  → 命令通道下发 iperf_client / net_probe（ADR-015 白名单）
  → winhelper 心跳拉命令（30s 间隔）→ 捆绑 iperf3.exe 解压 %TEMP% staging 执行
  → JSON 结果回传 commands result → complete_from_command → 任务 done
```

## 实测结果

| 测试类型 | 命令 | 结果 | 闭环耗时 |
|----------|------|------|----------|
| bandwidth_tcp | iperf_client | tcp 887.8 Mbits/sec | 24s |
| udp_jitter | iperf_client | udp 1.0 Mbits/sec · jitter 0.155ms · loss 0% | 24s |
| latency_server | net_probe | ping 172.17.5.215 → 1ms · loss 0% | 48s |
| latency_gateway | net_probe | ping 网关 172.17.90.1 → 1ms · loss 0% | 48s |

## 故障定位与修复

**现象**：UDP 打流三连失败（终端报 `iperf3_timeout`，服务端进程 50s 超时被杀，任务 timeout），TCP 正常。

**根因**：服务器 firewalld 仅放行 `18200-18299/tcp`，缺 UDP。iperf3 UDP 数据包被防火墙丢弃，客户端收不到服务端统计响应，挂起直至内部超时。

**修复**：`firewall-cmd --permanent --add-port=18200-18299/udp && firewall-cmd --reload`，复测一次通过。

**教训**：iperf 端口段放行必须 TCP/UDP 成对配置；"任务 timeout + 命令 executed 失败"组合应优先排查中间网络设备对数据面（区别于控制面）的过滤。

## 环境基线（联调确认）

- 服务器：/usr/bin/iperf3 3.9；settings `iperf.server_ip=172.17.5.215`；18200-18299 tcp+udp 已放行
- 终端：winhelper.exe 按 spec 捆绑 `libs/iperf3/iperf3.exe`（v3.1.3，运行期 _MEI 解压验证），与 3.9 服务端 TCP/UDP 兼容
