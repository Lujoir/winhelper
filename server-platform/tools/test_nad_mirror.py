# -*- coding: utf-8 -*-
"""画方终端镜像转换单测（nad_client._term_to_row）。

回归背景（2026-09-19 首次上线）：批量同步回 1543 台终端，但 wol-capable
全为 0 —— 原始 term 的 macs / ips 是「数字键 dict」形态（{"0": {...}}），
直接迭代 dict 只得到键字符串，MAC/IP 全被丢弃。此用例锁定该形态。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "server"))
import nad_client  # noqa: E402


def run():
    # 1) 实测形态：macs / ips / macports 均为数字键 dict
    term = {
        "oid": "OID-1", "name": "PC-1", "online": True, "owner": "张三",
        "ou": {"namepath": "总部/13F"},
        "macs": {"0": {"mac": "AA:BB:CC:DD:EE:FF",
                       "ips": {"0": {"ip": "172.17.90.5"},
                               "1": {"ip": "10.0.0.5"}},
                       "macports": {"0": {"manip": "192.168.254.6"}}}},
    }
    row = nad_client._term_to_row(term)
    assert row["oid"] == "OID-1", row
    assert row["mac"] == "AA:BB:CC:DD:EE:FF", row
    assert row["ips"] == ["172.17.90.5", "10.0.0.5"], row
    assert row["group_path"] == "总部/13F", row
    assert row["online"] is True and row["owner"] == "张三", row

    # 2) list 形态（兼容既有解析路径）
    term2 = {"oid": "OID-2", "name": "PC-2",
             "macs": [{"mac": "11:22:33:44:55:66",
                       "ips": [{"ip": "192.168.1.8"}]}]}
    row2 = nad_client._term_to_row(term2)
    assert row2["mac"] == "11:22:33:44:55:66", row2
    assert row2["ips"] == ["192.168.1.8"], row2

    # 3) 无 macs / 空 term：不抛异常，mac 空（前端禁选并标注原因）
    row3 = nad_client._term_to_row({"oid": "OID-3", "name": "PC-3"})
    assert row3["mac"] == "" and row3["ips"] == [], row3
    assert nad_client._term_to_row({})["oid"] == "", "空 term 不得抛异常"

    # 4) IP 去重 + 非法项跳过（缺 ip 键的条目不得混入）
    term4 = {"oid": "OID-4",
             "macs": {"0": {"mac": "AA:AA:AA:AA:AA:AA",
                            "ips": {"0": {"ip": "1.1.1.1"},
                                    "1": {"ip": "1.1.1.1"},
                                    "2": {}}}}}
    row4 = nad_client._term_to_row(term4)
    assert row4["ips"] == ["1.1.1.1"], row4

    print("nad mirror tests: 4/4 groups passed")


if __name__ == "__main__":
    run()
