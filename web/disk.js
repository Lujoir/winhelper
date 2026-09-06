/* ==========================================
   磁盘清理模块 - 前端逻辑
   安全模型:
     🟢 安全级垃圾  → 一键清理 / 勾选清理
     🟡 谨慎级垃圾  → 默认不勾选, 显式确认后清理
     🔴 大文件      → 仅定位+分析, 不提供删除, 手动处理
   ========================================== */

// 全局状态
let junkScanId = null;
let junkPollTimer = null;
let cleanupJobId = null;
let cleanupPollTimer = null;
let largeScanId = null;
let largePollTimer = null;
let diskTabInited = false;
let latestJunkResult = null;

// ===================== Tab 接入 =====================
// 由 app.js 的 switchTab 调用（首次进入时初始化）
function initDiskTab() {
    if (diskTabInited) return;
    diskTabInited = true;
    loadDiskOverview();
    loadTreeDrives().then(() => {
        // 自动启动C盘目录树扫描：为仪表盘/智能推荐/目录树供数（全量约1-3分钟，后台执行）
        if (!window._treeDone && !window._treeRunning) startLargeScan(true);
    });
    startJunkScan(true);   // 进入页面自动扫描垃圾
    scanInstallers(true);  // 自动扫描安装包
    if (typeof initAppdataTab === "function") initAppdataTab();  // 初始化应用数据迁移模块（加载迁移目标盘）
}

// ===================== 磁盘范围选择 =====================
let treeScanId = null;
let treeCache = null;   // 当前层 children 数据

function loadTreeDrives() {
    return apiFetch("/api/disk/drives").then(data => {
        const sel = document.getElementById("largeScope");
        if (!data.success || !data.drives.length) {
            sel.innerHTML = '<option value="C:\\">C:\\</option>';
        } else {
            sel.innerHTML = data.drives.map(d =>
                `<option value="${d.drive}">${d.drive} 已用${d.used_percent}% (剩 ${d.free_text})</option>`
            ).join("") + '<option value="__CUSTOM__">自定义目录…</option>';
        }
    }).catch(() => {});
}

function onTreeScopeChange() {
    const scope = document.getElementById("largeScope").value;
    document.getElementById("largeCustomDir").style.display = scope === "__CUSTOM__" ? "" : "none";
}

// ===================== 磁盘概览 =====================
async function loadDiskOverview() {
    try {
        const data = await apiFetch("/api/disk/overview");
        if (!data.success) return;
        window._diskUsedBytes = data.used;  // 容量守恒视图使用
        document.getElementById("diskTotal").textContent = data.total_text;
        document.getElementById("diskUsed").textContent = data.used_text;
        document.getElementById("diskFree").textContent = data.free_text;
        document.getElementById("diskUsedPercent").textContent = `已用 ${data.used_percent}%`;
        const fill = document.getElementById("diskUsageFill");
        fill.style.width = Math.min(100, data.used_percent) + "%";
        fill.style.background = data.used_percent > 90
            ? "linear-gradient(90deg,#ff8a65,#ff5252)"
            : data.used_percent > 75
                ? "linear-gradient(90deg,#ffd54f,#ff8a65)"
                : "linear-gradient(90deg,#00bcd4,#4fc3f7)";
    } catch (e) { /* 静默 */ }
}

function updateCleanableBadge() {
    const el = document.getElementById("diskCleanable");
    if (latestJunkResult) {
        el.textContent = latestJunkResult.total_size_text;
    }
}

// ===================== 垃圾扫描 =====================
async function startJunkScan(silent) {
    const btn = document.getElementById("junkScanBtn");
    btn.disabled = true;
    document.getElementById("junkCleanSelBtn").disabled = true;
    showJunkProgress(true, "正在扫描垃圾文件…");

    try {
        const data = await apiFetch("/api/disk/scan?type=junk");
        if (!data.success) {
            showJunkProgress(false);
            showError(data.error || "扫描启动失败");
            btn.disabled = false;
            return;
        }
        junkScanId = data.scan_id;
        pollJunkScan(silent === true);
    } catch (e) {
        showJunkProgress(false);
        btn.disabled = false;
        showError("扫描请求失败: " + e.message);
    }
}

function pollJunkScan(silent) {
    clearInterval(junkPollTimer);
    junkPollTimer = setInterval(async () => {
        try {
            const data = await apiFetch(`/api/disk/scan-status?scan_id=${junkScanId}`);
            if (!data.success) { clearInterval(junkPollTimer); return; }
            const t = data.task;

            if (t.status === "running") {
                const p = t.progress || {};
                const pct = p.total ? Math.round((p.done / p.total) * 100) : 5;
                setJunkProgress(pct, `正在分析: ${p.current || "…"} (${p.done || 0}/${p.total || "?"})`);
            } else {
                clearInterval(junkPollTimer);
                showJunkProgress(false);
                document.getElementById("junkScanBtn").disabled = false;

                if (t.status === "done" && t.result) {
                    latestJunkResult = t.result;
                    renderJunkCategories(t.result);
                    updateCleanableBadge();
                    loadDiskOverview();
                } else if (t.status === "cancelled") {
                    showJunkProgress(false);
                } else if (t.status === "error") {
                    showError("扫描失败: " + (t.error || "未知错误"));
                }
            }
        } catch (e) { /* 下次重试 */ }
    }, 700);
}

function renderJunkCategories(result) {
    const grid = document.getElementById("junkGrid");
    const cats = result.categories || [];

    if (cats.length === 0) {
        grid.innerHTML = '<div class="empty-state-full" style="grid-column:1/-1">未获取到分类数据</div>';
        return;
    }

    grid.innerHTML = cats.map(c => {
        const empty = c.count === 0;
        return `
        <label class="junk-card ${empty ? "junk-empty" : ""}" title="${escapeHtml(c.desc)}">
            <input type="checkbox" class="junk-check" data-key="${c.key}"
                   data-risk="${c.risk}" ${c.default_checked && !empty ? "checked" : ""} ${empty ? "disabled" : ""}
                   onchange="onJunkCheckChange()">
            <div class="junk-info">
                <div class="junk-name">
                    ${c.name}
                    <span class="risk-badge ${c.risk === "safe" ? "risk-safe" : "risk-caution"}">${c.risk === "safe" ? "安全" : "谨慎"}</span>
                </div>
                <div class="junk-desc">${escapeHtml(c.desc)}</div>
                <div class="junk-paths">${(c.paths || []).map(p => `<span class="junk-path" title="${escapeHtml(p)}">${escapeHtml(p)}</span>`).join("") || '<span class="junk-path">回收站 (系统管理)</span>'}</div>
            </div>
            <div class="junk-size">
                <div class="junk-size-val">${c.size_text}</div>
                <div class="junk-size-cnt">${c.count} 项</div>
            </div>
        </label>`;
    }).join("");

    onJunkCheckChange();
}

function onJunkCheckChange() {
    const checked = document.querySelectorAll(".junk-check:checked").length;
    document.getElementById("junkCleanSelBtn").disabled = checked === 0;
}

// ===================== 垃圾清理 =====================
async function cleanSelected() {
    const keys = [...document.querySelectorAll(".junk-check:checked")].map(el => el.dataset.key);
    if (keys.length === 0) return;
    startCleanup(keys, "清理选中项");
}

async function startCleanup(categories, title) {
    if (!categories.length) return;
    if (!confirm(`${title}\n\n将清理 ${categories.length} 个分类的文件。\n清理的文件不可恢复（回收站项除外），确定继续？`)) return;

    document.getElementById("junkCleanSelBtn").disabled = true;
    document.getElementById("junkScanBtn").disabled = true;
    showJunkProgress(true, "正在清理…");

    try {
        const data = await apiFetch(`/api/disk/cleanup?categories=${categories.join(",")}`);
        if (!data.success) {
            showJunkProgress(false);
            showError(data.error || "清理启动失败");
            onJunkCheckChange();
            document.getElementById("junkScanBtn").disabled = false;
            return;
        }
        cleanupJobId = data.job_id;
        pollCleanup();
    } catch (e) {
        showJunkProgress(false);
        showError("清理请求失败: " + e.message);
    }
}

function pollCleanup() {
    clearInterval(cleanupPollTimer);
    cleanupPollTimer = setInterval(async () => {
        try {
            const data = await apiFetch(`/api/disk/scan-status?scan_id=${cleanupJobId}`);
            if (!data.success) { clearInterval(cleanupPollTimer); return; }
            const t = data.task;

            if (t.status === "running") {
                const p = t.progress || {};
                const bar = document.getElementById("junkProgressBar");
                const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;

                if (p.files_done !== undefined) {
                    // 分类内逐文件实时进度
                    const freedText = humanSizeRaw(p.freed || 0);
                    setJunkProgress(
                        null,
                        `正在清理: ${p.current || "…"} · 已处理 ${fmtNum(p.files_done)} 个文件 · 已释放 ${freedText}`
                    );
                    if (p.done === 0) {
                        // 首个分类进行中：流动条纹动画表示任务活跃
                        bar.classList.add("indeterminate");
                        bar.style.width = "100%";
                    } else {
                        bar.classList.remove("indeterminate");
                        bar.style.width = Math.max(pct, 4) + "%";
                    }
                } else {
                    setJunkProgress(Math.max(pct, 4), `正在清理: ${p.current || "…"} (${p.done || 0}/${p.total || "?"})`);
                }
            } else {
                clearInterval(cleanupPollTimer);
                showJunkProgress(false);
                document.getElementById("junkScanBtn").disabled = false;

                if (t.status === "done" && t.result && t.result.success) {
                    const r = t.result;
                    showToast(`✅ 清理完成：释放 ${r.total_freed_text}，删除 ${r.total_deleted} 项${r.total_failed ? `，跳过 ${r.total_failed} 项(占用/权限)` : ""}`);
                    if (r.hint) console.info(r.hint);
                    // 自动重新扫描刷新数据
                    setTimeout(() => startJunkScan(true), 800);
                } else if (t.status === "cancelled") {
                    showToast("清理已取消");
                } else {
                    showError("清理失败: " + (t.result && t.result.error || t.error || "未知错误"));
                }
            }
        } catch (e) { /* 重试 */ }
    }, 500);
}

// ===================== 安装包清理 =====================
let insScanId = null;
let insPollTimer = null;
let insJobId = null;
let insJobPollTimer = null;
let insItems = [];   // 安装包列表（含勾选状态）
let insTotal = 0;

function scanInstallers(silent) {
    const btn = document.getElementById("insScanBtn");
    const scope = document.getElementById("insScope").value;
    const custom = document.getElementById("insCustomDir").value.trim();
    if (scope === "custom" && !custom) { showError("请输入自定义目录路径"); return; }

    btn.disabled = true;
    document.getElementById("insCleanBtn").disabled = true;
    if (!silent) showInsProgress(true, "正在扫描安装包…");

    let q = `/api/installers/scan?scope=${encodeURIComponent(scope)}`;
    if (scope === "custom") q += `&custom=${encodeURIComponent(custom)}`;

    apiFetch(q).then(data => {
        if (!data.success) {
            showInsProgress(false);
            btn.disabled = false;
            if (!silent) showError(data.error || "扫描启动失败");
            return;
        }
        insScanId = data.scan_id;
        clearInterval(insPollTimer);
        insPollTimer = setInterval(pollInstallerScan, 700);
    }).catch(e => {
        showInsProgress(false);
        btn.disabled = false;
        if (!silent) showError("扫描请求失败: " + e.message);
    });
}

function onInsScopeChange() {
    const scope = document.getElementById("insScope").value;
    document.getElementById("insCustomDir").style.display = scope === "custom" ? "" : "none";
}

function pollInstallerScan() {
    apiFetch(`/api/disk/scan-status?scan_id=${insScanId}`).then(data => {
        if (!data.success) { clearInterval(insPollTimer); return; }
        const t = data.task;

        if (t.status === "running") {
            const p = t.progress || {};
            const pct = p.total ? Math.round((p.done / p.total) * 100) : 10;
            setInsProgress(Math.max(pct, 8), `正在扫描: ${p.current || "…"}`);
        } else {
            clearInterval(insPollTimer);
            showInsProgress(false);
            document.getElementById("insScanBtn").disabled = false;

            if (t.status === "done" && t.result && t.result.success) {
                renderInstallers(t.result);
            } else if (t.status === "error") {
                showError("安装包扫描失败: " + (t.error || "未知错误"));
            }
        }
    }).catch(() => {});
}

function renderInstallers(r) {
    insItems = (r.items || []).map(it => ({...it, checked: !!it.auto}));
    insTotal = r.total || insItems.length;

    const bd = r.browser_dirs || [];
    const bdText = bd.length
        ? ` · 浏览器下载目录: ${bd.map(b => `${b.label}(${shortPath(b.path, 24)})`).join("、")}`
        : "";
    document.getElementById("insSummary").innerHTML =
        `扫描范围: ${escapeHtml((r.scanned_zones || []).join("、"))}${bdText}<br>` +
        `共发现 <strong>${r.total}</strong> 个安装包（${r.total_size_text}），` +
        `其中明确的安装包 <strong style="color:var(--accent-cyan)">${r.auto_count}</strong> 个 ` +
        `（${r.auto_size_text}）已默认勾选，其余待人工确认。`;

    renderInstallerRows();
}

/** 渲染安装包列表：勾选优先 → 大小降序 */
function renderInstallerRows() {
    const tbody = document.getElementById("insBody");
    const items = [...insItems].sort((a, b) =>
        (b.checked - a.checked) || (b.size - a.size));

    // 表头总控勾选框状态（全选/半选/未选）
    const master = document.getElementById("insMasterCheck");
    if (master) {
        const n = items.filter(i => i.checked).length;
        master.checked = items.length > 0 && n === items.length;
        master.indeterminate = n > 0 && n < items.length;
    }

    if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">未发现安装包 🎉</td></tr>';
        return;
    }

    tbody.innerHTML = items.map(it => `
        <tr class="${it.checked ? "row-checked" : ""}">
            <td style="text-align:center"><input type="checkbox" class="ins-check" data-path="${escapeHtml(it.path)}"
                data-size="${it.size}" ${it.checked ? "checked" : ""} onchange="onInstallerCheckChange(this)"></td>
            <td title="${escapeHtml(it.path)}" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(it.name)}</td>
            <td style="white-space:nowrap"><strong>${it.size_text}</strong></td>
            <td style="white-space:nowrap"><span class="type-badge tb-info">${it.ext}</span></td>
            <td style="white-space:nowrap">${it.dir_label}</td>
            <td style="white-space:nowrap;font-size:12px;color:var(--text-secondary)">${it.modified}</td>
            <td style="white-space:nowrap"><span class="type-badge ${it.auto ? "tb-safe" : "tb-warn"}" title="${escapeHtml(it.hint || "")}">${it.auto ? "明确" : "待确认"}</span></td>
            <td><button class="btn btn-mini btn-ghost" onclick="openLocation('${escapeJs(it.path)}')">位置</button></td>
        </tr>
    `).join("") + (insTotal > insItems.length ? `<tr><td colspan="8" class="empty-state" style="padding:8px">其余 ${insTotal - insItems.length} 个已省略</td></tr>` : "");
}

/** 表头总控: 全选 / 取消全选 */
function toggleAllInstallers(masterEl) {
    insItems.forEach(i => i.checked = masterEl.checked);
    renderInstallerRows();
    onInstallerCheck();
}

/** 单个勾选变化 → 更新状态并重排（勾选项自动上浮） */
function onInstallerCheckChange(cb) {
    const it = insItems.find(i => i.path === cb.dataset.path);
    if (it) it.checked = cb.checked;
    renderInstallerRows();
    onInstallerCheck();
}

function onInstallerCheck() {
    const checked = insItems.filter(i => i.checked);
    const btn = document.getElementById("insCleanBtn");
    btn.disabled = checked.length === 0;
    const totalSize = checked.reduce((a, i) => a + i.size, 0);
    btn.textContent = checked.length
        ? `🗑 清理勾选安装包 (${checked.length} 个 · ${humanSizeRaw(totalSize)})`
        : "🗑 清理勾选安装包";
}

function cleanInstallers() {
    const paths = insItems.filter(i => i.checked).map(i => i.path);
    if (!paths.length) return;
    const totalSize = insItems.filter(i => i.checked).reduce((a, i) => a + i.size, 0);
    if (!confirm(`清理安装包确认（不可恢复）\n\n将永久删除 ${paths.length} 个安装包，共 ${humanSizeRaw(totalSize)}。\n\n确定继续？`)) return;

    document.getElementById("insCleanBtn").disabled = true;
    document.getElementById("insScanBtn").disabled = true;
    showInsProgress(true, "正在清理安装包…");

    apiFetch(`/api/appdata/delete?paths=${paths.map(encodeURIComponent).join("|")}`)
        .then(data => {
            if (!data.success) {
                showInsProgress(false);
                showError(data.error || "清理启动失败");
                onInstallerCheck();
                document.getElementById("insScanBtn").disabled = false;
                return;
            }
            insJobId = data.job_id;
            clearInterval(insJobPollTimer);
            insJobPollTimer = setInterval(pollInstallerClean, 600);
        })
        .catch(e => {
            showInsProgress(false);
            showError("清理请求失败: " + e.message);
        });
}

function pollInstallerClean() {
    apiFetch(`/api/disk/scan-status?scan_id=${insJobId}`).then(data => {
        if (!data.success) { clearInterval(insJobPollTimer); return; }
        const t = data.task;

        if (t.status === "running") {
            const p = t.progress || {};
            const pct = p.total ? Math.round((p.done / p.total) * 100) : 10;
            setInsProgress(Math.max(pct, 10), `正在删除: ${p.current || "…"} (${p.done || 0}/${p.total || "?"})`);
        } else {
            clearInterval(insJobPollTimer);
            showInsProgress(false);
            document.getElementById("insScanBtn").disabled = false;

            if (t.status === "done" && t.result && t.result.success) {
                const r = t.result;
                showToast(`✅ 安装包清理完成：删除 ${r.deleted} 个，释放 ${r.freed_text}${r.failed ? `，跳过 ${r.failed}` : ""}`);
                scanInstallers(true);  // 刷新列表
            } else if (t.status === "cancelled") {
                showToast("清理已取消");
            } else {
                showError("清理失败: " + (t.result && t.result.error || t.error || "未知错误"));
            }
        }
    }).catch(() => {});
}

function showInsProgress(show, text) {
    document.getElementById("insProgressWrap").style.display = show ? "flex" : "none";
    if (text) document.getElementById("insProgressText").textContent = text;
    if (!show) document.getElementById("insProgressBar").style.width = "0";
}

function setInsProgress(pct, text) {
    document.getElementById("insProgressBar").style.width = pct + "%";
    document.getElementById("insProgressText").textContent = text;
}

// ===================== 容量定位分析（目录树） =====================
function startLargeScan() {
    const btn = document.getElementById("largeScanBtn");
    let scope = document.getElementById("largeScope").value;
    const minMb = document.getElementById("largeMinMb").value;
    let root = scope;
    if (scope === "__CUSTOM__") {
        root = document.getElementById("largeCustomDir").value.trim();
        if (!root) { showError("请输入自定义目录路径"); return; }
    }
    if (!root) root = "C:\\";  // 磁盘列表未就绪时兜底

    btn.disabled = true;
    document.getElementById("largeCancelBtn").style.display = "";
    showLargeProgress(true, "正在扫描目录树…");
    window._treeRunning = true; window._treeDone = false;
    document.getElementById("treeSummary").style.display = "none";
    document.getElementById("treeBreadcrumb").style.display = "none";
    document.getElementById("recCard").style.display = "none";
    document.getElementById("treeBody").innerHTML =
        '<tr><td colspan="7" class="empty-state">扫描中…</td></tr>';
    document.getElementById("largeFilesBody").innerHTML =
        '<tr><td colspan="7" class="empty-state">扫描中…</td></tr>';
    document.getElementById("treeNote").textContent = "";
    document.getElementById("largeNote").textContent = "";

    // 仪表盘卡片内同步显示扫描进度（此前进度只显示在收纳的目录树卡片中，用户无感知）
    const tm = document.getElementById("treemap");
    if (tm) tm.innerHTML =
        '<div class="empty-state-full" style="padding-top:170px">' +
        '<div class="loader-ring" style="margin:0 auto 14px"></div>' +
        '<p id="tmSpinText">正在扫描目录树（全盘约需1-3分钟）…</p></div>';
    const tmStart = document.getElementById("tmStartBtn");
    if (tmStart) tmStart.style.display = "none";

    apiFetch(`/api/disk/scan?type=tree&root=${encodeURIComponent(root)}&min_mb=${minMb}`)
        .then(data => {
            if (!data.success) {
                showLargeProgress(false);
                btn.disabled = false;
                window._treeRunning = false;
                tmShowError(data.error || "扫描启动失败");
                showError(data.error || "扫描启动失败");
                return;
            }
            treeScanId = data.scan_id;
            clearInterval(largePollTimer);
            largePollTimer = setInterval(pollLargeScan, 800);
        })
        .catch(e => {
            showLargeProgress(false);
            btn.disabled = false;
            window._treeRunning = false;
            tmShowError("扫描请求失败: " + e.message);
            showError("扫描请求失败: " + e.message);
        });
}

function pollLargeScan() {
    apiFetch(`/api/disk/scan-status?scan_id=${treeScanId}`).then(data => {
        if (!data.success) { clearInterval(largePollTimer); return; }
        const t = data.task;

        if (t.status === "running") {
            const p = t.progress || {};
            setLargeProgressText(
                `已扫描 ${fmtNum(p.scanned_files || 0)} 个文件 · ${fmtNum(p.dirs || 0)} 个目录 · ` +
                `耗时 ${p.elapsed || 0}s · 当前: ${shortPath(p.current_dir || "", 50)}`
            );
            // 仪表盘内嵌实时进度
            const spin = document.getElementById("tmSpinText");
            if (spin) spin.textContent =
                `正在扫描: ${shortPath(p.current_dir || "", 44)} · 已 ${fmtNum(p.scanned_files || 0)} 文件 · ${p.elapsed || 0}s`;
        } else {
            clearInterval(largePollTimer);
            showLargeProgress(false);
            document.getElementById("largeScanBtn").disabled = false;
            document.getElementById("largeCancelBtn").style.display = "none";
            window._treeRunning = false;

            if (t.status === "done" && t.result && t.result.success !== false) {
                window._treeDone = true;
                const r = t.result;
                renderRecommendations(r);
                const sum = document.getElementById("treeSummary");
                sum.style.display = "block";
                const skSamples = (r.skipped_samples && r.skipped_samples.length)
                    ? `（如 ${r.skipped_samples.slice(0, 3).map(escapeHtml).join("、")}…）` : "";
                sum.innerHTML =
                    `📂 <strong>${escapeHtml(r.root_path)}</strong> — ` +
                    `总容量 <strong style="color:var(--accent-cyan)">${r.total_size_text}</strong> · ` +
                    `${fmtNum(r.total_files)} 文件 · ${fmtNum(r.total_dirs)} 目录 · 耗时 ${r.elapsed}s` +
                    (r.skipped_dirs ? ` · ${r.skipped_dirs} 个无权限目录已计入统计${skSamples}` : " · 全量统计");
                renderLargeFiles(r);
                loadTreeNode(r.root_path);
            } else if (t.status === "cancelled") {
                document.getElementById("treeBody").innerHTML =
                    '<tr><td colspan="7" class="empty-state">扫描已取消</td></tr>';
                tmShowError("扫描已取消，可点击重试");
            } else if (t.status === "error") {
                tmShowError("扫描失败: " + (t.error || "未知错误"));
                showError("扫描失败: " + (t.error || "未知错误"));
            } else if (t.result && t.result.error) {
                tmShowError(t.result.error);
                showError(t.result.error);
            }
        }
    }).catch(() => {});
}

// ---- 树节点下钻与渲染 ----
function loadTreeNode(path) {
    apiFetch(`/api/disk/tree?scan_id=${treeScanId}&path=${encodeURIComponent(path)}`)
        .then(data => {
            if (!data.success) {
                tmShowError(data.error || "加载失败");
                showError(data.error || "加载失败");
                return;
            }
            treeCache = data;
            window._tmCurrent = path;
            renderTreeBreadcrumb(data);
            renderTreeChildren();
            if (typeof renderTreemap === "function") renderTreemap();
        })
        .catch(e => {
            tmShowError("加载目录树失败: " + e.message);
            showError("加载目录树失败: " + e.message);
        });
}

function renderTreeBreadcrumb(data) {
    const bc = document.getElementById("treeBreadcrumb");
    bc.style.display = "flex";
    const rootPath = (window._treeRoot || "C:\\").replace(/[\\/]$/, "");
    const rel = data.rel || "";
    let html = `<span class="crumb" onclick="loadTreeNode('${escapeJs(rootPath)}')">${escapeHtml(rootPath)}</span>`;
    if (rel && rel !== ".") {
        let acc = rootPath;
        rel.split(/[\\/]/).filter(Boolean).forEach(seg => {
            acc = acc + "\\" + seg;
            html += ` <span class="crumb-sep">›</span> <span class="crumb" onclick="loadTreeNode('${escapeJs(acc)}')">${escapeHtml(seg)}</span>`;
        });
    }
    bc.innerHTML = html;
}

function renderTreeChildren() {
    if (!treeCache) return;
    const tbody = document.getElementById("treeBody");
    const sortKey = document.getElementById("largeSort").value;
    const children = [...(treeCache.children || [])];
    children.sort(sortKey === "files"
        ? (a, b) => b.files - a.files
        : (a, b) => b.size - a.size);

    if (children.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">该目录下没有子目录</td></tr>';
    } else {
        tbody.innerHTML = children.map(c => `
            <tr class="${c.small_heavy ? "row-small-heavy" : ""}">
                <td><span class="tree-dir-name" onclick="loadTreeNode('${escapeJs(c.path)}')" title="${escapeHtml(c.path)}">📁 ${escapeHtml(truncate(c.name, 40))}</span></td>
                <td style="white-space:nowrap"><strong>${c.size_text}</strong></td>
                <td style="white-space:nowrap">${fmtNum(c.files)}</td>
                <td style="white-space:nowrap">${fmtNum(c.dirs)}</td>
                <td style="white-space:nowrap;font-size:12px;color:var(--text-secondary)">${c.avg_text}</td>
                <td style="white-space:nowrap">${c.small_heavy
                    ? '<span class="type-badge tb-warn" title="文件数≥300且均值≤256KB，多为日志/缓存，清理效率高">海量小文件</span>' : ""}</td>
                <td style="white-space:nowrap">
                    <button class="btn btn-mini btn-ghost" onclick="loadTreeNode('${escapeJs(c.path)}')">进入</button>
                    <button class="btn btn-mini btn-ghost" onclick="openLocation('${escapeJs(c.path)}')">位置</button>
                </td>
            </tr>
        `).join("");
    }

    const loose = treeCache.loose_files || [];
    document.getElementById("treeNote").textContent =
        `本层子目录共 ${treeCache.children_total} 个，已全部列出` +
        (loose.length
            ? ` · 散落文件 ${loose.length} 个（最大: ${loose[0].name} ${loose[0].size_text}），可通过「位置」在资源管理器中处理。`
            : "");
}

// ---- 智能推荐 TOP10 分析视图 ----
function renderRecommendations(result) {
    const card = document.getElementById("recCard");
    const recs = result.recommendations || [];
    if (!recs.length) { card.style.display = "none"; return; }

    const totalFreed = recs.reduce((a, r) => a + r.size, 0);
    document.getElementById("recBadge").textContent =
        `${recs.length} 个目录 · 预计可释放 ${humanSizeRaw(totalFreed)}`;
    document.getElementById("recNote").textContent = "ℹ️ " + (result.recommend_note || "");

    document.getElementById("recBody").innerHTML = recs.map(r => {
        const confCls = r.confidence_text === "高" ? "tb-safe" :
                        r.confidence_text === "中" ? "tb-warn" : "tb-danger";
        const rankCls = r.rank <= 3 ? "rec-rank hot" : "rec-rank";
        return `
        <tr>
            <td><span class="${rankCls}">${r.rank}</span></td>
            <td title="${escapeHtml(r.path)}" style="max-width:260px">
                <span class="tree-dir-name" onclick="loadTreeNode('${escapeJs(r.path)}')">${escapeHtml(shortPath(r.rel || r.path, 40))}</span>
                ${r.small_heavy ? '<span class="type-badge tb-warn" style="margin-left:4px">海量小文件</span>' : ""}
            </td>
            <td style="white-space:nowrap"><strong>${r.size_text}</strong></td>
            <td style="white-space:nowrap">${fmtNum(r.files)}</td>
            <td style="white-space:nowrap;font-size:12px">${r.category}</td>
            <td style="white-space:nowrap"><span class="type-badge ${confCls}">${r.confidence_text} (${Math.round(r.confidence * 100)}%)</span></td>
            <td style="font-size:12px;max-width:200px">${escapeHtml(r.reason || "")}</td>
            <td style="font-size:12px;max-width:180px">${escapeHtml(r.advice)}</td>
            <td style="white-space:nowrap">
                <button class="btn btn-mini btn-ghost" onclick="loadTreeNode('${escapeJs(r.path)}')">下钻</button>
                <button class="btn btn-mini btn-ghost" onclick="openLocation('${escapeJs(r.path)}')">位置</button>
            </td>
        </tr>`;
    }).join("");
    card.style.display = "";
}

function humanSizeRaw(n) {
    n = parseFloat(n || 0);
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
}

async function cancelLargeScan() {
    if (!treeScanId) return;
    try { await apiFetch(`/api/disk/scan-cancel?scan_id=${treeScanId}`); } catch (e) { /* 忽略 */ }
}

function renderLargeFiles(result) {
    window._treeRoot = result.root_path;
    if (treeCache) treeCache._root = result.root_path;
    const tbody = document.getElementById("largeFilesBody");
    const files = result.top_files || [];

    document.getElementById("largeNote").textContent =
        `≥${result.min_mb}MB 的大文件 TOP${files.length}（范围: ${result.root_path}）`;

    if (files.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">未发现符合阈值的大文件</td></tr>';
        return;
    }

    tbody.innerHTML = files.map(f => `
        <tr>
            <td style="white-space:nowrap"><strong>${f.size_text}</strong></td>
            <td style="white-space:nowrap"><span class="type-badge tb-${f.level}">${f.type}</span></td>
            <td style="white-space:nowrap;font-size:12px;color:var(--text-secondary)">${f.modified}</td>
            <td style="white-space:nowrap;font-size:12px">${f.location}</td>
            <td style="font-size:12px;max-width:220px">${f.advice}</td>
            <td title="${escapeHtml(f.path)}" style="font-size:12px;color:var(--text-secondary)">${escapeHtml(shortPath(f.path, 46))}</td>
            <td><button class="btn btn-mini btn-ghost" onclick="openLocation('${escapeJs(f.path)}')">打开位置</button></td>
        </tr>
    `).join("");
}

async function openLocation(path) {
    try {
        const data = await apiFetch(`/api/disk/open-location?path=${encodeURIComponent(path)}`);
        if (!data.success) showError(data.error || "定位失败");
    } catch (e) {
        showError("定位请求失败: " + e.message);
    }
}

// ===================== UI 辅助 =====================
function showJunkProgress(show, text) {
    const wrap = document.getElementById("junkProgressWrap");
    wrap.style.display = show ? "flex" : "none";
    if (text) document.getElementById("junkProgressText").textContent = text;
    if (!show) {
        const bar = document.getElementById("junkProgressBar");
        bar.style.width = "0";
        bar.classList.remove("indeterminate");
    }
}

function setJunkProgress(pct, text) {
    const bar = document.getElementById("junkProgressBar");
    if (pct !== null && pct !== undefined) {
        bar.classList.remove("indeterminate");
        bar.style.width = pct + "%";
    }
    document.getElementById("junkProgressText").textContent = text;
}

function showLargeProgress(show, text) {
    const wrap = document.getElementById("largeProgressWrap");
    wrap.style.display = show ? "flex" : "none";
    if (text) setLargeProgressText(text);
}

function setLargeProgressText(text) {
    document.getElementById("largeProgressText").textContent = text;
}

function fmtNum(n) {
    return (n || 0).toLocaleString("zh-CN");
}

function shortPath(p, max) {
    max = max || 40;
    if (!p) return "";
    if (p.length <= max) return p;
    return p.substring(0, 10) + "…" + p.substring(p.length - (max - 12));
}

function escapeJs(s) {
    return String(s).replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, "&quot;");
}

function showToast(msg) {
    const div = document.createElement("div");
    div.style.cssText = `
        position:fixed; bottom:24px; right:24px; z-index:999;
        padding:14px 20px; background:#2e7d32; color:#fff;
        border-radius:8px; font-size:14px; font-weight:500;
        box-shadow:0 4px 20px rgba(46,125,50,.4); max-width:420px;
    `;
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(() => { div.style.opacity = "0"; div.style.transition = "opacity .4s";
        setTimeout(() => div.remove(), 400); }, 4500);
}

/* ==========================================
   C盘文件夹容量分析仪表盘（矩形分块可视化）
   - 数据源: 容量定位分析的目录树扫描结果
   - 布局: squarified treemap
   - 配色: 占用越大背景越深（渐变），占用越小越浅
   - 交互: 点击色块下钻 / 上一级返回 / 与目录树面包屑联动
   ========================================== */

/** 从「容量定位分析」的扫描结果生成仪表盘 */
function startTreeForTreemap() {
    if (window._treeRunning) {
        document.getElementById("tmNote").textContent =
            "目录树扫描进行中，完成后将自动生成仪表盘，请稍候…";
        return;
    }
    if (window._treeDone && treeScanId && window._treeRoot) {
        loadTreeNode(window._treeRoot);
        return;
    }
    // 未扫描: 使用默认 C:\ 启动目录树扫描
    const sel = document.getElementById("largeScope");
    if (sel) {
        const opt = [...sel.options].find(o => o.value === "C:\\");
        if (opt) sel.value = "C:\\";
    }
    startLargeScan();
}

/** 仪表盘区域内的错误/状态展示 */
function tmShowError(msg) {
    const tm = document.getElementById("treemap");
    if (tm) tm.innerHTML =
        '<div class="empty-state-full">❌ ' + escapeHtml(msg) +
        '<br><br><button class="btn btn-primary btn-mini" onclick="startTreeForTreemap()">重试</button></div>';
    const tmStart = document.getElementById("tmStartBtn");
    if (tmStart) tmStart.style.display = "";
}

function renderTreemap() {
    const el = document.getElementById("treemap");
    if (!el || !treeCache) return;

    const tmStart = document.getElementById("tmStartBtn");
    if (tmStart) tmStart.style.display = "none";
    const upBtn = document.getElementById("tmUpBtn");
    const atRoot = !treeCache.rel || treeCache.rel === ".";
    if (upBtn) upBtn.style.display = atRoot ? "none" : "";
    const pathText = document.getElementById("tmPathText");
    if (pathText) pathText.textContent = "当前: " + (window._tmCurrent || window._treeRoot || "");

    // 数据准备: 子目录按容量排序，取TOP15，其余合并为「其他」
    const children = (treeCache.children || []).filter(c => c.size > 0);
    children.sort((a, b) => b.size - a.size);
    const TOPN = 15;
    const top = children.slice(0, TOPN);
    const rest = children.slice(TOPN);
    const data = top.map(c => ({
        name: c.name, path: c.path, size: c.size, value: c.size,
        size_text: c.size_text, files: c.files, isOther: false,
    }));
    if (rest.length) {
        const rs = rest.reduce((a, c) => a + c.size, 0);
        data.push({
            name: `其他（${rest.length} 个目录）`, path: null, size: rs, value: rs,
            size_text: humanSizeRaw(rs), files: rest.reduce((a, c) => a + c.files, 0),
            isOther: true,
        });
    }
    // 容量守恒视图：根目录散落系统文件（pagefile/hiberfil）+ 权限受限残差
    if (atRoot) {
        (treeCache.loose_files || []).slice(0, 6).forEach(f => {
            if (f.size > 0) data.push({
                name: f.name, path: f.path, size: f.size, value: f.size,
                size_text: f.size_text, files: 1, isFile: true,
            });
        });
        const accounted = data.reduce((a, d) => a + d.size, 0);
        const residual = (window._diskUsedBytes || 0) - accounted;
        if (residual > 100 * 1024 * 1024) data.push({
            name: "权限受限/系统保护", path: null, size: residual, value: residual,
            size_text: "≈ " + humanSizeRaw(residual), files: -1, isUnknown: true,
        });
    }

    if (!data.length) {
        el.innerHTML = '<div class="empty-state-full">该目录下没有可展示的子目录</div>';
        return;
    }

    // 布局
    const wrapW = el.clientWidth || el.offsetWidth || 900;
    const wrapH = 460;
    el.style.height = wrapH + "px";
    const rects = squarifyLayout(data, wrapW, wrapH);
    const maxSize = Math.max(...data.map(d => d.size), 1);

    el.innerHTML = "";
    rects.forEach(rc => {
        const d = rc.item;
        const ratio = d.size / maxSize;
        let col = blockColors(ratio);
        if (d.isUnknown) col = { g1: "rgb(90,98,112)", g2: "rgb(58,64,76)", fg: "#e8eaed" };
        if (d.isFile) col = { g1: "rgb(146,120,72)", g2: "rgb(96,78,44)", fg: "#fdf3dd" };
        const div = document.createElement("div");
        div.className = "tm-block" + (d.isOther ? " tm-other" : "") + (d.isUnknown ? " tm-unknown" : "");
        div.style.left = Math.round(rc.x) + "px";
        div.style.top = Math.round(rc.y) + "px";
        div.style.width = Math.round(rc.w) + "px";
        div.style.height = Math.round(rc.h) + "px";
        div.style.background = `linear-gradient(135deg, ${col.g2}, ${col.g1})`;
        div.style.color = col.fg;
        div.title = `${d.name}\n路径: ${d.path || "（聚合视图）"}\n容量: ${d.size_text} · ${fmtNum(d.files || 0)} 个文件`;
        if (!d.isOther && !d.isUnknown && !d.isFile) div.onclick = () => loadTreeNode(d.path);

        // 文字自适应: 块足够大才显示名称/容量/路径
        if (rc.w > 76 && rc.h > 44) {
            let html = `<div class="tm-name">${escapeHtml(truncate(d.name, Math.floor(rc.w / 9)))}</div>` +
                       `<div class="tm-size">${d.size_text}</div>`;
            if (rc.h > 78 && rc.w > 150 && d.path) {
                html += `<div class="tm-path">${escapeHtml(shortPath(d.path, Math.floor(rc.w / 7)))}</div>`;
            }
            div.innerHTML = html;
        } else if (rc.w > 40 && rc.h > 22) {
            div.innerHTML = `<div class="tm-name" style="font-size:10px">${escapeHtml(truncate(d.name, Math.floor(rc.w / 7)))}</div>`;
        }
        el.appendChild(div);
    });
}

/** squarified treemap 布局算法 */
function squarifyLayout(data, W, H) {
    const rects = [];
    let list = data.slice();
    let x = 0, y = 0, w = W, h = H;

    while (list.length) {
        const total = list.reduce((s, c) => s + c.value, 0);
        if (total <= 0 || w <= 0 || h <= 0) break;
        const scale = (w * h) / total;
        const shortSide = Math.min(w, h);

        // 贪心构建一行: 逐个尝试加入，宽高比变差即停止
        let row = [], rowArea = 0, worst = Infinity, i = 0;
        while (i < list.length) {
            const area = list[i].value * scale;
            if (row.length === 0) {
                row.push(i); rowArea = area;
                const thick0 = rowArea / shortSide;
                const len0 = area / thick0;
                worst = Math.max(thick0 / len0, len0 / thick0);
                i++; continue;
            }
            const newRowArea = rowArea + area;
            const thick = newRowArea / shortSide;
            let w2 = 0;
            for (const idx of row) {
                const a2 = list[idx].value * scale;
                const len2 = a2 / thick;
                w2 = Math.max(w2, thick / len2, len2 / thick);
            }
            const lenC = area / thick;
            w2 = Math.max(w2, thick / lenC, lenC / thick);
            if (w2 > worst) break;
            row.push(i); rowArea = newRowArea; worst = w2;
            i++;
        }

        const thick = rowArea / shortSide;
        let off = 0;
        for (const idx of row) {
            const len = (list[idx].value * scale) / thick;
            let r;
            if (w >= h) r = { x: x, y: y + off, w: thick, h: len };
            else r = { x: x + off, y: y, w: len, h: thick };
            r.item = list[idx];
            rects.push(r);
            off += len;
        }
        if (w >= h) { x += thick; w -= thick; }
        else { y += thick; h -= thick; }
        list = list.slice(row.length);
    }
    return rects;
}

/** 配色: 占用比例 → 深浅渐变背景 + 自适应文字颜色 */
function blockColors(ratio) {
    const t = Math.max(0, Math.min(1, Math.pow(ratio, 0.45)));
    const c0 = [214, 238, 250];  // 小: 极浅蓝
    const c1 = [77, 166, 216];   // 中: 天蓝
    const c2 = [13, 71, 127];    // 大: 深海蓝
    let c;
    if (t < 0.5) {
        const k = t / 0.5;
        c = c0.map((v, i) => Math.round(v + (c1[i] - v) * k));
    } else {
        const k = (t - 0.5) / 0.5;
        c = c1.map((v, i) => Math.round(v + (c2[i] - v) * k));
    }
    // 每个色块内部使用同色系深→浅渐变
    const g1 = c.map(v => Math.max(0, Math.round(v * 0.8)));
    const g2 = c.map(v => Math.min(255, Math.round(v * 1.1 + 10)));
    const fg = t > 0.42 ? "#f4faff" : "#0c2740";
    return { g1: `rgb(${g1.join(",")})`, g2: `rgb(${g2.join(",")})`, fg };
}

/** 仪表盘返回上一级 */
function treemapUp() {
    const cur = window._tmCurrent || window._treeRoot;
    if (!cur) return;
    const parts = cur.replace(/[\\/]+$/, "").split(/[\\/]/);
    if (parts.length <= 1) return;  // 已在根
    parts.pop();
    const parent = parts.join("\\") + (parts.length === 1 ? "\\" : "");
    loadTreeNode(parent);
}

// 窗体尺寸变化时重排仪表盘（色块按渲染时宽度布局，需随窗体自适应）
window.addEventListener("resize", function () {
    clearTimeout(window._tmResizeTimer);
    window._tmResizeTimer = setTimeout(function () {
        var el = document.getElementById("treemap");
        if (el && treeCache && el.clientWidth > 50 && el.offsetParent !== null) {
            renderTreemap();
        }
    }, 150);
});
