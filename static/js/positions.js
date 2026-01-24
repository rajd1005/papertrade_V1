var activeTradesList = [];

// 1. Main Sync Loop
function updateData() {
    // A. Prepare Request
    let payload = {
        include_closed: $('#closed').is(':visible'), // Save bandwidth: only fetch closed if tab is open
        ltp_req: null
    };

    // Check if Import Modal is open (Priority for LTP)
    if ($('#importModal').is(':visible')) {
        let iSym = $('#imp_sym').val();
        let iType = $('input[name="imp_type"]:checked').val();
        if (iSym && iType) {
            payload.ltp_req = {
                symbol: iSym,
                expiry: $('#imp_exp').val(),
                strike: $('#imp_str').val(),
                type: iType
            };
        }
    } 
    // Else check if Main Trade Tab is visible
    else if ($('#trade').is(':visible')) {
        let currentSym = $('#sym').val();
        let tVal = $('input[name="type"]:checked').val();
        if(currentSym && tVal) {
            payload.ltp_req = {
                symbol: currentSym,
                expiry: $('#exp').val(),
                strike: $('#str').val(),
                type: tVal
            };
        }
    }

    // B. Combined API Call
    $.ajax({
        type: "POST",
        url: '/api/sync',
        data: JSON.stringify(payload),
        contentType: "application/json",
        success: function(d) {
            
            // 1. Update Status Badge & Login Button
            let status = d.status || {};
            if (status.state === 'FAILED') {
                let btnHtml = `<a href="${status.login_url}" class="btn btn-sm btn-danger fw-bold shadow-sm py-0" style="font-size: 0.75rem;" target="_blank"><i class="fas fa-key"></i> Manual Login</a>`;
                $('#status-badge').attr('class', 'badge bg-transparent p-0').html(btnHtml);
            } else if (status.active) {
                // Only update if text is different to prevent flickering/redraws
                if ($('#status-badge').text().trim() !== "Connected") {
                    $('#status-badge').attr('class', 'badge bg-success shadow-sm').html('<i class="fas fa-wifi"></i> Connected');
                }
            } else {
                 let spinner = '<span class="spinner-border spinner-border-sm text-warning" role="status" aria-hidden="true" style="width: 0.8rem; height: 0.8rem; border-width: 0.15em;"></span> <span class="text-warning small blink" style="font-size:0.75rem;">Wait...</span>';
                 $('#status-badge').attr('class', 'badge bg-warning text-dark shadow-sm blink').html('<i class="fas fa-sync fa-spin"></i> Auto-Login...');
            }

            // 2. Update Indices
            let inds = d.indices || {NIFTY:0, BANKNIFTY:0, SENSEX:0};
            if(inds.NIFTY === 0) {
                 let spinner = '<span class="spinner-border spinner-border-sm text-warning" role="status" aria-hidden="true" style="width: 0.8rem; height: 0.8rem; border-width: 0.15em;"></span>';
                 $('#n_lp').html(spinner); $('#b_lp').html(spinner); $('#s_lp').html(spinner);
            } else {
                $('#n_lp').text(inds.NIFTY); 
                $('#b_lp').text(inds.BANKNIFTY); 
                $('#s_lp').text(inds.SENSEX); 
            }

            // 3. Update Specific LTP (if requested)
            if (d.specific_ltp > 0) {
                curLTP = d.specific_ltp; 

                if ($('#importModal').is(':visible')) {
                    // Update Import Modal LTP
                    $('#imp_ltp').text("LTP: " + curLTP);
                    // Auto-fill price if empty
                    if(!$('#imp_price').val()) $('#imp_price').val(curLTP);
                } else {
                    // Update Main Trade Tab LTP
                    $('#inst_ltp').text("LTP: " + curLTP);
                    // Auto calculate SL points if user is typing
                    if (document.activeElement.id !== 'p_sl' && typeof calcSLPriceFromPts === 'function') {
                        calcSLPriceFromPts('#sl_pts', '#p_sl');
                    }
                }
            }

            // 4. Update Active Positions (Optimized)
            renderActivePositions(d.positions || []);

            // 5. Update Closed Trades (if requested)
            if (d.closed_trades && d.closed_trades.length > 0) {
                // Verify history.js is loaded
                if(typeof renderClosedTrades === 'function') renderClosedTrades(d.closed_trades);
            }
        },
        error: function(err) {
            console.log("Sync Error:", err);
        }
    });
}

// 2. Render Active Positions (Optimized for Real-Time Updates)
function renderActivePositions(trades) {
    activeTradesList = trades; 
    let sumLive = 0, sumPaper = 0;
    let capLive = 0, capPaper = 0; 
    let filterType = $('#active_filter').val();

    // 1. Calculate Totals first
    trades.forEach(t => {
        let pnl = (t.status === 'PENDING') ? 0 : (t.current_ltp - t.entry_price) * t.quantity;
        let invested = t.entry_price * t.quantity; 
        let cat = getTradeCategory(t);
        if(cat === 'LIVE') { sumLive += pnl; capLive += invested; }
        else if(cat === 'PAPER' && !t.is_replay) { sumPaper += pnl; capPaper += invested; }
    });
    
    // Update Header Stats
    $('#sum_live').text("₹ " + sumLive.toFixed(2)).attr('class', sumLive >= 0 ? 'fw-bold text-success' : 'fw-bold text-danger');
    $('#sum_paper').text("₹ " + sumPaper.toFixed(2)).attr('class', sumPaper >= 0 ? 'fw-bold text-success' : 'fw-bold text-danger');
    $('#cap_live').text("₹ " + (capLive/100000).toFixed(2) + " L");
    $('#cap_paper').text("₹ " + (capPaper/100000).toFixed(2) + " L");

    // 2. Smart Rendering (DOM Patching)
    let filtered = trades.filter(t => filterType === 'ALL' || getTradeCategory(t) === filterType);
    
    // A. Remove cards that are no longer present
    let currentIds = new Set(filtered.map(t => `trade-card-${t.id}`));
    $('#pos-container').children().each(function() {
        if (!currentIds.has(this.id) && this.id !== 'no-trades-msg') {
            $(this).remove();
        }
    });

    // B. Handle Empty State
    if(filtered.length === 0) {
        if($('#pos-container').children().length === 0) {
             $('#pos-container').html('<div class="text-center p-4 text-muted" id="no-trades-msg">No Active Trades for selected filter</div>');
        }
        return;
    } else {
        $('#no-trades-msg').remove();
    }

    // C. Update or Append Cards
    filtered.forEach(t => {
        let pnl = (t.status === 'PENDING') ? 0 : (t.current_ltp - t.entry_price) * t.quantity;
        let invested = t.entry_price * t.quantity;
        let pnlColor = pnl >= 0 ? 'text-success' : 'text-danger';
        if (t.status === 'PENDING') { pnl = 0; pnlColor = 'text-warning'; }
        
        let cat = getTradeCategory(t); 
        let badge = getMarkBadge(cat);
        if(t.is_replay) badge = '<span class="badge bg-info text-dark" style="font-size:0.65rem;">REPLAY</span>';

        // Status Tag
        let statusTag = '';
        if(t.status === 'PENDING') statusTag = '<span class="badge bg-warning text-dark" style="font-size:0.65rem;">Pending</span>';
        else {
            let hits = t.targets_hit_indices || [];
            let maxHit = -1;
            if(hits.length > 0) maxHit = Math.max(...hits);
            
            if(maxHit === 0) statusTag = '<span class="badge bg-success" style="font-size:0.65rem;">T1 Hit</span>';
            else if(maxHit === 1) statusTag = '<span class="badge bg-success" style="font-size:0.65rem;">T2 Hit</span>';
            else if(maxHit === 2) statusTag = '<span class="badge bg-success" style="font-size:0.65rem;">T3 Hit</span>';
            else statusTag = '<span class="badge bg-primary" style="font-size:0.65rem;">Active</span>';
        }

        // --- TIME LOGIC ---
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
            } else if (t.logs[0] && t.logs[0].includes("Status: OPEN")) {
                activeTimeStr = addedTimeStr;
                waitDuration = `<span class="text-muted ms-1" style="font-size:0.65rem;">(Instant)</span>`;
            }
        }
        if(t.is_replay && t.last_update_time) {
            activeTimeStr = t.last_update_time.slice(11, 16);
            waitDuration = '<span class="text-info ms-1" style="font-size:0.65rem;">(Sim)</span>';
        }
        
        // --- PROJECTED P&L CALCULATION ---
        let projProfit = 0;
        let projLoss = (t.sl - t.entry_price) * t.quantity;
        let remQty = t.quantity;
        let lotSz = t.lot_size || 1;
        let tControls = t.target_controls || [{enabled:true, lots:0}, {enabled:true, lots:0}, {enabled:true, lots:1000}];
        
        let startIdx = (t.targets_hit_indices && t.targets_hit_indices.length > 0) ? Math.max(...t.targets_hit_indices) + 1 : 0;

        for(let i=startIdx; i<3; i++) {
            if(remQty <= 0) break;
            let tp = t.targets[i];
            let tc = tControls[i];
            if(!tc) continue;
            let q = (i === 2 || tc.lots >= 1000) ? remQty : Math.min(tc.lots * lotSz, remQty);
            if(q > 0) {
                projProfit += (tp - t.entry_price) * q;
                remQty -= q;
            }
        }

        let projPColor = projProfit >= 0 ? 'text-success' : 'text-danger';
        let projLColor = projLoss >= 0 ? 'text-success' : 'text-danger';

        // --- DOM PATCHING ---
        let cardId = `trade-card-${t.id}`;
        
        if ($(`#${cardId}`).length) {
            // UPDATE EXISTING CARD
            // We only update fields that change frequently
            $(`#${cardId} .t-pnl-val`).text(t.status==='PENDING'?'PENDING':pnl.toFixed(2))
                .removeClass('text-success text-danger text-warning').addClass(pnlColor);
            
            $(`#${cardId} .t-ltp`).text(t.current_ltp.toFixed(2));
            $(`#${cardId} .t-qty`).text(t.quantity); // In case of partial exits
            $(`#${cardId} .t-fund`).text("₹" + (invested/1000).toFixed(1) + "k");
            $(`#${cardId} .t-sl`).text("SL: " + t.sl.toFixed(1));
            
            // Update Status Badge if it changed (optimization: check html content)
            let curBadge = $(`#${cardId} .t-status-container`).html();
            let newBadgeHtml = `${badge} ${statusTag}`;
            if(curBadge !== newBadgeHtml) $(`#${cardId} .t-status-container`).html(newBadgeHtml);
            
            // Update Projected
            $(`#${cardId} .t-proj-p`).text("₹" + projProfit.toFixed(0)).removeClass('text-success text-danger').addClass(projPColor);
            $(`#${cardId} .t-proj-l`).text("₹" + projLoss.toFixed(0)).removeClass('text-success text-danger').addClass(projLColor);
            
            // Update Times (in case of activation)
            if(activeTimeStr !== '--:--') {
                 $(`#${cardId} .t-active-time`).html(`<span class="text-primary">Active: <b>${activeTimeStr}</b></span> ${waitDuration}`);
            }

        } else {
            // CREATE NEW CARD
            let editBtn = `<button class="btn btn-sm btn-outline-primary py-0 px-2" style="font-size:0.75rem;" onclick="openEditTradeModal('${t.id}')">✏️</button>`;
            
            // --- NEW: AJAX Button for Exit/Cancel ---
            let exitBtn = `<button class="btn btn-sm btn-dark fw-bold py-0 px-2" style="font-size:0.75rem;" onclick="exitTrade('${t.id}')">${t.status==='PENDING'?'Cancel':'Exit'}</button>`;
            // ----------------------------------------

            let html = `
            <div id="${cardId}" class="card mb-2 shadow-sm border-0">
                <div class="card-body p-2">
                    <div class="d-flex justify-content-between align-items-start mb-1">
                        <div>
                            <span class="fw-bold text-dark h6 m-0">${t.symbol}</span>
                            <div class="mt-1 d-flex gap-1 align-items-center flex-wrap t-status-container">
                                ${badge} ${statusTag}
                            </div>
                        </div>
                        <div class="text-end">
                            <div class="fw-bold h6 m-0 t-pnl-val ${pnlColor}">${t.status==='PENDING'?'PENDING':pnl.toFixed(2)}</div>
                        </div>
                    </div>
                    <hr class="my-1 text-muted opacity-25">
                    <div class="row g-0 text-center mt-2" style="font-size:0.75rem;">
                        <div class="col-3 border-end">
                            <div class="text-muted small">Qty</div>
                            <div class="fw-bold text-dark t-qty">${t.quantity}</div>
                        </div>
                        <div class="col-3 border-end">
                            <div class="text-muted small">Entry</div>
                            <div class="fw-bold text-dark">${t.entry_price.toFixed(2)}</div>
                        </div>
                        <div class="col-3 border-end">
                            <div class="text-muted small">LTP</div>
                            <div class="fw-bold text-dark t-ltp">${t.current_ltp.toFixed(2)}</div>
                        </div>
                        <div class="col-3">
                            <div class="text-muted small">Fund</div>
                            <div class="fw-bold text-dark t-fund">₹${(invested/1000).toFixed(1)}k</div>
                        </div>
                    </div>
                    <div class="d-flex justify-content-between align-items-center mt-2 px-1 bg-light rounded py-1" style="font-size:0.75rem;">
                        <span class="text-muted">Added: <b>${addedTimeStr}</b></span>
                        <div class="d-flex align-items-center t-active-time">
                            <span class="text-primary">Active: <b>${activeTimeStr}</b></span>
                            ${waitDuration}
                        </div>
                    </div>
                    <div class="d-flex justify-content-between align-items-center mt-2 px-1" style="font-size:0.75rem;">
                         <span class="text-danger fw-bold t-sl">SL: ${t.sl.toFixed(1)}</span>
                         <span class="text-muted">T: ${t.targets[0].toFixed(0)} | ${t.targets[1].toFixed(0)} | ${t.targets[2].toFixed(0)}</span>
                    </div>

                    <div class="d-flex justify-content-between align-items-center mt-1 px-1 py-1 border-top border-light" style="font-size:0.75rem;">
                        <span class="text-muted" title="Based on Global/Trade Target Config"><i class="fas fa-shield-alt text-secondary"></i> Projected:</span>
                        <div>
                             <span class="t-proj-p ${projPColor} fw-bold me-2" title="Max Profit">₹${projProfit.toFixed(0)}</span>
                             <span class="t-proj-l ${projLColor} fw-bold" title="Max Loss">₹${projLoss.toFixed(0)}</span>
                        </div>
                    </div>

                    <div class="d-flex justify-content-end gap-2 mt-2 pt-1 border-top border-light">
                        ${editBtn}
                        <button class="btn btn-sm btn-light border text-muted py-0 px-2" style="font-size:0.75rem;" onclick="showLogs('${t.id}', 'active')">📜 Logs</button>
                        ${exitBtn}
                    </div>
                </div>
            </div>`;
            $('#pos-container').append(html);
        }
    });
}

// --- Trade Management Functions ---

function openEditTradeModal(id) {
    let t = activeTradesList.find(x => x.id == id); if(!t) return;
    $('#edit_trade_id').val(t.id);
    $('#edit_entry').val(t.entry_price);
    $('#edit_sl').val(t.sl);
    $('#edit_trail').val(t.trailing_sl || 0);
    $('#edit_trail_mode').val(t.sl_to_entry || 0);
    $('#edit_exit_mult').val(t.exit_multiplier || 1);
    
    let defaults = [
        {enabled: true, lots: 0, trail_to_entry: false},
        {enabled: true, lots: 0, trail_to_entry: false},
        {enabled: true, lots: 1000, trail_to_entry: false}
    ];
    let controls = t.target_controls || defaults;

    // T1
    $('#edit_t1').val(t.targets[0] || 0);
    $('#check_t1').prop('checked', controls[0].enabled);
    let l1 = controls[0].lots;
    $('#full_t1').prop('checked', l1 >= 1000);
    $('#lot_t1').val(l1 < 1000 && l1 > 0 ? l1 : '');
    $('#cost_t1').prop('checked', controls[0].trail_to_entry || false);
    
    // T2
    $('#edit_t2').val(t.targets[1] || 0);
    $('#check_t2').prop('checked', controls[1].enabled);
    let l2 = controls[1].lots;
    $('#full_t2').prop('checked', l2 >= 1000);
    $('#lot_t2').val(l2 < 1000 && l2 > 0 ? l2 : '');
    $('#cost_t2').prop('checked', controls[1].trail_to_entry || false);

    // T3
    $('#edit_t3').val(t.targets[2] || 0);
    $('#check_t3').prop('checked', controls[2].enabled);
    let l3 = controls[2].lots;
    $('#full_t3').prop('checked', l3 >= 1000);
    $('#lot_t3').val(l3 < 1000 && l3 > 0 ? l3 : '');
    $('#cost_t3').prop('checked', controls[2].trail_to_entry || false);
    
    let hits = t.targets_hit_indices || [];
    $('#edit_t1').prop('disabled', hits.includes(0));
    $('#edit_t2').prop('disabled', hits.includes(1));
    $('#edit_t3').prop('disabled', hits.includes(2));
    
    let lot = t.lot_size || 1;
    $('#man_add_lots').attr('step', lot).attr('min', lot).val(lot).data('lot', lot);
    $('#man_exit_lots').attr('step', lot).attr('min', lot).val(lot).data('lot', lot);

    new bootstrap.Modal(document.getElementById('editTradeModal')).show();
}

function saveTradeUpdate() {
    let d = {
        id: $('#edit_trade_id').val(),
        entry_price: parseFloat($('#edit_entry').val()),
        sl: parseFloat($('#edit_sl').val()),
        trailing_sl: parseFloat($('#edit_trail').val()),
        sl_to_entry: parseInt($('#edit_trail_mode').val()) || 0,
        exit_multiplier: parseInt($('#edit_exit_mult').val()) || 1,
        targets: [
            parseFloat($('#edit_t1').val())||0,
            parseFloat($('#edit_t2').val())||0,
            parseFloat($('#edit_t3').val())||0
        ],
        target_controls: [
            { 
                enabled: $('#check_t1').is(':checked'), 
                lots: $('#full_t1').is(':checked') ? 1000 : (parseInt($('#lot_t1').val()) || 0),
                trail_to_entry: $('#cost_t1').is(':checked')
            },
            { 
                enabled: $('#check_t2').is(':checked'), 
                lots: $('#full_t2').is(':checked') ? 1000 : (parseInt($('#lot_t2').val()) || 0),
                trail_to_entry: $('#cost_t2').is(':checked')
            },
            { 
                enabled: $('#check_t3').is(':checked'), 
                lots: $('#full_t3').is(':checked') ? 1000 : (parseInt($('#lot_t3').val()) || 0),
                trail_to_entry: $('#cost_t3').is(':checked')
            }
        ]
    };
    $.ajax({ type: "POST", url: '/api/update_trade', data: JSON.stringify(d), contentType: "application/json", success: function(r) { if(r.status==='success') { $('#editTradeModal').modal('hide'); updateData(); } else alert("Failed to update: " + r.message); } });
}

function managePos(action) {
    let inputId = (action === 'ADD') ? '#man_add_lots' : '#man_exit_lots';
    let qty = parseInt($(inputId).val());
    let lotSize = $(inputId).data('lot') || 1;
    
    if(!qty || qty <= 0 || qty % lotSize !== 0) { 
        alert(`Invalid Quantity. Must be multiple of ${lotSize}`); return; 
    }
    let lots = qty / lotSize;
    if(confirm(`${action === 'ADD' ? 'Add' : 'Exit'} ${qty} Qty (${lots} Lots)?`)) {
        let d = { id: $('#edit_trade_id').val(), action: action, lots: lots };
        $.ajax({
            type: "POST", url: '/api/manage_trade', data: JSON.stringify(d), contentType: "application/json",
            success: function(r) {
                if(r.status === 'success') { $('#editTradeModal').modal('hide'); updateData(); }
                else alert("Error: " + r.message);
            }
        });
    }
}

// --- NEW: AJAX EXIT FUNCTION ---
function exitTrade(id) {
    if(!confirm("Are you sure you want to Cancel/Exit this trade?")) return;
    
    // Optimistic UI: Hide card immediately to feel "Instant"
    $(`#trade-card-${id}`).css('opacity', '0.5');

    $.ajax({
        url: `/close_trade/${id}`,
        type: 'POST', // Using POST for Action
        success: function(r) {
            if(r.status === 'success') {
                if(typeof showFloatingAlert === 'function') showFloatingAlert(r.message, 'success');
                // Data update will naturally remove the card
                updateData(); 
            } else {
                $(`#trade-card-${id}`).css('opacity', '1'); // Revert if failed
                if(typeof showFloatingAlert === 'function') showFloatingAlert(r.message, 'error');
                else alert(r.message);
            }
        },
        error: function(err) {
            $(`#trade-card-${id}`).css('opacity', '1');
            alert("Network Error during Exit");
        }
    });
}
