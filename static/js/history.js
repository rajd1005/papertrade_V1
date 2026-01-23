// Global cache and data storage
var simResultsCache = {}; 
var allClosedTrades = []; // Global variable to store trades for simulation

// 1. Core Rendering Function (Accepts data from Sync Loop)
function renderClosedTrades(trades) {
    allClosedTrades = trades; // Update global variable
    let filterDate = $('#hist_date').val(); 
    let filterType = $('#hist_filter').val();

    let html = ''; 
    let dayTotal = 0;
    let totalWins = 0;
    let totalLosses = 0;
    let totalPotential = 0;
    let totalCapital = 0; 
    
    let totalSimPnl = 0;
    let hasSimData = false;

    let filtered = trades.filter(t => t.exit_time && t.exit_time.startsWith(filterDate) && (filterType === 'ALL' || getTradeCategory(t) === filterType));
    
    if(filtered.length === 0) {
        html = '<div class="text-center p-4 text-muted">No History for this Date/Filter</div>';
    } else {
        filtered.forEach(t => {
            dayTotal += t.pnl; 
            if(t.pnl > 0) totalWins += t.pnl;
            else totalLosses += t.pnl;
            
            let invested = t.entry_price * t.quantity; 
            totalCapital += invested;

            let color = t.pnl >= 0 ? 'text-success' : 'text-danger';
            let cat = getTradeCategory(t); 
            let badge = getMarkBadge(cat);
            
            // --- Check for Cached Simulation Results ---
            let simData = simResultsCache[t.id];
            
            // 1. Button Visibility
            let btnStyle = simData ? '' : 'display:none;'; 
            
            // 2. Purple P/L Content & Visibility
            let simPnlHtml = '';
            let simPnlStyle = 'display:none;';
            
            if(simData) {
                hasSimData = true;
                totalSimPnl += simData.simulated_pnl;
                let diff = simData.difference;
                let diffClass = diff >= 0 ? 'text-success' : 'text-danger';
                let diffSign = diff >= 0 ? '+' : '';
                
                simPnlHtml = `
                    <span style="color: #6f42c1;">🔮 Sim: ₹${simData.simulated_pnl.toFixed(2)}</span> 
                    <span class="${diffClass} small fw-bold">(${diffSign}${diff.toFixed(2)})</span>
                `;
                simPnlStyle = ''; 
            }

            // --- Potential Profit Logic ---
            let potHtml = '';
            let potTag = ''; 
            
            // Logic: Suppress potential for Direct SL or Not Active
            let isDirectSL = (t.status === 'SL_HIT' && (!t.targets_hit_indices || t.targets_hit_indices.length === 0));
            let isNotActive = (t.status === 'NOT_ACTIVE' || (t.status === 'TIME_EXIT' && t.pnl === 0));

            if (!isDirectSL && !isNotActive) {
                let mh = t.made_high || t.entry_price;
                if(mh < t.exit_price) mh = t.exit_price; 
                let pot = (mh - t.entry_price) * t.quantity;
                
                // --- NEW: Visual Tag for Tracking Status (Virtual SL) ---
                let trackTag = t.virtual_sl_hit ? '🔴' : '';

                if(pot > 0) {
                    totalPotential += pot; 
                    if (t.targets && t.targets.length >= 3) {
                        let badgeStyle = 'badge bg-white text-success border border-success';
                        if (mh >= t.targets[2]) potTag = `<span class="${badgeStyle}" style="font-size:0.65rem;">Pot. T3</span>`;
                        else if (mh >= t.targets[1]) potTag = `<span class="${badgeStyle}" style="font-size:0.65rem;">Pot. T2</span>`;
                        else if (mh >= t.targets[0]) potTag = `<span class="${badgeStyle}" style="font-size:0.65rem;">Pot. T1</span>`;
                    }
                    potHtml = `
                    <div class="mt-2 p-1 rounded bg-light border border-warning border-opacity-25 d-flex justify-content-between align-items-center" style="font-size:0.75rem;">
                        <span class="text-muted">High: <b>${mh.toFixed(2)} ${trackTag}</b></span>
                        <span class="text-success fw-bold">Max Potential: ₹${pot.toFixed(0)}</span>
                    </div>`;
                }
            }

            // --- Status Tags ---
            let statusTag = '';
            let rawStatus = t.status || '';
            
            if (isNotActive) statusTag = '<span class="badge bg-secondary opacity-50" style="font-size:0.65rem;">Not Active</span>';
            else if (isDirectSL) statusTag = '<span class="badge bg-danger" style="font-size:0.65rem;">Stop-Loss</span>';
            else if (rawStatus === 'SL_HIT') statusTag = '<span class="badge bg-danger" style="font-size:0.65rem;">SL (After Tgt)</span>';
            else if (rawStatus.includes('TARGET')) {
                    let maxHit = -1;
                    if (t.targets_hit_indices && t.targets_hit_indices.length > 0) maxHit = Math.max(...t.targets_hit_indices);
                    else {
                        if(rawStatus.includes('1')) maxHit = 0;
                        if(rawStatus.includes('2')) maxHit = 1;
                        if(rawStatus.includes('3')) maxHit = 2;
                    }
                    if (maxHit === 0) statusTag = '<span class="badge bg-success" style="font-size:0.65rem;">T1 Hit</span>';
                    else if (maxHit === 1) statusTag = '<span class="badge bg-success" style="font-size:0.65rem;">T2 Hit</span>';
                    else statusTag = '<span class="badge bg-success" style="font-size:0.65rem;">T3 Hit</span>'; 
            } 
            else if (rawStatus === 'COST_EXIT') statusTag = '<span class="badge bg-warning text-dark" style="font-size:0.65rem;">Cost Exit</span>';
            else statusTag = `<span class="badge bg-secondary" style="font-size:0.65rem;">${rawStatus}</span>`;

            let addedTimeStr = t.entry_time ? t.entry_time.slice(11, 16) : '--:--';
            let activeTimeStr = '--:--';
            let waitDuration = '';
            
            if (t.logs && t.logs.length > 0) {
                let activationLog = t.logs.find(l => l.includes('Order ACTIVATED'));
                if (activationLog) {
                    let match = activationLog.match(/\[(.*?)\]/);
                    if (match && match[1]) {
                        activeTimeStr = match[1].slice(11, 16); 
                        let addedDateObj = new Date(t.entry_time);
                        let activeDateObj = new Date(match[1]);
                        if(addedDateObj && activeDateObj) {
                            let diff = activeDateObj - addedDateObj; 
                            if(diff > 0) {
                                let totalSecs = Math.floor(diff / 1000);
                                let m = Math.floor(totalSecs / 60);
                                let s = totalSecs % 60;
                                waitDuration = `<span class="text-muted ms-1" style="font-size:0.65rem;">(${m}m ${s}s)</span>`;
                            }
                        }
                    }
                } else {
                    let firstLog = t.logs[0] || "";
                    if (firstLog.includes("Status: OPEN")) {
                        activeTimeStr = addedTimeStr;
                        waitDuration = `<span class="text-muted ms-1" style="font-size:0.65rem;">(Instant)</span>`;
                    }
                }
            }

            // --- Buttons ---
            let editBtn = (t.order_type === 'SIMULATION') ? `<button class="btn btn-sm btn-outline-primary py-0 px-2" style="font-size:0.75rem;" onclick="editSim('${t.id}')">✏️</button>` : '';
            let delBtn = `<button class="btn btn-sm btn-outline-danger py-0 px-2" style="font-size:0.75rem;" onclick="deleteTrade('${t.id}')">🗑️</button>`;
            let simLogBtn = `<button id="btn-sim-log-${t.id}" class="btn btn-sm btn-light border text-primary py-0 px-2" style="${btnStyle} font-size:0.75rem;" onclick="showSimLogs('${t.id}')">🧪 Logs</button>`;
            
            let telegramBtn = `<button class="btn btn-sm btn-outline-info py-0 px-2" style="font-size:0.75rem;" title="Send Trade Status to Telegram" onclick="sendTradeReport('${t.id}')">📢</button>`;

            html += `
            <div class="card mb-2 shadow-sm border-0" id="card-${t.id}">
                <div class="card-body p-2">
                    <div class="d-flex justify-content-between align-items-start mb-1">
                        <div>
                            <span class="fw-bold text-dark h6 m-0">${t.symbol}</span>
                            <div class="mt-1 d-flex gap-1 align-items-center flex-wrap">
                                ${badge} ${statusTag} ${potTag}
                                <span class="badge bg-info text-dark" id="sim-badge-${t.id}" style="display:none; font-size:0.65rem;">Simulating...</span>
                            </div>
                        </div>
                        <div class="text-end">
                            <div class="fw-bold h6 m-0 ${color}">${t.pnl.toFixed(2)}</div>
                            <div class="small fw-bold" id="sim-pnl-${t.id}" style="${simPnlStyle} color: #6f42c1;">
                                ${simPnlHtml}
                            </div> 
                        </div>
                    </div>
                    <hr class="my-1 text-muted opacity-25">
                    <div class="row g-0 text-center mt-2" style="font-size:0.75rem;">
                        <div class="col-3 border-end">
                            <div class="text-muted small">Qty</div>
                            <div class="fw-bold text-dark">${t.quantity}</div>
                        </div>
                        <div class="col-3 border-end">
                            <div class="text-muted small">Entry</div>
                            <div class="fw-bold text-dark">${t.entry_price.toFixed(2)}</div>
                        </div>
                        <div class="col-3 border-end">
                            <div class="text-muted small">LTP</div>
                            <div class="fw-bold text-dark">${t.current_ltp ? t.current_ltp.toFixed(2) : t.exit_price.toFixed(2)}</div>
                        </div>
                        <div class="col-3">
                            <div class="text-muted small">Fund</div>
                            <div class="fw-bold text-dark">₹${(invested/1000).toFixed(1)}k</div>
                        </div>
                    </div>
                    <div class="d-flex justify-content-between align-items-center mt-2 px-1 bg-light rounded py-1" style="font-size:0.75rem;">
                        <span class="text-muted">Added: <b>${addedTimeStr}</b></span>
                        <div class="d-flex align-items-center">
                            <span class="text-primary">Active: <b>${activeTimeStr}</b></span>
                            ${waitDuration}
                        </div>
                    </div>
                    <div class="d-flex justify-content-between align-items-center mt-2 px-1" style="font-size:0.75rem;">
                            <span class="text-danger fw-bold">SL: ${t.sl.toFixed(1)}</span>
                            <span class="text-muted">T: ${t.targets[0].toFixed(0)} | ${t.targets[1].toFixed(0)} | ${t.targets[2].toFixed(0)}</span>
                    </div>
                    ${potHtml}
                    <div class="d-flex justify-content-end gap-2 mt-2 pt-1 border-top border-light">
                        ${editBtn} ${simLogBtn} ${telegramBtn} ${delBtn}
                        <button class="btn btn-sm btn-light border text-muted py-0 px-2" style="font-size:0.75rem;" onclick="showLogs('${t.id}', 'closed')">📜 Logs</button>
                    </div>
                </div>
            </div>`;
        });
    }
    $('#hist-container').html(html); 
    
    // Update Summary Badges
    $('#day_pnl').text("₹ " + dayTotal.toFixed(2));
    if(dayTotal >= 0) $('#day_pnl').removeClass('bg-danger').addClass('bg-success'); else $('#day_pnl').removeClass('bg-success').addClass('bg-danger');

    $('#total_wins').text("Wins: ₹ " + totalWins.toFixed(2));
    $('#total_losses').text("Loss: ₹ " + totalLosses.toFixed(2));
    $('#total_potential').text("Max Potential: ₹ " + totalPotential.toFixed(2));
    $('#total_cap_hist').text("Funds Used: ₹ " + (totalCapital/100000).toFixed(2) + " L");
    
    if(hasSimData) {
        let totalDiff = totalSimPnl - dayTotal;
        let diffSign = totalDiff >= 0 ? '+' : '';
        $('#total_sim_pnl').show().html(`🔮 Sim Total: <b>₹ ${totalSimPnl.toFixed(2)}</b> <span class="small text-muted">(${diffSign}${totalDiff.toFixed(2)})</span>`);
    } else {
        $('#total_sim_pnl').hide();
    }
}

// 2. Fallback function for manual calls or events
function loadClosedTrades() {
    $.get('/api/closed_trades', function(trades) {
        renderClosedTrades(trades);
    });
}

// --- Action Functions ---

function deleteTrade(id) { 
    if(confirm("Delete trade?")) $.post('/api/delete_trade/' + id, r => { if(r.status === 'success') loadClosedTrades(); else alert('Failed to delete'); }); 
}

function editSim(id) {
    let t = allClosedTrades.find(x => x.id == id); if(!t) return;
    $('.dashboard-tab').hide(); $('#history').show(); $('.nav-btn').removeClass('active'); $('.nav-btn').last().addClass('active');
    if(t.raw_params) {
        $('#h_sym').val(t.raw_params.symbol); $('#h_entry').val(t.entry_price); $('#h_qty').val(t.quantity); $('#h_time').val(t.raw_params.time);
        $(`input[name="h_type"][value="${t.raw_params.type}"]`).prop('checked', true);
        loadDetails('#h_sym', '#h_exp', 'input[name="h_type"]:checked', '#h_qty', '#h_sl_pts');
        setTimeout(() => { $('#h_exp').val(t.raw_params.expiry).change(); setTimeout(() => { $('#h_str').val(t.raw_params.strike).change(); }, 500); }, 800);
    } else alert("Old trade format.");
}

function showSimLogs(id) {
    let data = simResultsCache[id];
    if(!data || !data.logs || data.logs.length === 0) return alert("No simulation logs found.");
    $('#logModalBody').html(data.logs.join('<br>'));
    $('#logModal').modal('show');
}

// --- NEW: Telegram Notification Functions ---

function sendTradeReport(tradeId) {
    if(!confirm("Send detailed status of this trade to Telegram?")) return;
    
    $.ajax({
        type: "POST",
        url: '/api/manual_trade_report',
        data: JSON.stringify({ trade_id: tradeId }),
        contentType: "application/json",
        success: function(res) {
            if(res.status === 'success') alert("✅ Trade Report Sent!");
            else alert("❌ Error: " + res.message);
        }
    });
}

function sendManualSummary() {
    let mode = $('#hist_filter').val();
    if(mode === 'ALL') mode = 'PAPER'; 
    
    if(!confirm(`Send ${mode} Daily P/L Summary to Telegram?`)) return;

    $.ajax({
        type: "POST",
        url: '/api/manual_summary',
        data: JSON.stringify({ mode: mode }),
        contentType: "application/json",
        success: function(res) {
            if(res.status === 'success') alert("✅ Summary Sent!");
            else alert("❌ Error: " + res.message);
        }
    });
}

// --- NEW: Send Final Trade Status List ---
function sendManualTradeStatus() {
    let mode = $('#hist_filter').val();
    if(mode === 'ALL') mode = 'PAPER'; 
    
    if(!confirm(`Send ${mode} Final Trade Status List to Telegram?`)) return;

    $.ajax({
        type: "POST",
        url: '/api/manual_trade_status',
        data: JSON.stringify({ mode: mode }),
        contentType: "application/json",
        success: function(res) {
            if(res.status === 'success') alert("✅ Status List Sent!");
            else alert("❌ Error: " + res.message);
        }
    });
}

// --- SCENARIO ANALYSIS LOGIC ---
async function runBatchSimulation() {
    let config = {
        trail_to_entry_t1: $('#sim_trail_t1').is(':checked'),
        exit_multiplier: parseInt($('#sim_exit_mult').val()) || 1,
        target_controls: [
            { lots: $('#sim_f1').is(':checked') ? 1000 : parseInt($('#sim_l1').val()) || 0, enabled: true },
            { lots: $('#sim_f2').is(':checked') ? 1000 : parseInt($('#sim_l2').val()) || 0, enabled: true },
            { lots: $('#sim_f3').is(':checked') ? 1000 : parseInt($('#sim_l3').val()) || 0, enabled: true }
        ]
    };

    let filterDate = $('#hist_date').val();
    let filterType = $('#hist_filter').val();
    let visibleTrades = allClosedTrades.filter(t => t.exit_time && t.exit_time.startsWith(filterDate) && (filterType === 'ALL' || getTradeCategory(t) === filterType));

    if(visibleTrades.length === 0) {
         alert("No visible trades to analyze!");
         return;
    }

    // Reset Cache
    simResultsCache = {};
    $('.sim-result-badge').remove(); 
    $('#total_sim_pnl').hide();
    
    let totalSimPnl = 0;
    let totalOriginalPnl = 0;
    let improvedCount = 0;
    let worsenedCount = 0;
    let processed = 0;
    let count = visibleTrades.length;
    
    $('#sim_results_box').show(); 

    for (let t of visibleTrades) {
        $(`#sim-badge-${t.id}`).show().text("Simulating...");

        try {
            let res = await $.ajax({
                type: "POST",
                url: '/api/simulate_scenario',
                data: JSON.stringify({ trade_id: t.id, config: config }),
                contentType: "application/json"
            });

            if(res.status === 'success') {
                totalSimPnl += res.simulated_pnl;
                totalOriginalPnl += (res.original_pnl || t.pnl);
                
                // Save to Cache
                simResultsCache[t.id] = res;
                $(`#btn-sim-log-${t.id}`).show();

                if (res.simulated_pnl > (res.original_pnl || t.pnl)) improvedCount++;
                if (res.simulated_pnl < (res.original_pnl || t.pnl)) worsenedCount++;

                $(`#sim-badge-${t.id}`).removeClass('bg-info').addClass('bg-warning').text('Simulated');
                
                let diff = res.difference;
                let diffClass = diff >= 0 ? 'text-success' : 'text-danger';
                let diffSign = diff >= 0 ? '+' : '';
                
                $(`#sim-pnl-${t.id}`).show().html(`
                    <span style="color: #6f42c1;">🔮 Sim: ₹${res.simulated_pnl.toFixed(2)}</span> 
                    <span class="${diffClass} small fw-bold">(${diffSign}${diff.toFixed(2)})</span>
                `);
            } else {
                 $(`#sim-badge-${t.id}`).removeClass('bg-info').addClass('bg-danger').text('Error');
            }
        } catch(e) {
            console.error("Sim error", e);
             $(`#sim-badge-${t.id}`).removeClass('bg-info').addClass('bg-danger').text('Fail');
        }

        processed++;
        $('#sim_progress').text(`${processed}/${count}`);
        $('#sim_net_pnl').text("₹ " + totalSimPnl.toFixed(2));
        
        await new Promise(r => setTimeout(r, 100)); 
    }
    
    // Show Final Badge
    let grandDiff = totalSimPnl - totalOriginalPnl;
    let grandDiffSign = grandDiff >= 0 ? '+' : '';
    $('#total_sim_pnl').show().html(`🔮 Sim Total: <b>₹ ${totalSimPnl.toFixed(2)}</b> <span class="small text-muted">(${grandDiffSign}${grandDiff.toFixed(2)})</span>`);
    
    // Batch Summary Modal
    $('#sim_res_total').text("₹ " + totalSimPnl.toFixed(2));
    let diffSign = grandDiff >= 0 ? '+' : '';
    $('#sim_res_diff').text(`${diffSign} ₹ ${grandDiff.toFixed(2)} vs Original`);
    if(grandDiff >= 0) $('#sim_res_diff').addClass('text-success').removeClass('text-danger');
    else $('#sim_res_diff').addClass('text-danger').removeClass('text-success');

    $('#sim_res_improved').text(improvedCount);
    $('#sim_res_worsened').text(worsenedCount);

    $('#simResultModal').modal('show');
}
