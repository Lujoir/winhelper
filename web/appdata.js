/* ==========================================
   应用数据盘点与迁移 - 前端逻辑
   分级展示: 区域卡片 → 分类表 → 大文件TOP25
   辅助功能: 迁移到数据盘(推荐) / 谨慎删除 / 打开位置
   ========================================== */

let adScanId = null;
let adPollTimer = null;
let adJobId = null;          // 迁移/删除任务
let adJobKind = null;
let adJobPollTimer = null;
let adTabInited = false;
let adData = null;           // 扫描结果
let adSelectedZone = null;
let adSelectedCats = new Set();  // 勾选的迁移分类

// ===================== Tab 接入 =====================
function initAppdataTab() {
    if (adTabInited) return;
    adTabInited = true;
    loadAdDrives();
}

// ===================== 扫描 =====================
function startAppdataScan() {
    const btn = document.getElementById("adScanBtn");
    btn.disabled = true;
    showAdProgress(true, "正在盘点应用数据…");
    document.getElementById("adDetail").style.display = "none";

    apiFetch("/api/appdata/scan").then(data => {
        if (!data.success) {
            showAdProgress(false);
            btn.disabled = false;
            showError(data.error || "盘点启动失败");
            return;
        }
        adScanId = data.scan_id;
        clearInterval(adPollTimer);
        adPollTimer = setInterval(pollAppdataScan, 800);
    }).catch(e => {
        showAdProgress(false);
        btn.disabled = false;
        showError("盘点请求失败: " + e.message);
    });
}

function pollAppdataScan() {
    apiFetch(`/api/disk/scan-status?scan_id=${adScanId}`).then(data => {
        if (!data.success) { clearInterval(adPollTimer); return; }
        const t = data.task;

        if (t.status === "running") {
            const p = t.progress || {};
            const pct = p.total ? Math.round((p.done / p.total) * 100) : 5;
            setAdProgress(pct, `正在分析: ${p.current || "…"} (${p.done || 0}/${p.total || "?"})`);
        } else {
            clearInterval(adPollTimer);
            showAdProgress(false);
            document.getElementById("adScanBtn").disabled = false;

            if (t.status === "done" && t.result) {
                adData = t.result;
                document.getElementById("adSummaryText").textContent =
                    `共发现 ${adData.total_size_text} 应用数据`;
                renderAdZones(adData.zones);
            } else if (t.status === "error") {
                showError("盘点失败: " + (t.error || "未知错误"));
            }
        }
    }).catch(() => {});
}

function renderAdZones(zones) {
    const grid = document.getElementById("adZoneGrid");
    grid.innerHTML = zones.map(z => {
        const active = adSelectedZone === z.key ? "ad-zone-active" : "";
        const tiers = z.tiers || {};
        return `
        <div class="junk-card ad-zone-card ${active} ${z.found ? "" : "junk-empty"}" onclick="selectAdZone('${z.key}')">
            <div class="junk-info">
                <div class="junk-name">${z.name}
                    ${z.found ? "" : '<span class="risk-badge risk-safe">未检测到</span>'}
                </div>
                <div class="junk-desc">${z.found ? `${z.file_count} 个文件 · 大文件 ${((z.tiers || {}).huge || {}).count || 0} 个≥1GB / ${((z.tiers || {}).large || {}).count || 0} 个500MB+` : "未在本机发现该应用数据目录"}</div>
                <div class="junk-paths">${(z.paths || []).slice(0, 2).map(p => `<span class="junk-path" title="${escapeHtml(p)}">${escapeHtml(shortPath(p, 34))}</span>`).join("")}</div>
            </div>
            <div class="junk-size">
                <div class="junk-size-val">${z.total_size_text}</div>
                <div class="junk-size-cnt">${z.found ? "点击查看" : ""}</div>
            </div>
        </div>`;
    }).join("");
}

function selectAdZone(key) {
    if (!adData) return;
    adSelectedZone = key;
    adSelectedCats.clear();
    renderAdZones(adData.zones);

    const z = adData.zones.find(x => x.key === key);
    if (!z || !z.found) return;

    document.getElementById("adDetail").style.display = "";
    document.getElementById("adZoneTitle").textContent = `${z.name} — 数据分类明细`;
    document.getElementById("adZoneBadge").textContent = `共 ${z.total_size_text} · ${z.file_count} 个文件`;
    document.getElementById("adZoneAdvice").innerHTML = `💡 ${escapeHtml(z.zone_advice)}`;
    renderAdCategories(z);
    renderAdTopFiles(z);
}

function renderAdCategories(z) {
    const tbody = document.getElementById("adCatBody");
    if (!z.categories.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">该区域没有数据</td></tr>';
        return;
    }
    const MAX_ROWS = 15;
    const cats = z.categories.slice(0, MAX_ROWS);
    const more = z.categories.length - MAX_ROWS;
    let moreRow = "";
    if (more > 0) {
        moreRow = `<tr><td colspan="7" class="empty-state" style="padding:8px">其余 ${more} 个更小的分类已省略</td></tr>`;
    }
    tbody.innerHTML = cats.map(c => {
        const riskCls = c.risk === "safe" ? "risk-safe" : c.risk === "info" ? "risk-info" : "risk-caution";
        const riskText = c.risk === "safe" ? "低" : c.risk === "info" ? "中" : "高";
        return `
        <tr>
            <td style="white-space:nowrap">
                ${c.migrate_ok ? `<input type="checkbox" class="ad-cat-check" data-key="${escapeHtml(c.key)}"
                    data-path="${escapeHtml(c.path)}" onchange="adOnCatCheck()" title="勾选后可迁移到数据盘">` : ""}
                <strong>${escapeHtml(c.name)}</strong>
            </td>
            <td style="white-space:nowrap"><strong>${c.size_text}</strong></td>
            <td>${c.count}</td>
            <td><span class="risk-badge ${riskCls}">${riskText}</span></td>
            <td title="${escapeHtml(c.path)}" style="font-size:11px;color:var(--text-secondary);max-width:180px">${escapeHtml(shortPath(c.path, 30))}</td>
            <td style="font-size:12px;max-width:230px">${escapeHtml(c.advice)}</td>
            <td style="white-space:nowrap">
                <button class="btn btn-mini btn-ghost" onclick="adOpenLoc('${escapeJs(c.path)}')">位置</button>
                <button class="btn btn-mini btn-ghost" style="color:var(--color-critical);border-color:rgba(255,82,82,.4)"
                    onclick="adDeleteCat('${escapeJs(c.key)}', '${escapeJs(c.path)}', ${c.count}, '${c.size_text}', '${escapeJs(c.name)}')">删除</button>
            </td>
        </tr>`;
    }).join("") + moreRow;
}

function renderAdTopFiles(z) {
    const tbody = document.getElementById("adTopBody");
    const files = z.top_files || [];
    if (!files.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state">无文件</td></tr>';
        return;
    }
    tbody.innerHTML = files.map(f => {
        let tier = "小", tierCls = "tb-safe";
        if (f.size >= 1024 * 1024 * 1024) { tier = "≥1GB"; tierCls = "tb-danger"; }
        else if (f.size >= 500 * 1024 * 1024) { tier = "大"; tierCls = "tb-warn"; }
        else if (f.size >= 100 * 1024 * 1024) { tier = "中"; tierCls = "tb-info"; }
        return `
        <tr>
            <td><span class="type-badge ${tierCls}">${tier}</span></td>
            <td style="white-space:nowrap"><strong>${f.size_text}</strong></td>
            <td style="white-space:nowrap;font-size:12px;color:var(--text-secondary)">${f.modified}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</td>
            <td title="${escapeHtml(f.path)}" style="font-size:11px;color:var(--text-secondary);max-width:200px">${escapeHtml(shortPath(f.path, 36))}</td>
            <td style="white-space:nowrap">
                <button class="btn btn-mini btn-ghost" onclick="adOpenLoc('${escapeJs(f.path)}')">位置</button>
                <button class="btn btn-mini btn-ghost" style="color:var(--color-critical);border-color:rgba(255,82,82,.4)"
                    onclick="adDeleteFiles(['${escapeJs(f.path)}'], 1, '${f.size_text}')">删除</button>
            </td>
        </tr>`;
    }).join("");
}

// ===================== 迁移 =====================
function loadAdDrives() {
    apiFetch("/api/appdata/drives").then(data => {
        const sel = document.getElementById("adDriveSelect");
        if (!data.success || !data.drives.length) {
            sel.innerHTML = '<option value="">未发现其他数据盘</option>';
            return;
        }
        sel.innerHTML = data.drives.map(d =>
            `<option value="${d.drive}">${d.drive} (剩余 ${d.free_text})</option>`).join("");
    }).catch(() => {});
}

function adOnCatCheck() {
    adSelectedCats.clear();
    document.querySelectorAll(".ad-cat-check:checked").forEach(el => {
        adSelectedCats.add(JSON.stringify({ key: el.dataset.key, path: el.dataset.path }));
    });
    const btn = document.getElementById("adMigrateBtn");
    btn.disabled = adSelectedCats.size === 0 || !document.getElementById("adDriveSelect").value;
    document.getElementById("adMigrateHint").textContent = adSelectedCats.size
        ? `已勾选 ${adSelectedCats.size} 个分类，迁移后源数据移动到目标盘（保留相对目录结构），不会误删。`
        : "在上方分类表勾选要迁移的分类（建议: 聊天文件/接收的文件/会议录像），再点击「迁移选中分类」。";
}

async function adMigrateSelected() {
    const drive = document.getElementById("adDriveSelect").value;
    const folder = (document.getElementById("adTargetName").value || "AppDataMigrate").trim();
    if (!drive || adSelectedCats.size === 0) return;

    const cats = [...adSelectedCats].map(s => JSON.parse(s));
    const listText = cats.map(c => "· " + c.key).join("\n");
    if (!(await uiConfirm({
        title: "应用数据迁移确认",
        message: `将以下 ${cats.length} 个分类的数据移动到 ${drive}${folder}:\n${listText}\n\n说明:\n· 迁移是"移动"而非删除，保留相对目录结构\n· 同名文件自动跳过或改名，不会覆盖\n· 聊天媒体迁移后在聊天窗口可能显示为过期\n\n确定开始迁移？`,
        danger: true
    }))) return;

    startAdJob("migrate", cats.map(c =>
        `/api/appdata/migrate?path=${encodeURIComponent(c.path)}&target=${encodeURIComponent(drive + folder + "\\" + c.key.replace(/[\\/:*?"<>|]/g, "_"))}`),
        `迁移 ${cats.length} 个分类`, () => {
            adSelectedCats.clear();
            startAppdataScan();  // 完成后刷新盘点数据
        });
}

// ===================== 删除辅助 =====================
async function adDeleteCat(catKey, path, count, sizeText, name) {
    if (!(await uiConfirm({
        title: "应用数据删除确认（不可恢复）",
        message: `目标: ${name}\n大小: ${sizeText} · ${count} 个文件\n\n⚠️ 数据文件删除后无法从回收站找回，\n如需保留请改用「迁移」功能。\n\n确定要永久删除该分类全部文件吗？`,
        danger: true
    }))) return;
    startAdJob("delete", [`/api/appdata/delete?paths=${encodeURIComponent(path)}`],
        `删除 ${name}`, () => startAppdataScan());
}

async function adDeleteFiles(paths, count, sizeText) {
    if (!(await uiConfirm({
        title: "应用数据删除确认（不可恢复）",
        message: `将永久删除 ${count} 个文件 (共 ${sizeText})。\n\n确定继续？`,
        danger: true
    }))) return;
    startAdJob("delete", [`/api/appdata/delete?paths=${paths.map(encodeURIComponent).join("|")}`],
        `删除 ${count} 个文件`, () => startAppdataScan());
}

// ===================== 通用任务执行(迁移/删除共用) =====================
function startAdJob(kind, endpoints, title, onDone) {
    const results = [];
    let done = 0;
    showAdProgress(true, `${title}…`);

    endpoints.forEach(ep => {
        apiFetch(ep).then(data => {
            results.push(data);
        }).catch(e => {
            results.push({ success: false, error: e.message });
        }).finally(() => {
            done++;
            if (done === endpoints.length) {
                const ok = results.filter(r => r.success && r.job_id);
                if (ok.length === 0) {
                    showAdProgress(false);
                    showError(results[0] && results[0].error || "任务启动失败");
                    return;
                }
                // 逐个等待任务完成（顺序执行避免磁盘IO争抢）
                runSequentially(ok.map(r => r.job_id), 0, title, onDone);
            }
        });
    });
}

function runSequentially(jobIds, idx, title, onDone) {
    if (idx >= jobIds.length) {
        showAdProgress(false);
        onDone && onDone();
        return;
    }
    adJobId = jobIds[idx];
    clearInterval(adJobPollTimer);
    adJobPollTimer = setInterval(() => {
        apiFetch(`/api/disk/scan-status?scan_id=${adJobId}`).then(data => {
            if (!data.success) { clearInterval(adJobPollTimer); return; }
            const t = data.task;
            if (t.status === "running") {
                const p = t.progress || {};
                const pct = p.total ? Math.round((p.done / p.total) * 100) : 5;
                setAdProgress(pct, `${title}: ${p.current || "…"} (${p.done || 0}/${p.total || "?"})`);
            } else {
                clearInterval(adJobPollTimer);
                if (t.status === "done" && t.result) {
                    const r = t.result;
                    if (r.success === false) {
                        showError(r.error || "任务失败");
                    } else if (adJobKind !== null || true) {
                        if (r.moved !== undefined) {
                            showToast(`✅ 迁移完成: 移动 ${r.moved} 个文件，释放C盘 ${r.freed_text}${r.failed ? `，跳过占用 ${r.failed}` : ""}`);
                        } else if (r.deleted !== undefined) {
                            showToast(`✅ 删除完成: 释放 ${r.freed_text}，删除 ${r.deleted} 项${r.failed ? `，跳过 ${r.failed}` : ""}`);
                        }
                        if (r.hint) console.info(r.hint);
                    }
                } else if (t.status === "cancelled") {
                    showToast("任务已取消");
                } else {
                    showError("任务失败: " + (t.error || "未知错误"));
                }
                runSequentially(jobIds, idx + 1, title, onDone);
            }
        }).catch(() => {});
    }, 800);
}

// ===================== 辅助 =====================
function adOpenLoc(path) {
    apiFetch(`/api/disk/open-location?path=${encodeURIComponent(path)}`)
        .then(d => { if (!d.success) showError(d.error || "定位失败"); })
        .catch(e => showError("定位失败: " + e.message));
}

function showAdProgress(show, text) {
    document.getElementById("adProgressWrap").style.display = show ? "flex" : "none";
    if (text) document.getElementById("adProgressText").textContent = text;
    if (!show) document.getElementById("adProgressBar").style.width = "0";
}

function setAdProgress(pct, text) {
    document.getElementById("adProgressBar").style.width = pct + "%";
    document.getElementById("adProgressText").textContent = text;
}
