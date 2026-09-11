# -*- coding: utf-8 -*-
import io
p = "server-platform/console/index.html"
s = io.open(p, encoding="utf-8").read()

# 1) renderInfo 姒傝鎵╁睍锛欳PU/鍐呭瓨/纭洏/GPU + 璧勪骇鏄庣粏鎸夐挳
old = ("    + '<span class=\"kv\">棣栨鎺ュ叆<b>' + fmtTs(t.first_seen) + '</b></span>'\n"
       "    + '<span class=\"kv\">鏈€杩戝績璺?b>' + fmtTs(t.last_seen) + '</b></span>'\n"
       "    + '</div>';\n"
       "}")
assert old in s, "c1"
new = ("    + '<span class=\"kv\">棣栨鎺ュ叆<b>' + fmtTs(t.first_seen) + '</b></span>'\n"
       "    + '<span class=\"kv\">鏈€杩戝績璺?b>' + fmtTs(t.last_seen) + '</b></span>'\n"
       "    + '<span class=\"kv\">CPU<b>' + esc(t.cpu_model; '-') + (t.cpu_cores ? ' (' + t.cpu_cores + ' 绾跨▼)' : '') + '</b></span>'\n"
       "    + '<span class=\"kv\">鍐呭瓨<b>' + (t.mem_total_mb ? (t.mem_total_mb / 1024).toFixed(1) + ' GB' : '-') + '</b></span>'\n"
       "    + '<span class=\"kv\">纭洏<b>' + (t.disk_total_gb ? t.disk_total_gb.toFixed(0) + ' GB' : '-') + '</b></span>'\n"
       "    + '<span class=\"kv\">鏄惧崱<b>' + esc(t.gpu_info; '-') + '</b></span>'\n"
       "    + '<span class=\"kv\">鏋舵瀯<b>' + esc(t.os_arch; '-') + '</b></span>'\n"
       "    + '</div>'\n"
       "    + '<div style=\"margin-top:10px\"><button class=\"btn\" onclick=\"showAssetModal()\">璧勪骇鏄庣粏</button></div>';\n"
       "}")
s = s.replace(old, new, 1)

# 2) 璧勪骇鏄庣粏寮圭獥锛歴howAssetModal + 娓叉煋鍑芥暟锛堣拷鍔犲湪 renderInfo 涔嬪悗锛?anchor = "function metricCard(label, value, unit, ratio, cls){"
assert anchor in s, "c2"
modal = ('/* ---------- 璧勪骇鏄庣粏寮圭獥锛堢粨鏋勫寲 asset JSON锛?---------- */\n'
         'function showAssetModal(){\n'
         '  if(!currentTid) return;\n'
         '  apiFetch("/console/terminals/" + encodeURIComponent(currentTid)).then(function(j){\n'
         '    renderAssetModal(j.asset, j.terminal);\n'
         '  }).catch(function(e){ toast("鍔犺浇璧勪骇鏄庣粏澶辫触锛? + e.message); });\n'
         '}\n\n'
         'function closeAssetModal(){\n'
         '  var ov = document.getElementById("assetModal");\n'
         '  if(ov) ov.style.display = "none";\n'
         '}\n\n'
         'function assetSec(title, rows){\n'
         '  var h = "<h3>" + esc(title) + "</h3><table>";\n'
         '  for(var i=0;i<rows.length;i++){\n'
         '    h += "<tr><td>" + esc(rows[i][0]) + "</td><td>" + rows[i][1] + "</td></tr>";\n'
         '  }\n'
         '  return h + "</table>";\n'
         '}\n\n'
         'function renderAssetModal(a, t){\n'
         '  var ov = document.getElementById("assetModal");\n'
         '  var body = document.getElementById("assetModalBody");\n'
         '  if(!a){ body.innerHTML = \'<div class="empty">鏆傛棤璧勪骇鏄庣粏鏁版嵁锛堢瓑寰呯粓绔噸鏂版敞鍐屼笂鎶ワ級</div>\';\n'
         '    ov.style.display = "flex"; return; }\n'
         '  var dash = function(v){ return (v === null; v === undefined; v === "" ) ? "-" : esc(v); };\n'
         '  var fmtGb = function(b){ return b ? (b/1073741824).toFixed(1) + " GB" : "-"; };\n'
         '  var html = "";\n'
         '  var os = a.os; {}, cpu = a.cpu; {}, mem = a.memory; {modules:[]};\n'
         '  html += assetSec("鎿嶄綔绯荤粺", [\n'
         '    ["涓绘満鍚?, dash(a.hostname)], ["绯荤粺", dash(os.caption)],\n'
         '    ["鐗堟湰", dash(os.version)], ["Build", dash(os.build)]]);\n'
         '  html += assetSec("澶勭悊鍣?, [\n'
         '    ["鍨嬪彿", dash(cpu.name)],\n'
         '    ["鐗╃悊鏍?/ 绾跨▼", (cpu.cores; "-") + " / " + (cpu.logical; "-")],\n'
         '    ["鍩哄噯 / 褰撳墠棰戠巼", (cpu.max_mhz; "-") + " MHz / " + (cpu.cur_mhz; "-") + " MHz"]]);\n'
         '  var mrows = [["鎬诲閲?, fmtGb(mem.total)]];\n'
         '  (mem.modules; []).forEach(function(m, i){\n'
         '    mrows.push(["鍐呭瓨鏉?" + (m.slot; i),\n'
         '      (m.size ? (m.size/1073741824).toFixed(1) + " GB" : "-")\n'
         '      + (m.type; m.type !== "--" ? " 路 " + m.type : "")\n'
         '      + (m.speed_mhz ? " @" + m.speed_mhz + "MHz" : "")]);\n'
         '  });\n'
         '  html += assetSec("鍐呭瓨", mrows);\n'
         '  var drows = (a.disks; []).map(function(d){\n'
         '    return [d.model; d.name; "-",\n'
         '      (d.size ? (d.size/1073741824).toFixed(0) + " GB" : "-")\n'
         '      + " 路 " + dash(d.bus) + " 路 " + dash(d.media)\n'
         '      + (d.volumes; d.volumes.length ? " 路 " + esc(d.volumes.join(" ")) : "")\n'
         '      + (d.system ? " 路 绯荤粺鐩? : "")];\n'
         '  });\n'
         '  html += assetSec("鐗╃悊纾佺洏", drows.length ? drows : [["-", "-"]]);\n'
         '  var grows = (a.gpu; []).map(function(g){\n'
         '    return [g.name; "-",\n'
         '      (g.dedicated ? "鐙樉" : "鏍告樉") + " 路 鏄惧瓨 " + dash(g.vram_text)\n'
         '      + " 路 椹卞姩 " + dash(g.driver) + " 路 " + dash(g.resolution)];\n'
         '  });\n'
         '  html += assetSec("鏄惧崱", grows.length ? grows : [["-", "-"]]);\n'
         '  var nrows = (a.network; []).map(function(n){\n'
         '    return [n.name; "-",\n'
         '      dash(n.status) + " 路 " + ((n.ipv4; n.ipv4.length) ? esc(n.ipv4.join(", "))\n'
         '        + ((n.plen; n.plen.length) ? "/" + n.plen[0] : "") : "-")\n'
         '      + " 路 缃戝叧 " + dash(n.gw)\n'
         '      + " 路 DNS " + ((n.dns; n.dns.length) ? esc(n.dns.join(", ")) : "-")\n'
         '      + " 路 " + dash(n.speed)];\n'
         '  });\n'
         '  html += assetSec("缃戠粶閫傞厤鍣?, nrows.length ? nrows : [["-", "-"]]);\n'
         '  var tp = a.temps; {};\n'
         '  html += assetSec("娓╁害", [\n'
         '    ["CPU", tp.cpu != null ? tp.cpu + " 鈩? : "-"],\n'
         '    ["GPU", tp.gpu != null ? tp.gpu + " 鈩? : "-"]]);\n'
         '  body.innerHTML = html + \'<div style="margin-top:10px;color:var(--text-muted,#5f6577);font-size:11px">閲囬泦鏃堕棿锛歕'\n'
         '    + fmtTs(a.ts) + " 路 schema " + esc(a.schema) + "</div>";\n'
         '  ov.style.display = "flex";\n'
         '}\n\n' + anchor)
s = s.replace(anchor, modal, 1)
io.open(p, "w", encoding="utf-8").write(s)
print("CONSOLE_JS_PATCHED")
