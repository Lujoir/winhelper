# -*- coding: utf-8 -*-
"""
e2e_filesearch.py — 「文件检索」E2E（独立页 + 主应用）
====================================================
桩策略：本地 127.0.0.1:5700 起 Everything HTTP 兼容桩（search_service 真实请求它——
服务层 HTTP 链路全真），前端 pywebview 桩按路由回假数据。

门禁：查询渲染/状态/错误指引/保存路径/打开位置/默认收起/联动展开 + pageerror=0。
运行：python tools/e2e_filesearch.py
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from playwright.sync_api import sync_playwright

ND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = os.path.dirname(ND_ROOT)
STANDALONE = os.path.join(ND_ROOT, "web", "filesearch-standalone.html")
MAIN_INDEX = os.path.join(WS, "web", "index.html")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name + ("" if cond else "  <- " + str(detail)[:180]))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  | " + str(detail)[:130]))


# ---------------- Everything HTTP 兼容桩（真实 5700 服务） ----------------

class FakeEverything(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        search = unquote(q.get("search", [""])[0])
        results = []
        if search:
            for i, (name, path, size) in enumerate([
                ("report_2026.pdf", "C:\\Users\\demo\\Documents", 1234567),
                ("report-final.docx", "C:\\Users\\demo\\Documents", 54321),
                ("build_report.log", "D:\\logs", 999),
            ]):
                if search.lower() in name.lower():
                    results.append({"type": "file", "name": name, "path": path,
                                    "size": size, "date_modified": 1727000000 + i})
        body = json.dumps({"totalResults": len(results), "results": results}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


FS_STUB_JS = r"""
window.__stubCalls = [];
window.__stubStatus = { success: true, db_exists: true, file_count: 123456,
    dir_count: 9876, volumes: ["C:"], db_path: "C:\\stub\\index.db",
    indexer_task_registered: true, state: "ready", hint: "" };
window.pywebview = { api: {
    call: function (path) {
        window.__stubCalls.push(path);
        var p = path.split("?")[0];
        if (p.indexOf("/api/filesearch/query") === 0) {
            var qraw = decodeURIComponent((path.split("q=")[1] || "").split("&")[0]);
            window.__stubLastQ = qraw;   /* 原始拼接串（ext 筛选断言用） */
            var q = qraw.split(" ext:")[0];
            if (q === "emptycase") { return Promise.resolve({ success: true, q: q, total: 0, results: [] }); }
            var rs0 = [
                { name: "report_2026.pdf", path: "C:\\Users\\demo\\Documents", size: 1234567,
                  date_modified: 1727000000 },
                { name: "report-final.docx", path: "C:\\Users\\demo\\Documents", size: 54321,
                  date_modified: 1727000001 },
                { name: "build_report.log", path: "D:\\logs", size: 999, date_modified: 1727000002 }];
            var sp = (path.match(/sort=([a-z_]+)/) || [])[1];
            var sa = (path.match(/ascending=(\d)/) || [])[1] !== "0";
            if (sp === "ext") {   /* 桩模拟服务端 ext 排序（扩展名字典序） */
                rs0 = rs0.slice().sort(function (a, b) {
                    var ea = a.name.split(".").pop().toLowerCase(), eb = b.name.split(".").pop().toLowerCase();
                    var c = ea < eb ? -1 : (ea > eb ? 1 : 0);
                    return sa ? c : -c;
                });
            }
            if (q === "typecase") { return Promise.resolve({ success: true, q: q, total: 5, results: [
                { name: "项目资料", path: "C:\\Users\\demo\\Documents", is_dir: true, size: null,
                  date_modified: 1727000009 },
                { name: "预算表.xlsx", path: "C:\\Users\\demo\\Documents", size: 20480,
                  date_modified: 1727000008 },
                { name: "report_2026.pdf", path: "C:\\Users\\demo\\Documents", size: 1234567,
                  date_modified: 1727000007 },
                { name: "readme.txt", path: "C:\\Users\\demo\\Documents", size: 100,
                  date_modified: 1727000006 },
                { name: "无扩展名文件", path: "C:\\Users\\demo\\Documents", size: 10,
                  date_modified: 1727000005 }] }); }
            if (q === "errorcase") { return Promise.resolve({ success: false,
                error: "indexer_not_running",
                hint: "检索索引未部署：请重新安装客户端或由管理员部署" }); }
            return Promise.resolve({ success: true, q: q, total: 3, results: rs0 });
        }
        if (p.indexOf("/api/filesearch/status") === 0) return Promise.resolve(window.__stubStatus);
        if (p.indexOf("/api/filesearch/indexer-deploy") === 0) {
            window.__stubDeployCalls = (window.__stubDeployCalls || 0) + 1;
            window.__stubStatus.state = "building";
            window.__stubStatus.hint = "索引构建中（首次需数分钟），请稍后重试";
            return Promise.resolve({ success: true, hint: "已发起部署（UAC 需确认），索引构建需数分钟，请稍后" });
        }
        if (p.indexOf("/api/filesearch/stats") === 0) return Promise.resolve({
            success: true, exts: [{ext: "pdf", count: 2}, {ext: "docx", count: 1}, {ext: "log", count: 1}],
            date_min: 1727000000, date_max: 1727000002 });
        if (p.indexOf("/api/filesearch/save-path") === 0) {
            window.__stubSavedPath = decodeURIComponent((path.split("path=")[1] || "").split("&")[0]);
            return Promise.resolve({ success: true });
        }
        if (p.indexOf("/api/filesearch/open-location") === 0) {
            window.__stubLastOpen = decodeURIComponent((path.split("path=")[1] || "").split("&")[0]);
            return Promise.resolve({ success: true });
        }
        return Promise.resolve({ success: false, error: "未知接口: " + p });
    } } };
"""


def run_scenario_1(page):
    print("\n[场景1] 独立测试页（filesearch-standalone.html + pywebview 桩）")
    page.goto("file:///" + STANDALONE.replace("\\", "/"))
    page.wait_for_timeout(900)
    check("Rename·独立页标题文件检索", "文件检索" in page.title())
    for fn in ["initFileSearchTab", "fsStartSearch", "fsRenderResults",
               "fsOpenLocation", "fsInitCollapsible", "fsExpandCard", "fsInitChips", "fsSortBy"]:
        check("[nd-fs] %s 为函数" % fn, page.evaluate("typeof %s" % fn) == "function")
    check("折叠·检索卡默认展开（状态卡已整卡移除，2026-09-16）", page.evaluate(
        "(function(){ var cs = document.querySelectorAll('#tab-filesearch .section-card[data-collapse]');"
        " if (cs.length !== 1) { return -1; }"
        " return !cs[0].classList.contains('collapsed'); })()") is True)
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("折叠·点开始检索先展开", page.evaluate(
        "(function(){ return !document.getElementById('fsSearchBtn').closest('.section-card')"
        ".classList.contains('collapsed'); })()"))
    check("未检索·筛选条件行隐藏（类型/自定义/修改时间，2026-09-16）", page.evaluate(
        "(function(){ var fr = document.getElementById('fsFilterRow');"
        " if (!fr) { return false; }"
        " var need = ['fsTypeFilterBtn','fsDateFrom','fsDateTo','fsCustomExt'];"
        " for (var i = 0; i < need.length; i++) { if (!document.getElementById(need[i])) { return false; } }"
        " return getComputedStyle(fr).display === 'none' && fr.getBoundingClientRect().height === 0; })()"))

    page.fill("#fsQuery", "report")
    page.click("#fsSearchBtn")
    page.wait_for_timeout(500)
    body = page.inner_text("#fsBody")
    check("检索·结果表渲染 3 行（显式 thead/tbody）", page.evaluate(
        "document.querySelectorAll('#fsBody tbody tr').length") == 3)
    check("检索·名称与路径列", "report_2026.pdf" in body and "Documents" in body)

    # 列宽：名称/类型/路径默认 160/160/230（自动布局的 2/3）+ 表头右缘拖拽（2026-09-16 用户要求）
    check("列宽·默认 160/160/230（大小/修改时间/操作平分剩余）", page.evaluate(
        "(function(){ var m = {}; var cs = document.querySelectorAll('#fsTable colgroup col');"
        " for (var i = 0; i < cs.length; i++) { m[cs[i].getAttribute('data-col')] = cs[i].style.width; }"
        " return m.name === '160px' && m.ext === '160px' && m.path === '230px'"
        " && !m.size && !m.mtime && !m.act; })()"))
    check("列宽·拖拽热区 6 个", page.evaluate(
        "document.querySelectorAll('#fsTable thead .fs-rs').length") == 6)
    _bx = page.evaluate("(function(){ var r = document.querySelector("
        "'#fsTable thead th[data-col=name] .fs-rs').getBoundingClientRect();"
        " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()")
    page.mouse.move(_bx["x"], _bx["y"])
    page.mouse.down()
    page.mouse.move(_bx["x"] + 90, _bx["y"], steps=5)
    page.mouse.up()
    page.wait_for_timeout(200)
    check("列宽·拖拽 +90 生效（250px）且记忆且不触发排序", page.evaluate(
        "(function(){ var c = document.querySelector('#fsTable colgroup col[data-col=name]');"
        " var saved = JSON.parse(localStorage.getItem('fs_col_widths_v1') || '{}');"
        " return c.style.width === '250px' && saved.name === 250 && !fsState.sortCol; })()"))
    page.evaluate("(function(){ try { localStorage.removeItem('fs_col_widths_v1'); } catch (e) {}"
        " fsState.colW = {};"
        " fsRenderResults({ results: fsState.lastResults, q: fsState.lastQuery,"
        " total: (fsState.lastResults || []).length }); })()")
    page.wait_for_timeout(200)
    check("列宽·清除记忆重渲染回默认 160", page.evaluate(
        "document.querySelector('#fsTable colgroup col[data-col=name]').style.width") == "160px")
    check("检索·大小格式化（MB）", "1.2 MB" in body, body[:120])
    check("检索·summary 命中与关键词", "命中 3 条" in page.inner_text("#fsSummary")
          and "report" in page.inner_text("#fsSummary"))
    check("检索·行内打开位置按钮", page.evaluate(
        "document.querySelectorAll('#fsBody button').length") >= 3)

    page.evaluate("document.querySelector('#fsBody button').click()")
    page.wait_for_timeout(300)
    check("打开位置·调用携带完整路径", page.evaluate(
        "(function(){ var p = window.__stubLastOpen || '';"
        " return p.indexOf('report_2026.pdf') >= 0 && p.indexOf('Documents') >= 0; })()"),
        page.evaluate("window.__stubLastOpen"))

    page.fill("#fsQuery", "emptycase")
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("空结果·空态渲染", "无匹配结果" in page.inner_text("#fsBody")
          and "命中 0 条" in page.inner_text("#fsSummary"))

    page.fill("#fsQuery", "errorcase")
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("错误·索引未就绪指引（并入检索卡：摘要+结果区双处可见）",
          "索引" in page.inner_text("#fsSummary")
          and "未部署" in page.inner_text("#fsSummary")
          and "管理员" in page.inner_text("#fsBody")
          and "部署索引" in page.inner_text("#fsBody"),
          {"summary": page.inner_text("#fsSummary")[:80], "body": page.inner_text("#fsBody")[:120]})

    # 未检索隐藏筛选行 → 首次检索（含失败路径）后显示（2026-09-16 用户要求）
    check("检索后·筛选条件行显示（失败路径同样触发，2026-09-16）", page.evaluate(
        "(function(){ var fr = document.getElementById('fsFilterRow');"
        " return !!fr && getComputedStyle(fr).display !== 'none'"
        " && fr.getBoundingClientRect().height > 0; })()"))

    # 状态卡零残留（2026-09-16：DOM 整卡移除，索引状态属系统内部信息；
    # 「索引(未)就绪」文案不全局查——错误指引并入检索卡属预期语义）
    check("零残留·无状态卡 DOM 与字样", page.evaluate(
        "(function(){ var t = document.getElementById('tab-filesearch').textContent;"
        " return !document.getElementById('fsStatusBody')"
        " && !document.getElementById('fsStatusSummary')"
        " && t.indexOf('检索状态与设置') < 0 && t.indexOf('刷新状态') < 0; })()"))
    # 品牌与旧说明句零残留（2026-09-16 文案清理批次：自研引擎后 Everything 字样失实）
    check("零残留·无 Everything 品牌与旧说明句（placeholder 中性保留）", page.evaluate(
        "(function(){ var tab = document.getElementById('tab-filesearch');"
        " var t = tab.textContent;"
        " var ph = (document.getElementById('fsQuery').getAttribute('placeholder') || '');"
        " return t.indexOf('Everything') < 0 && t.indexOf('即时检索全盘') < 0"
        " && t.indexOf('可定位到所在目录') < 0"
        " && ph.indexOf('Everything') < 0 && ph.indexOf('ext:pdf') >= 0; })()"))

    # 索引器部署三态（4.1.7 A/B 方案：状态卡已移除，三态在检索卡空态/错误态呈现）
    page.evaluate("window.__fsDeployPollMs = 200")
    page.evaluate('(function(){ window.__stubStatus.state = "not_deployed";'
        ' window.__stubStatus.hint = "检索索引未部署：请重新安装客户端或由管理员部署"; })()')
    page.evaluate("fsLoadDeployState()")
    page.wait_for_timeout(300)
    check("部署·not_deployed 空态提示与部署按钮", page.evaluate(
        "(function(){ var b = document.getElementById('fsDeployBtn');"
        " return !!b && !b.disabled"
        " && document.getElementById('fsBody').textContent.indexOf('尚未部署') >= 0; })()"))
    page.click("#fsDeployBtn")
    page.wait_for_timeout(500)
    check("部署·点击发起 indexer-deploy 并转入 building", page.evaluate(
        "(window.__stubDeployCalls || 0) === 1")
        and "构建中" in page.inner_text("#fsBody"), {
        "calls": page.evaluate("window.__stubDeployCalls || 0"),
        "body": page.inner_text("#fsBody")[:80]})
    check("部署·building 态按钮禁用（防重复发起）", page.evaluate(
        "(function(){ var b = document.getElementById('fsDeployBtn');"
        " return !b || b.disabled; })()"))
    page.evaluate('(function(){ window.__stubStatus.state = "ready"; window.__stubStatus.hint = ""; })()')
    page.wait_for_timeout(800)
    check("部署·轮询至 ready 恢复检索空态（间隔注入 200ms）", page.evaluate(
        "(function(){ return document.getElementById('fsBody').textContent.indexOf('尚未检索') >= 0; })()")
        and "索引就绪" in page.inner_text("#fsSummary"), {
        "summary": page.inner_text("#fsSummary")[:60]})

    # STYLE.md 符合性（2026-09-15 UI 整改门禁：computed style 实测）
    st = page.evaluate("(function(){ var tab = document.getElementById('tab-filesearch');"
        " var ph = tab.querySelector('.page-header');"
        " var h2 = ph.querySelector('h2').getBoundingClientRect();"
        " var card = document.getElementById('fsSearchBtn').closest('.section-card');"
        " var header = card.querySelector('.card-header').getBoundingClientRect();"
        " var br = document.getElementById('fsSearchBtn').getBoundingClientRect();"
        " var bcs = getComputedStyle(card.querySelector('.card-body'));"
        " var scs = getComputedStyle(card.querySelector('.nd-summary'));"
        " return { h2x: Math.round(h2.x),"
        " btnRightGap: Math.round(header.right - br.right),"
        " btnInRightHalf: br.right > header.left + header.width / 2,"
        " bodyPad: bcs.padding, sumFs: scs.fontSize, sumCol: scs.color,"
        " aiCard: card.classList.contains('nd-ai-card') }; })()")
    check("Style·页头不贴边（标题 x≥16）", st["h2x"] >= 16, st)
    check("Style·卡头按钮贴右缘（右缘距≤50 且过半）", 0 <= st["btnRightGap"] <= 50 and st["btnInRightHalf"], st)
    check("Style·卡体留白 15px 17px", st["bodyPad"] == "15px 17px", st["bodyPad"])
    check("Style·说明句 12px secondary", st["sumFs"] == "12px" and st["sumCol"] == "rgb(138, 147, 165)", st)
    check("Style·检索卡不复用 AI 卡外壳", st["aiCard"] is False, st)

    # ---- 表头排序 / 类型预筛 / 右键菜单（2026-09-15 功能迭代）----
    page.fill("#fsQuery", "report")
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    page.evaluate("fsSortBy('size')")
    page.wait_for_timeout(400)
    last = page.evaluate("window.__stubCalls[window.__stubCalls.length - 1]")
    check("排序·大小列走服务端 sort=size 升序", "sort=size" in last and "ascending=1" in last, last[-90:])
    page.evaluate("fsSortBy('size')")
    page.wait_for_timeout(400)
    last = page.evaluate("window.__stubCalls[window.__stubCalls.length - 1]")
    check("排序·再点翻转降序", "sort=size" in last and "ascending=0" in last, last[-90:])
    check("排序·表头箭头指示", "▼" in page.inner_text("#fsBody table tr"), page.inner_text("#fsBody table tr")[:50])
    qcount = page.evaluate("window.__stubCalls.filter(function(c){return c.indexOf('/api/filesearch/query')===0;}).length")
    page.evaluate("fsSortBy('ext')")   # 换列复位正序
    page.wait_for_timeout(400)
    qcount2 = page.evaluate("window.__stubCalls.filter(function(c){return c.indexOf('/api/filesearch/query')===0;}).length")
    last = page.evaluate("window.__stubCalls[window.__stubCalls.length - 1]")
    check("排序·类型列服务端化（发请求 sort=ext 升序）", qcount2 == qcount + 1
          and "sort=ext" in last and "ascending=1" in last, last[-90:])
    exts = page.evaluate("(function(){ var t=[]; var rs=document.querySelectorAll('#fsBody tbody tr');"
        " for (var i=0;i<rs.length;i++){ t.push(rs[i].querySelectorAll('td')[1].textContent); } return t.join(','); })()")
    check("排序·类型列升序 Word,文本,PDF", exts == "DOCX 文档,文本文件,PDF 文档", exts)
    page.evaluate("fsSortBy('ext')")   # 同列翻转
    page.wait_for_timeout(400)
    last = page.evaluate("window.__stubCalls[window.__stubCalls.length - 1]")
    check("排序·类型列翻转 sort=ext 倒序参数", "sort=ext" in last and "ascending=0" in last, last[-90:])
    exts2 = page.evaluate("(function(){ var t=[]; var rs=document.querySelectorAll('#fsBody tbody tr');"
        " for (var i=0;i<rs.length;i++){ t.push(rs[i].querySelectorAll('td')[1].textContent); } return t.join(','); })()")
    check("排序·类型列翻转倒序 PDF,文本,Word", exts2 == "PDF 文档,文本文件,DOCX 文档", exts2)
    page.evaluate("fsSortBy('ext')")   # 复位正序（后续用例行序回归 stub 原序）
    page.click("#fsChips .nd-chip[data-i='1']")   # pdf
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("筛选·pdf chip 拼接 ext:pdf", page.evaluate("window.__stubLastQ") == "report ext:pdf",
          page.evaluate("window.__stubLastQ"))
    page.click("#fsChips .nd-chip[data-i='2']")   # doc(x)
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("筛选·多选 OR 拼接 ext:pdf;doc;docx", page.evaluate("window.__stubLastQ") == "report ext:pdf;doc;docx",
          page.evaluate("window.__stubLastQ"))   # doc(x) chip 本含 doc+docx 两后缀，与 pdf 取并集
    check("筛选·chip 选中态两枚", page.evaluate(
        "document.querySelectorAll('#fsChips .nd-chip.on').length") == 2)
    page.click("#fsChips .nd-chip[data-i='0']")   # 全部
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("筛选·全部清空 ext", page.evaluate("window.__stubLastQ") == "report", page.evaluate("window.__stubLastQ"))
    check("筛选·全部 chip 独亮", page.evaluate(
        "document.querySelectorAll('#fsChips .nd-chip.on').length") == 1)

    # 类型下拉筛选（基于 /api/filesearch/stats 真实扩展名）
    page.evaluate("document.getElementById('fsTypeFilterBtn').click()")
    page.wait_for_timeout(200)
    check("筛选·类型下拉已渲染", page.evaluate(
        "document.querySelectorAll('#fsTypeFilterPanel .nd-type-item').length") >= 3)
    page.evaluate("(function(){ var cb = document.querySelector('#fsTypeFilterPanel input[data-ext=\"log\"]');"
        " if (cb) { cb.checked = true; cb.dispatchEvent(new Event('change')); } })()")
    page.wait_for_timeout(200)
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("筛选·类型下拉拼接 ext:log", page.evaluate("window.__stubLastQ") == "report ext:log",
          page.evaluate("window.__stubLastQ"))
    check("筛选·类型按钮显示已选计数", "已选" in page.inner_text("#fsTypeFilterBtn"))

    # 修改时间区间筛选
    page.fill("#fsDateFrom", "2025-01-01")
    page.fill("#fsDateTo", "2025-12-31")
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    last = page.evaluate("window.__stubCalls[window.__stubCalls.length - 1]")
    check("筛选·日期范围透传", "date_from=2025-01-01" in last and "date_to=2025-12-31" in last, last[-120:])

    # 自定义类型输入 + 互斥 + 组合场景（2026-09-16 排序/筛选增强）
    page.fill("#fsCustomExt", "dwg, psd；7Z")
    page.wait_for_timeout(200)
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("筛选·自定义 ext 拼接（中英逗号/空格分隔+大小写归一）",
          page.evaluate("window.__stubLastQ") == "report ext:dwg;psd;7z",
          page.evaluate("window.__stubLastQ"))
    last = page.evaluate("window.__stubCalls[window.__stubCalls.length - 1]")
    check("筛选·组合场景（自定义 ext AND 日期区间）",
          "date_from=2025-01-01" in last and "date_to=2025-12-31" in last
          and "ext:dwg;psd;7z" in page.evaluate("window.__stubLastQ"), last[-140:])
    page.click("#fsChips .nd-chip[data-i='1']")   # 点 pdf chip → 应清自定义
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    check("筛选·chips 与自定义互斥（点 chip 清自定义框）",
          page.evaluate("window.__stubLastQ") == "report ext:pdf"
          and page.evaluate("document.getElementById('fsCustomExt').value") == "",
          {"q": page.evaluate("window.__stubLastQ"),
           "v": page.evaluate("document.getElementById('fsCustomExt').value")})
    page.click("#fsChips .nd-chip[data-i='0']")   # 恢复全部
    page.fill("#fsDateFrom", "")
    page.fill("#fsDateTo", "")
    page.click("#fsSearchBtn")
    page.wait_for_timeout(400)
    last = page.evaluate("window.__stubCalls[window.__stubCalls.length - 1]")
    check("筛选·日期清空后不再透传", "date_from=" not in last and "date_to=" not in last, last[-140:])

    page.evaluate("(function(){ var rs = document.querySelectorAll('#fsBody tbody tr');"
        " for (var i = 1; i < rs.length; i++) { if (rs[i].textContent.indexOf('report_2026.pdf') >= 0) {"
        " rs[i].dispatchEvent(new MouseEvent('contextmenu', {bubbles:true, cancelable:true})); break; } } })()")
    page.wait_for_timeout(200)
    check("右键·菜单出现含两动作", page.evaluate(
        "(function(){ var m = document.getElementById('fsCtxMenu');"
        " return !!m && m.style.display === 'block'"
        " && m.textContent.indexOf('打开文件位置') >= 0 && m.textContent.indexOf('复制路径') >= 0; })()"))
    check("右键·菜单携带完整路径", page.evaluate(
        "(document.getElementById('fsCtxMenu').getAttribute('data-full')||'').indexOf('report_2026.pdf') >= 0"))
    page.evaluate("(function(){ var its = document.querySelectorAll('#fsCtxMenu .nd-ctx-item');"
        " for (var i=0;i<its.length;i++){ if (its[i].getAttribute('data-act')==='copy') { its[i].click(); } } })()")
    page.wait_for_timeout(200)
    check("右键·复制路径反馈并关闭", ("已复制" in page.inner_text("#fsSummary")
          or "复制失败" in page.inner_text("#fsSummary"))   # headless 剪贴板可能拒绝，动作链路可见即算通过
          and page.evaluate("document.getElementById('fsCtxMenu').style.display") == "none",
          page.inner_text("#fsSummary")[:40])
    page.evaluate("(function(){ var rs = document.querySelectorAll('#fsBody tbody tr');"
        " for (var i=0;i<rs.length;i++){ if (rs[i].textContent.indexOf('report-final.docx') >= 0) {"
        " rs[i].dispatchEvent(new MouseEvent('contextmenu', {bubbles:true, cancelable:true})); break; } } })()")
    page.evaluate("(function(){ var its = document.querySelectorAll('#fsCtxMenu .nd-ctx-item');"
        " for (var i=0;i<its.length;i++){ if (its[i].getAttribute('data-act')==='open') { its[i].click(); } } })()")
    page.wait_for_timeout(300)
    check("右键·打开位置动作生效", page.evaluate(
        "(window.__stubLastOpen||'').indexOf('docx') >= 0"
        " && (window.__stubLastOpen||'').indexOf('report_2026.pdf') < 0"), page.evaluate("window.__stubLastOpen"))

    # ---- 展示增强（2026-09-16，对标资源管理器详情视图）：内联 SVG 图标 + 友好类型名 + 后缀原样 ----
    page.fill("#fsQuery", "typecase")
    page.click("#fsSearchBtn")
    page.wait_for_timeout(500)
    check("展示·行首类型图标为内联 SVG（四类 folder/xls/pdf/text 齐备）", page.evaluate(
        "(function(){ var cs = ['fs-ic-folder','fs-ic-xls','fs-ic-pdf','fs-ic-text'];"
        " for (var i = 0; i < cs.length; i++) {"
        " if (!document.querySelector('#fsBody .' + cs[i])) { return 'missing:' + cs[i]; } }"
        " return document.querySelectorAll('#fsBody svg.fs-ic').length === 5; })()"))
    check("展示·图标列零 emoji（svg 元素非文本符号）", page.evaluate(
        "(function(){ var ic = document.querySelector('#fsBody .fs-icon');"
        " return !!ic && ic.children.length === 1 && ic.children[0].tagName === 'svg'; })()"))
    check("展示·目录行类型=文件夹（is_dir 强制，不受名称带点影响）", page.evaluate(
        "(function(){ var c = document.querySelector('#fsBody tr.fs-dir .fs-type-cell');"
        " return !!c && c.textContent === '文件夹'; })()"))
    check("展示·类型名友好化（XLSX 工作表 / PDF 文档 / 文本文件）", page.evaluate(
        "(function(){ var m = {}; var rs = document.querySelectorAll('#fsBody tbody tr');"
        " for (var i = 0; i < rs.length; i++) { var n = rs[i].querySelector('.fs-name');"
        " if (n) { m[n.textContent] = rs[i].querySelector('.fs-type-cell').textContent; } }"
        " return m['预算表.xlsx'] === 'XLSX 工作表' && m['report_2026.pdf'] === 'PDF 文档'"
        " && m['readme.txt'] === '文本文件'; })()"))
    check("展示·无扩展名文件兜底「文件」", page.evaluate(
        "(function(){ var rs = document.querySelectorAll('#fsBody tbody tr');"
        " for (var i = 0; i < rs.length; i++) { var n = rs[i].querySelector('.fs-name');"
        " if (n && n.textContent === '无扩展名文件') {"
        " return rs[i].querySelector('.fs-type-cell').textContent === '文件'; } }"
        " return false; })()"))
    check("展示·文件名后缀原样保留（全链无剥离）", page.evaluate(
        "(function(){ var ns = document.querySelectorAll('#fsBody .fs-name'); var ok = 0;"
        " for (var i = 0; i < ns.length; i++) { var t = ns[i].textContent;"
        " if (t.indexOf('.xlsx') >= 0 || t.indexOf('.pdf') >= 0 || t.indexOf('.txt') >= 0) { ok++; } }"
        " return ok === 3; })()"))
    check("展示·图标与名称同行（名称单元格结构）", page.evaluate(
        "(function(){ var c = document.querySelector('#fsBody .fs-name-cell');"
        " return !!c && !!c.querySelector('.fs-icon svg') && !!c.querySelector('.fs-name'); })()"))


def run_scenario_2(page):
    print("\n[场景2] 主应用集成（web/index.html + 桩）")
    page.goto("file:///" + MAIN_INDEX.replace("\\", "/"))
    page.wait_for_timeout(1200)
    for fn in ["switchTab", "initFileSearchTab", "initNetDoctorTab", "initPerfTab"]:
        check("[main] %s 为函数" % fn, page.evaluate("typeof %s" % fn) == "function")
    page.click('button[data-tab="filesearch"]')
    page.wait_for_timeout(600)
    check("主应用·文件检索页激活", page.evaluate(
        "document.getElementById('tab-filesearch').classList.contains('active')"))
    check("主应用·检索卡默认展开（状态卡已整卡移除，2026-09-16）", page.evaluate(
        "(function(){ var c0 = document.getElementById('fsSearchBtn').closest('.section-card');"
        " var cards = document.querySelectorAll('#tab-filesearch .section-card[data-collapse]');"
        " return c0 && !c0.classList.contains('collapsed') && cards.length === 1; })()"))
    check("主应用·未检索筛选条件行隐藏（类型/自定义/修改时间，2026-09-16）", page.evaluate(
        "(function(){ var fr = document.getElementById('fsFilterRow');"
        " if (!fr) { return false; }"
        " var need = ['fsTypeFilterBtn','fsDateFrom','fsCustomExt'];"
        " for (var i = 0; i < need.length; i++) { if (!document.getElementById(need[i])) { return false; } }"
        " return getComputedStyle(fr).display === 'none' && fr.getBoundingClientRect().height === 0; })()"))
    page.evaluate("document.getElementById('fsSearchBtn').click()")   # 联动展开（收起态程序化点击）
    page.wait_for_timeout(300)
    diag = page.evaluate("(function(){ var q = document.getElementById('fsQuery');"
        " var chain = []; var el = q;"
        " while (el && el !== document.body) { var cs = getComputedStyle(el);"
        " chain.push(el.tagName + '.' + String(el.className || '').slice(0, 24) + ':' + cs.display"
        " + (cs.position === 'fixed' ? ':FIXED' : '')); el = el.parentElement; }"
        " return chain; })()")
    print("  DIAG:", diag)
    page.fill("#fsQuery", "report")
    page.evaluate("document.getElementById('fsSearchBtn').click()")
    page.wait_for_timeout(500)
    check("主应用·检索渲染", page.evaluate(
        "document.querySelectorAll('#fsBody tbody tr').length") == 3)
    # 未检索隐藏筛选行 → 首次检索后显示（2026-09-16 用户要求）
    check("主应用·检索后筛选条件行显示（2026-09-16）", page.evaluate(
        "(function(){ var fr = document.getElementById('fsFilterRow');"
        " return !!fr && getComputedStyle(fr).display !== 'none'"
        " && fr.getBoundingClientRect().height > 0; })()"))
    # 列宽：默认 160/160/230 + 拖拽热区（2026-09-16，主应用同款）
    check("主应用·列宽默认 160/160/230 且热区 6 个", page.evaluate(
        "(function(){ var m = {}; var cs = document.querySelectorAll('#fsTable colgroup col');"
        " for (var i = 0; i < cs.length; i++) { m[cs[i].getAttribute('data-col')] = cs[i].style.width; }"
        " return m.name === '160px' && m.ext === '160px' && m.path === '230px'"
        " && !m.size && !m.mtime && !m.act"
        " && document.querySelectorAll('#fsTable thead .fs-rs').length === 6; })()"))
    # 状态卡零残留（2026-09-16：主应用同款整卡移除）
    check("主应用·状态卡零残留", page.evaluate(
        "(function(){ var t = document.getElementById('tab-filesearch').textContent;"
        " return !document.getElementById('fsStatusBody')"
        " && !document.getElementById('fsStatusSummary')"
        " && t.indexOf('检索状态与设置') < 0 && t.indexOf('刷新状态') < 0; })()"))
    # 品牌与旧说明句零残留（2026-09-16 文案清理批次，主应用同款）
    check("主应用·无 Everything 品牌与旧说明句字样", page.evaluate(
        "(function(){ var t = document.getElementById('tab-filesearch').textContent;"
        " return t.indexOf('Everything') < 0 && t.indexOf('即时检索全盘') < 0"
        " && t.indexOf('可定位到所在目录') < 0; })()"))
    # STYLE.md 符合性（2026-09-15 UI 整改门禁：页内边距/卡头贴右/说明句规格，computed style 实测）
    st = page.evaluate("(function(){ var tab = document.getElementById('tab-filesearch');"
        " var h2 = tab.querySelector('.page-header h2').getBoundingClientRect();"
        " var card = document.getElementById('fsSearchBtn').closest('.section-card');"
        " var header = card.querySelector('.card-header').getBoundingClientRect();"
        " var br = document.getElementById('fsSearchBtn').getBoundingClientRect();"
        " var bcs = getComputedStyle(card.querySelector('.card-body'));"
        " var scs = getComputedStyle(card.querySelector('.nd-summary'));"
        " var inMain = !!card.closest('.main-content');"
        " return { h2x: Math.round(h2.x), inMain: inMain,"
        " btnRightGap: Math.round(header.right - br.right),"
        " btnInRightHalf: br.right > header.left + header.width / 2,"
        " bodyPad: bcs.padding, sumFs: scs.fontSize, sumCol: scs.color,"
        " aiCard: card.classList.contains('nd-ai-card') }; })()")
    check("Style·主应用·页头不贴边（x≥24 且在 main-content 内）", st["h2x"] >= 24 and st["inMain"], st)
    check("Style·主应用·卡头按钮贴右缘（右缘距≤50 且过半）", 0 <= st["btnRightGap"] <= 50 and st["btnInRightHalf"], st)
    check("Style·主应用·卡体留白 15px 17px", st["bodyPad"] == "15px 17px", st["bodyPad"])
    check("Style·主应用·说明句 12px secondary", st["sumFs"] == "12px" and st["sumCol"] == "rgb(154, 160, 176)", st)
    check("Style·主应用·检索卡不复用 AI 卡外壳", st["aiCard"] is False, st)
    # 排序 / 类型预筛 / 右键菜单抽查（2026-09-15 功能迭代）
    page.evaluate("fsSortBy('size')")
    page.wait_for_timeout(400)
    last = page.evaluate("window.__stubCalls[window.__stubCalls.length - 1]")
    check("主应用·排序参数透传", "sort=size" in last and "ascending=1" in last, last[-80:])
    page.click("#fsChips .nd-chip[data-i='1']")
    page.evaluate("document.getElementById('fsSearchBtn').click()")
    page.wait_for_timeout(400)
    check("主应用·ext 拼接", page.evaluate("window.__stubLastQ") == "report ext:pdf",
          page.evaluate("window.__stubLastQ"))
    page.evaluate("(function(){ var tr = document.querySelectorAll('#fsBody tbody tr')[1];"
        " tr.dispatchEvent(new MouseEvent('contextmenu', {bubbles:true, cancelable:true})); })()")
    page.wait_for_timeout(200)
    check("主应用·右键菜单出现", page.evaluate(
        "document.getElementById('fsCtxMenu').style.display") == "block")
    # 其它模块不破坏（抽查）
    check("主应用·网络排障函数保持", page.evaluate("typeof ndStartTracert") == "function"
          and page.evaluate("typeof initNetDoctorTab") == "function")
    # 展示增强主应用抽查（2026-09-16）：SVG 图标 + 友好类型名随双仓同步生效
    page.fill("#fsQuery", "typecase")
    page.evaluate("document.getElementById('fsSearchBtn').click()")
    page.wait_for_timeout(500)
    check("主应用·SVG 图标与文件夹类型名渲染", page.evaluate(
        "(function(){ return document.querySelectorAll('#fsBody svg.fs-ic').length === 5"
        " && !!(document.querySelector('#fsBody tr.fs-dir .fs-type-cell') || {})"
        " && document.querySelector('#fsBody tr.fs-dir .fs-type-cell').textContent === '文件夹'; })()"))

    # 默认展开态强制恢复（2026-09-16 用户要求：文件检索模块不默认收起）
    page.evaluate("toggleAllCards(document.querySelector('#tab-filesearch .page-header button'))")
    page.wait_for_timeout(200)
    check("主应用·收纳全部后卡片收起（前置态）", page.evaluate(
        "document.getElementById('fsSearchBtn').closest('.section-card').classList.contains('collapsed')"))
    page.evaluate("switchTab('filesearch')")
    page.wait_for_timeout(400)
    check("主应用·重进文件检索页恢复默认展开（已检索的筛选行保持显示）", page.evaluate(
        "(function(){ var c = document.getElementById('fsSearchBtn').closest('.section-card');"
        " var fr = document.getElementById('fsFilterRow');"
        " return !c.classList.contains('collapsed')"
        " && getComputedStyle(fr).display !== 'none' && fr.getBoundingClientRect().height > 0; })()"))


def main():
    httpd = HTTPServer(("127.0.0.1", 5700), FakeEverything)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    print("Everything 兼容桩已起：127.0.0.1:5700")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append("S1:" + str(e)))
            page.add_init_script(FS_STUB_JS)
            try:
                run_scenario_1(page)
            finally:
                browser.close()

            errors2 = []
            browser = p.chromium.launch()
            page2 = browser.new_page(viewport={"width": 1440, "height": 900})
            page2.on("pageerror", lambda e: errors2.append("S2:" + str(e)))
            page2.add_init_script(FS_STUB_JS)
            try:
                run_scenario_2(page2)
            finally:
                browser.close()

        print("\n[JS errors] S1=%d, S2=%d" % (len(errors), len(errors2)))
        check("no-pageerror（两场景）", len(errors) == 0 and len(errors2) == 0,
              errors + errors2)
    finally:
        httpd.shutdown()
        httpd.server_close()

    total = len(PASS) + len(FAIL)
    print("\n===== RESULT: %d/%d passed =====" % (len(PASS), total))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("E2E FILESEARCH GATE: PASS")


if __name__ == "__main__":
    main()
