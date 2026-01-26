// Global State
let orbLotSize = 50; 
let orbCheckInterval = null;

// --- 1. STRICT ENFORCEMENT LOGIC ---
// Defined globally so it can be called from anywhere (Poller, Events, Init)
function enforceStrictReEntryRules() {
    let dir = $('#orb_direction').val(); // Get current UI value
    let $oppCheckbox = $('#orb_reentry_opposite');
    let isBotRunning = $('#orb_direction').prop('disabled'); // Check if bot is running

    // RULE: If Direction is NOT 'BOTH', Opposite Re-entry MUST be Disabled & Unchecked.
    if (dir && dir !== 'BOTH') {
        // Force Disable
        if (!$oppCheckbox.prop('disabled')) {
            $oppCheckbox.prop('disabled', true);
        }
        // Force Uncheck
        if ($oppCheckbox.prop('checked')) {
            $oppCheckbox.prop('checked', false);
        }
    } 
    // RULE: If 'BOTH', enable ONLY if bot is STOPPED (Edit Mode)
    else if (!isBotRunning) {
        if ($oppCheckbox.prop('disabled')) {
            $oppCheckbox.prop('disabled', false);
        }
    }
}

$(document).ready(function() {
    // 1. Initialization
    const REFRESH_INTERVAL = 1000; // 1 Second Refresh
    console.log("🚀 RD Algo Terminal Loaded");

    // Init Bootstrap Tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
      return new bootstrap.Tooltip(tooltipTriggerEl)
    })

    // Load Initial Data
    renderWatchlist();
    if(typeof loadSettings === 'function') loadSettings();
    
    // --- ORB STRATEGY INIT ---
    if ($('#orb_status_badge').length) {
        loadOrbStatus();
        // Poll status every 3 seconds to keep UI in sync
        orbCheckInterval = setInterval(loadOrbStatus, 3000);
    }

    // --- 2. BINDINGS FOR STRICT RULES ---
    
    // A. When Direction Changes -> Enforce Rules Immediately
    $('#orb_direction').change(enforceStrictReEntryRules);
    
    // B. Intercept ALL interactions on the Checkbox
    // Catches clicks, double-clicks, and keyboard toggles to prevent forcing it open
    $('#orb_reentry_opposite').on('click mousedown mouseup change', function(e) {
        let dir = $('#orb_direction').val();
        if (dir && dir !== 'BOTH') {
            e.preventDefault();
            e.stopPropagation();
            // Hard Reset State
            $(this).prop('checked', false);
            $(this).prop('disabled', true);
            return false;
        }
    });

    // 3. Date & Time Defaults
    let now = new Date(); 
    const offset = now.getTimezoneOffset(); 
    let localDate = new Date(now.getTime() - (offset*60*1000));
    
    $('#hist_date').val(localDate.toISOString().slice(0,10)); // History Date
    if($('#imp_time').length) $('#imp_time').val(localDate.toISOString().slice(0,16)); // Import Time
    
    // 4. Global Event Bindings
    $('#hist_date, #hist_filter').change(loadClosedTrades);
    $('#active_filter').change(updateData);
    
    $('input[name="type"]').change(function() {
        let s = $('#sym').val();
        if(s) loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts');
    });
    
    $('#sl_pts, #qty, #lim_pr, #ord').on('input change', calcRisk);
    
    // Search & Autocomplete
    bindSearch('#sym', '#sym_list'); 
    bindSearch('#imp_sym', '#sym_list'); 
    bindSearch('#new_watch_sym', '#sym_list');

    // Chain & Input Logic
    $('#sym').change(() => loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts'));
    $('#exp').change(() => fillChain('#sym', '#exp', 'input[name="type"]:checked', '#str'));
    $('#ord').change(function() { if($(this).val() === 'LIMIT') $('#lim_box').show(); else $('#lim_box').hide(); });
    $('#str').change(fetchLTP);

    // 5. Import Modal Bindings
    $('#imp_sym').change(() => loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts')); 
    $('#imp_exp').change(() => fillChain('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_str'));
    $('#imp_str').change(fetchLTP); // Fetch LTP for Import too

    $('input[name="imp_type"]').change(() => loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts'));
    
    // Import Risk Calc
    $('#imp_price').on('input', function() { calcImpFromPts(); }); 
    $('#imp_sl_pts').on('input', calcImpFromPts);
    $('#imp_sl_price').on('input', calcImpFromPrice);
    
    // Import "Full Exit" Checkboxes
    ['t1', 't2', 't3'].forEach(k => {
        $(`#imp_${k}_full`).change(function() {
            if($(this).is(':checked')) {
                $(`#imp_${k}_lots`).val(1000).prop('readonly', true);
            } else {
                $(`#imp_${k}_lots`).prop('readonly', false).val(0); 
            }
        });
    });

    // 6. Loops
    setInterval(updateClock, 1000); updateClock();
    setInterval(updateData, REFRESH_INTERVAL); updateData();
});

// ==========================================
// ORB STRATEGY FUNCTIONS
// ==========================================

function loadOrbStatus() {
    $.get('/api/orb/params', function(data) {
        // 1. Update Lot Size
        if (data.lot_size && data.lot_size > 0) {
            orbLotSize = data.lot_size;
            $('#orb_lot_size').text(orbLotSize);
        }

        // 2. Update Active State UI
        if (data.active) {
            $('#orb_status_badge').removeClass('bg-secondary').addClass('bg-success').text('RUNNING');
            
            // Toggle Buttons
            $('#btn_orb_start').addClass('d-none');
            $('#btn_orb_stop').removeClass('d-none');
            
            // Lock Controls & Sync Value (Server is Master)
            if (!$('#orb_lots_input').is(':focus')) $('#orb_lots_input').val(data.current_lots);
            if (!$('#orb_mode_input').is(':focus')) $('#orb_mode_input').val(data.current_mode);
            if (!$('#orb_direction').is(':focus')) $('#orb_direction').val(data.current_direction);
            if (!$('#orb_cutoff').is(':focus')) $('#orb_cutoff').val(data.current_cutoff);

            // Sync Re-entry Settings
            $('#orb_reentry_same_sl').prop('checked', data.re_sl);
            $('#orb_reentry_same_filter').val(data.re_sl_filter);
            $('#orb_reentry_opposite').prop('checked', data.re_opp);

            // Disable Inputs while Running
            $('#orb_lots_input').prop('disabled', true);
            $('#orb_mode_input').prop('disabled', true);
            $('#orb_direction').prop('disabled', true);
            $('#orb_cutoff').prop('disabled', true);
            $('#orb_reentry_same_sl').prop('disabled', true);
            $('#orb_reentry_same_filter').prop('disabled', true);
            $('#orb_reentry_opposite').prop('disabled', true);
            
        } else {
            $('#orb_status_badge').removeClass('bg-success').addClass('bg-secondary').text('STOPPED');
            
            // Toggle Buttons
            $('#btn_orb_start').removeClass('d-none');
            $('#btn_orb_stop').addClass('d-none');
            
            // Unlock Controls (BUT do not sync from server to allow editing)
            $('#orb_lots_input').prop('disabled', false);
            $('#orb_mode_input').prop('disabled', false);
            $('#orb_direction').prop('disabled', false);
            $('#orb_cutoff').prop('disabled', false);
            $('#orb_reentry_same_sl').prop('disabled', false);
            $('#orb_reentry_same_filter').prop('disabled', false);
            
            // The Opposite Re-entry Checkbox state is managed by enforceStrictReEntryRules() below
        }
        
        // 3. ENFORCE RULES (Overrides any previous unlock)
        enforceStrictReEntryRules();
        
        // 4. Recalculate Totals
        updateOrbCalc();
    });
}

function updateOrbCalc() {
    let input = $('#orb_lots_input');
    let userLots = parseInt(input.val()) || 0;
    
    // Ensure positive integer
    if (userLots < 2) {
        // Just visual hint, validation happens on click
    }
    
    let totalQty = userLots * orbLotSize;
    $('#orb_total_qty').text(totalQty);
}

function toggleOrb(action) {
    let lots = parseInt($('#orb_lots_input').val()) || 2;
    let mode = $('#orb_mode_input').val(); 
    let direction = $('#orb_direction').val();
    let cutoff = $('#orb_cutoff').val();

    let re_sl = $('#orb_reentry_same_sl').is(':checked');
    let re_sl_filter = $('#orb_reentry_same_filter').val();
    let re_opp = $('#orb_reentry_opposite').is(':checked');

    // Force disable in payload if strict check fails (Sanity Check)
    if (direction !== 'BOTH') re_opp = false;

    if (action === 'start') {
        // VALIDATION: Minimum 2 Lots
        if (lots < 2) {
            alert("Minimum 2 lots required.");
            return;
        }
        
        // VALIDATION: Multiple of 2 Only
        if (lots % 2 !== 0) {
            lots += 1; // Auto-correct to next even number
            alert("⚠️ Lots adjusted to " + lots + ". Must be a multiple of 2.");
            $('#orb_lots_input').val(lots);
        }
        
        if(!cutoff) {
            alert("Please select a valid Cutoff Time.");
            return;
        }
    }

    // Disable buttons to prevent double-click
    $('#btn_orb_start, #btn_orb_stop').prop('disabled', true);
    
    let payload = {
        action: action,
        lots: lots,
        mode: mode,
        direction: direction,
        cutoff: cutoff,
        re_sl: re_sl,
        re_sl_filter: re_sl_filter,
        re_opp: re_opp
    };

    $.ajax({
        url: '/api/orb/toggle',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload),
        success: function(res) {
            // Show result
            if(window.showFloatingAlert) {
                showFloatingAlert(res.message, res.status === 'success' ? 'success' : 'danger');
            } else {
                alert(res.message);
            }
            loadOrbStatus();
        },
        error: function(err) {
            alert("Request Failed: " + err.statusText);
        },
        complete: function() {
            $('#btn_orb_start, #btn_orb_stop').prop('disabled', false);
        }
    });
}

// ==========================================
// CORE DASHBOARD FUNCTIONS
// ==========================================

function updateClock() {
    let now = new Date();
    let options = { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true, timeZone: 'Asia/Kolkata' };
    let timeString = now.toLocaleTimeString('en-US', options);
    $('#live_clock').text(timeString);
}

function updateData() {
    // 1. Fetch Indices (Updates Ticker on ALL Pages)
    $.getJSON('/api/indices', function(data) {
        $('#n_lp').text(data.NIFTY.toFixed(2));
        $('#b_lp').text(data.BANKNIFTY.toFixed(2));
        $('#s_lp').text(data.SENSEX.toFixed(2));
    });

    // 2. Fetch Active Trades (Only for Dashboard Page)
    if ($('#pos-container').length) {
        $.getJSON('/api/positions', function(trades) {
            let container = $('#pos-container');
            container.empty();
            let filter = $('#active_filter').val();
            
            let displayTrades = trades.filter(t => {
                if (filter === 'LIVE') return t.mode === 'LIVE';
                if (filter === 'PAPER') return t.mode === 'PAPER';
                return true; 
            });

            if (displayTrades.length === 0) {
                container.html('<div class="text-center text-muted p-4">No active positions</div>');
            } else {
                displayTrades.forEach(t => {
                    let card = createTradeCard(t);
                    container.append(card);
                });
            }
        });
    }
}

function createTradeCard(t) {
    let pnlClass = t.pnl >= 0 ? 'text-success' : 'text-danger';
    let pnlSign = t.pnl >= 0 ? '+' : '';
    let badgeClass = t.mode === 'LIVE' ? 'bg-danger' : 'bg-primary';
    
    // Calculate progress for targets
    let t1_status = t.t1_hit ? '<span class="badge bg-success">T1 Hit</span>' : '';
    
    let html = `
    <div class="card mb-2 shadow-sm trade-card border-start border-4 ${t.pnl >= 0 ? 'border-success' : 'border-danger'}">
        <div class="card-body p-2">
            <div class="d-flex justify-content-between align-items-center mb-2">
                <div>
                    <span class="badge ${badgeClass} me-1">${t.mode}</span>
                    <span class="fw-bold">${t.symbol}</span>
                    <small class="text-muted ms-2">${t.entry_time.split(' ')[1]}</small>
                </div>
                <div class="text-end">
                    <h5 class="mb-0 fw-bold ${pnlClass}">${pnlSign}₹${t.pnl.toFixed(2)}</h5>
                </div>
            </div>
            
            <div class="row g-1 mb-2" style="font-size: 0.85rem;">
                <div class="col-3 text-muted">Qty: <span class="text-dark fw-bold">${t.qty}</span></div>
                <div class="col-3 text-muted">Avg: <span class="text-dark fw-bold">${t.avg_price.toFixed(2)}</span></div>
                <div class="col-3 text-muted">LTP: <span class="text-dark fw-bold">${t.ltp.toFixed(2)}</span></div>
                <div class="col-3 text-muted">SL: <span class="text-danger fw-bold">${t.sl.toFixed(2)}</span></div>
            </div>

            <div class="d-flex gap-2 justify-content-end">
                ${t1_status}
                <button class="btn btn-outline-secondary btn-sm py-0 px-2" onclick="editTrade('${t.id}')">✏️</button>
                <button class="btn btn-outline-danger btn-sm py-0 px-2" onclick="closeTrade('${t.id}')">Exit</button>
            </div>
        </div>
    </div>
    `;
    return html;
}

function closeTrade(id) {
    if(confirm('Are you sure you want to close this trade?')) {
        $.post('/close_trade/' + id, function(res) {
            alert(res.message);
            updateData();
        });
    }
}

function loadClosedTrades() {
    if (!$('#closed-container').length) return;
    
    let date = $('#hist_date').val();
    let filter = $('#hist_filter').val(); // LIVE / PAPER / ALL
    
    $.getJSON('/api/closed_trades', function(trades) {
        let container = $('#closed-container');
        container.empty();
        
        // Filter by date
        let filtered = trades.filter(t => t.exit_time && t.exit_time.startsWith(date));
        
        // Filter by Mode
        if (filter !== 'ALL') {
            filtered = filtered.filter(t => t.mode === filter);
        }

        if (filtered.length === 0) {
            container.html('<div class="text-center text-muted p-3">No trades found for this date.</div>');
            return;
        }

        let totalPnL = 0;
        let html = '<div class="list-group">';
        
        filtered.forEach(t => {
            totalPnL += parseFloat(t.pnl);
            let pnlColor = t.pnl >= 0 ? 'text-success' : 'text-danger';
            html += `
                <div class="list-group-item list-group-item-action">
                    <div class="d-flex w-100 justify-content-between">
                        <h6 class="mb-1 fw-bold">${t.symbol} <span class="badge bg-secondary" style="font-size:0.6rem">${t.mode}</span></h6>
                        <span class="fw-bold ${pnlColor}">₹${t.pnl.toFixed(2)}</span>
                    </div>
                    <small class="text-muted">Buy: ${t.avg_price} | Sell: ${t.exit_price}</small><br>
                    <small class="text-muted">Time: ${t.entry_time.split(' ')[1]} - ${t.exit_time.split(' ')[1]}</small>
                    <div class="mt-1"><span class="badge bg-light text-dark border">${t.status}</span></div>
                </div>
            `;
        });
        html += '</div>';
        
        $('#closed_pnl').text('₹' + totalPnL.toFixed(2));
        if(totalPnL >= 0) $('#closed_pnl').removeClass('text-danger').addClass('text-success');
        else $('#closed_pnl').removeClass('text-success').addClass('text-danger');
        
        container.html(html);
    });
}

function updateDisplayValues() {
    let mode = $('#mode_input').val(); 
    let s = settings.modes[mode]; if(!s) return;
    $('#qty_mult_disp').text(s.qty_mult); 
    $('#r_t1').text(s.ratios[0]); 
    $('#r_t2').text(s.ratios[1]); 
    $('#r_t3').text(s.ratios[2]); 
    if(typeof calcRisk === "function") calcRisk();
}

function switchTab(id) { 
    $('.dashboard-tab').hide(); $(`#${id}`).show(); 
    $('.nav-btn').removeClass('active'); $(event.target).addClass('active'); 
    if(id==='closed') loadClosedTrades(); 
    updateDisplayValues(); 
    if(id === 'trade') $('.sticky-footer').show(); else $('.sticky-footer').hide();
}

function setMode(el, mode) { 
    $('#mode_input').val(mode); 
    $(el).parent().find('.btn').removeClass('active'); 
    $(el).addClass('active'); 
    updateDisplayValues(); 
    loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts'); 
}

function panicExit() {
    if(confirm("⚠️ URGENT: Are you sure you want to CLOSE ALL POSITIONS (Live & Paper) immediately?")) {
        $.post('/api/panic_exit', function(res) {
            if(res.status === 'success') {
                alert("🚨 Panic Protocol Initiated: All orders cancelled and positions squaring off.");
                location.reload();
            } else {
                alert("Error: " + res.message);
            }
        });
    }
}

// ==========================================
// UTILS
// ==========================================

function bindSearch(inputId, listId) {
    $(inputId).on('input', function() {
        let q = $(this).val();
        if (q.length < 2) return;
        $.getJSON('/api/search', { q: q }, function(data) {
            let list = $(listId);
            list.empty();
            data.forEach(item => {
                // FIXED: Handle object items (e.g. {tradingsymbol: '...', ...})
                let val = (typeof item === 'object' && item !== null) ? (item.tradingsymbol || item.symbol || item.name || JSON.stringify(item)) : item;
                list.append(`<option value="${val}">`);
            });
        });
    });
}

function loadDetails(symId, expId, typeSelector, qtyId, slId) {
    let sym = $(symId).val();
    if (!sym) return;
    $.getJSON('/api/details', { symbol: sym }, function(d) {
        if(d.lot_size) {
            window.curLotSize = d.lot_size; 
            $(qtyId).val(d.lot_size);
        }
        if(d.opt_expiries) {
            let exps = d.opt_expiries.map(e => `<option value="${e}">${e}</option>`).join('');
            $(expId).html(exps).trigger('change');
        }
    });
}

function fillChain(symId, expId, typeSelector, strId) {
    let sym = $(symId).val();
    let exp = $(expId).val();
    let typ = $(typeSelector).val();
    
    // Get Spot LTP first to center chain
    $.getJSON('/api/indices', function(indices) {
        let ltp = 0;
        if(sym.includes('NIFTY')) ltp = indices.NIFTY;
        else if(sym.includes('BANK')) ltp = indices.BANKNIFTY;
        
        $.getJSON('/api/chain', { symbol: sym, expiry: exp, type: typ, ltp: ltp }, function(strikes) {
            let opts = strikes.map(s => {
                // FIXED: Handle object items (e.g. {strike: 21000})
                let val = (typeof s === 'object' && s !== null) ? (s.strike || s.price || s) : s;
                let isSelected = (s == strikes[Math.floor(strikes.length/2)]) ? 'selected' : '';
                return `<option value="${val}" ${isSelected}>${val}</option>`;
            }).join('');
            $(strId).html(opts).trigger('change');
        });
    });
}

function fetchLTP() {
    let s = $('#sym').val(); let e = $('#exp').val(); let str = $('#str').val(); let t = $('input[name="type"]:checked').val();
    if(s && e && str && t) {
        $.getJSON('/api/specific_ltp', { symbol: s, expiry: e, strike: str, type: t }, function(res) {
            $('#lim_pr').val(res.ltp);
            calcRisk();
        });
    }
    
    // Also for Import Modal
    let is = $('#imp_sym').val(); let ie = $('#imp_exp').val(); let istr = $('#imp_str').val(); let it = $('input[name="imp_type"]:checked').val();
    if(is && ie && istr && it) {
        $.getJSON('/api/specific_ltp', { symbol: is, expiry: ie, strike: istr, type: it }, function(res) {
            $('#imp_price').val(res.ltp);
            calculateImportRisk();
        });
    }
}

function calcRisk() {
    let price = parseFloat($('#lim_pr').val()) || 0;
    let sl_pts = parseFloat($('#sl_pts').val()) || 0;
    let qty = parseInt($('#qty').val()) || 0;
    
    if (price > 0 && sl_pts > 0 && qty > 0) {
        let risk = sl_pts * qty;
        $('#risk_disp').text(`Risk: ₹${risk.toFixed(0)}`);
    } else {
        $('#risk_disp').text('');
    }
}

// ==========================================
// IMPORT TRADE LOGIC
// ==========================================

function adjImpQty(dir) {
    let q = $('#imp_qty');
    let v = parseInt(q.val()) || 0;
    let step = (typeof curLotSize !== 'undefined' && curLotSize > 0) ? curLotSize : 1;
    let n = v + (dir * step);
    if(n < step) n = step;
    q.val(n);
}

function calcImpFromPts() {
    let entry = parseFloat($('#imp_price').val()) || 0;
    let pts = parseFloat($('#imp_sl_pts').val()) || 0;
    if(entry > 0) {
        $('#imp_sl_price').val((entry - pts).toFixed(2));
        calculateImportTargets(entry, pts);
    }
}

function calcImpFromPrice() {
    let entry = parseFloat($('#imp_price').val()) || 0;
    let price = parseFloat($('#imp_sl_price').val()) || 0;
    if(entry > 0) {
        let pts = entry - price;
        $('#imp_sl_pts').val(pts.toFixed(2));
        calculateImportTargets(entry, pts);
    }
}

function calculateImportTargets(entry, pts) {
    if(!entry || !pts) return;
    
    // Default Ratios
    let ratios = settings.modes.PAPER.ratios || [0.5, 1.0, 1.5];
    let t1_pts = pts * ratios[0];
    let t2_pts = pts * ratios[1];
    let t3_pts = pts * ratios[2];

    // Symbol Specific Override
    let sVal = $('#imp_sym').val();
    if(sVal) {
        let normS = (typeof normalizeSymbol === 'function') 
            ? normalizeSymbol(sVal) 
            : sVal.split(':')[0].trim().toUpperCase();
        
        let paperSettings = settings.modes.PAPER;
        if(paperSettings && paperSettings.symbol_sl && paperSettings.symbol_sl[normS]) {
            let sData = paperSettings.symbol_sl[normS];
            if (typeof sData === 'object' && sData.targets && sData.targets.length === 3) {
                t1_pts = sData.targets[0];
                t2_pts = sData.targets[1];
                t3_pts = sData.targets[2];
            }
        }
    }

    $('#imp_t1').val((entry + t1_pts).toFixed(2));
    $('#imp_t2').val((entry + t2_pts).toFixed(2));
    $('#imp_t3').val((entry + t3_pts).toFixed(2));
}

function calculateImportRisk() {
    let entry = parseFloat($('#imp_price').val()) || 0;
    let pts = parseFloat($('#imp_sl_pts').val()) || 0;
    let price = parseFloat($('#imp_sl_price').val()) || 0;
    
    if(entry === 0) return;

    if (pts > 0) {
        $('#imp_sl_price').val((entry - pts).toFixed(2));
    } else if (price > 0) {
        pts = entry - price;
        $('#imp_sl_pts').val(pts.toFixed(2));
    } else {
        pts = 20; // Default fallback
        $('#imp_sl_pts').val(pts.toFixed(2));
        $('#imp_sl_price').val((entry - pts).toFixed(2));
    }
    calculateImportTargets(entry, pts);
}

function submitImport() {
    let d = {
        symbol: $('#imp_sym').val(),
        expiry: $('#imp_exp').val(),
        strike: $('#imp_str').val(),
        type: $('input[name="imp_type"]:checked').val(),
        entry_time: $('#imp_time').val(),
        qty: parseInt($('#imp_qty').val()),
        price: parseFloat($('#imp_price').val()),
        sl: parseFloat($('#imp_sl_price').val()),
        target_channel: $('input[name="imp_channel"]:checked').val() || 'main',
        trailing_sl: parseFloat($('#imp_trail_sl').val()) || 0,
        sl_to_entry: parseInt($('#imp_trail_limit').val()) || 0,
        exit_multiplier: parseInt($('#imp_exit_mult').val()) || 1,
        targets: [
            parseFloat($('#imp_t1').val())||0,
            parseFloat($('#imp_t2').val())||0,
            parseFloat($('#imp_t3').val())||0
        ],
        target_controls: [
            { 
                enabled: $('#imp_t1_active').is(':checked'), 
                lots: $('#imp_t1_full').is(':checked') ? 1000 : (parseInt($('#imp_t1_lots').val()) || 0),
                trail_to_entry: $('#imp_t1_cost').is(':checked')
            },
            { 
                enabled: $('#imp_t2_active').is(':checked'), 
                lots: $('#imp_t2_full').is(':checked') ? 1000 : (parseInt($('#imp_t2_lots').val()) || 0),
                trail_to_entry: $('#imp_t2_cost').is(':checked')
            },
            { 
                enabled: $('#imp_t3_active').is(':checked'), 
                lots: $('#imp_t3_full').is(':checked') ? 1000 : (parseInt($('#imp_t3_lots').val()) || 0),
                trail_to_entry: $('#imp_t3_cost').is(':checked')
            }
        ]
    };
    
    if(!d.symbol || !d.entry_time || !d.price) { alert("Please fill all fields"); return; }
    
    $.ajax({
        type: "POST", url: '/api/import_trade', 
        data: JSON.stringify(d), contentType: "application/json",
        success: function(r) {
            if(r.status === 'success') {
                alert(r.message);
                $('#importModal').modal('hide');
                updateData(); 
            } else {
                alert("Error: " + r.message);
            }
        }
    });
}

function renderWatchlist() {
    if (typeof settings === 'undefined' || !settings.watchlist) return;
    let wl = settings.watchlist || [];
    let opts = '<option value="">📺 Select</option>';
    wl.forEach(w => { opts += `<option value="${w}">${w}</option>`; });
    $('#trade_watch').html(opts);
    $('#imp_watch').html(opts);
    
    let remOpts = '<option value="">Select to Remove...</option>';
    wl.forEach(w => { remOpts += `<option value="${w}">${w}</option>`; });
    if($('#remove_watch_sym').length) $('#remove_watch_sym').html(remOpts);
}

// Helper: Floating Alert
if (typeof showFloatingAlert === 'undefined') {
    window.showFloatingAlert = function(message, type='primary') {
        let alertHtml = `
            <div class="alert alert-${type} alert-dismissible fade show floating-alert py-3 border-0" role="alert">
                <span class="fw-bold">${message}</span>
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
        if ($('.notification-container').length) {
            $('.notification-container').append(alertHtml);
        } else {
            $('body').append('<div class="notification-container" style="position: fixed; top: 20px; right: 20px; z-index: 9999;">' + alertHtml + '</div>');
        }
        setTimeout(() => { $('.alert').last().alert('close'); }, 5000);
    };
}
