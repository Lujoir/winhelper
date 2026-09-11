# -*- coding: utf-8 -*-
import io
p = "server-platform/console/index.html"
s = io.open(p, encoding="utf-8").read()
anchor = "<script>"
assert anchor in s, "h1"
modal = ('<div class="modal-ov" id="assetModal" onclick="if(event.target===this)closeAssetModal()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100;align-items:flex-start;justify-content:center;padding:40px 16px;overflow:auto">'
 '<div style="background:var(--panel,#161a26);border:1px solid var(--border,#2a2f45);border-radius:12px;max-width:760px;width:100%;padding:20px 24px">'
 '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">'
 '<h2 style="margin:0;font-size:16px">资产明细</h2>'
 '<button class="btn" onclick="closeAssetModal()">关闭</button></div>'
 '<div id="assetModalBody"></div></div></div>\n<script>')
s = s.replace(anchor, modal, 1)
css = ('  #assetModal h3{margin:14px 0 6px;font-size:13px;color:#4fc3f7}\n'
       '  #assetModal table{width:100%;border-collapse:collapse;font-size:12px}\n'
       '  #assetModal td{padding:4px 8px;border-bottom:1px dashed rgba(42,47,69,.5);vertical-align:top}\n'
       '  #assetModal td:first-child{color:var(--text-muted,#5f6577);width:130px}\n')
anchor2 = "<style>"
assert anchor2 in s, "h2"
s = s.replace(anchor2, anchor2 + css, 1)
io.open(p, "w", encoding="utf-8").write(s)
print("MODAL_HTML_OK")
