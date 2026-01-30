// Global Socket Object
var socket = null;

// [CHANGED] Reduced to 1000ms (1 Second) for Real-Time feel
const REFRESH_INTERVAL = 1000; 

$(document).ready(function() {
    // --- WEBSOCKET INITIALIZATION ---
    socket = io();

    socket.on('connect', function() {
        console.log("✅ Frontend Connected to WebSocket!");
        $('#connection-status').html('<span class="badge bg-success">Online ⚡</span>');
        $('#status-badge').attr('class', 'badge bg-success shadow-sm').html('<i class="fas fa-wifi"></i> Live Feed');
    });

    socket.on('disconnect', function() {
        console.log("❌ Frontend Disconnected");
        $('#connection-status').html('<span class="badge bg-danger">Offline 🔌</span>');
        $('#status-badge').attr('class', 'badge bg-danger shadow-sm').html('Socket Lost');
    });

    // Listen for Real-Time Trade Updates (Active Positions)
    socket.on('trade_update', function(data) {
        if(typeof renderActivePositions === 'function') {
            renderActivePositions(data);
        }
    });

    // --- INITIALIZATION ---
    renderWatchlist();
    if(typeof loadSettings === 'function') loadSettings();
    
    // Date Logic
    let now = new Date(); 
    const offset = now.getTimezoneOffset(); 
    let localDate = new Date(now.getTime() - (offset*60*1000));
    
    // Set Inputs
    $('#hist_date').val(localDate.toISOString().slice(0,10)); 
    $('#imp_time').val(localDate.toISOString().slice(0,16)); 
    
    // --- EVENT BINDINGS ---
    
    // Global Filters
    $('#hist_date, #hist_filter').change(loadClosedTrades);
    $('#active_filter').change(updateData);
    
    // New Order Form Logic
    $('input[name="type"]').change(function() {
        let s = $('#sym').val();
        if(s) loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts');
    });
    
    $('#sl_pts, #qty, #lim_pr, #ord').on('input change', calcRisk);
    
    // Search Bindings
    bindSearch('#sym', '#sym_list'); 
    bindSearch('#imp_sym', '#sym_list'); 
    bindSearch('#new_watch_sym', '#sym_list'); 

    // Chain & Input Bindings (New Order)
    $('#sym').change(() => loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts'));
    $('#exp').change(() => fillChain('#sym', '#exp', 'input[name="type"]:checked', '#str'));
    $('#ord').change(function() { if($(this).val() === 'LIMIT') $('#lim_box').show(); else $('#lim_box').hide(); });

    // --- IMPORT MODAL BINDINGS (Instant Updates) ---
    
    // 1. Symbol Change -> Load Details -> Force Update
    $('#imp_sym').change(function() {
        loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts');
        setTimeout(updateData, 500); 
    }); 

    // 2. Expiry Change -> Load Strikes -> Force Update
    $('#imp_exp').change(function() {
        fillChain('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_str');
        setTimeout(updateData, 500);
    });

    // 3. Strike Change -> FETCH LTP IMMEDIATELY
    $('#imp_str').change(function() {
        $('#imp_ltp_display').text("Fetching...");
        updateData(); // Force immediate call
    });
    
    // 4. Type Change -> Reload -> Force Update
    $('input[name="imp_type"]').change(function() {
        loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts');
        updateData();
    });
    
    // Import Risk Calc Bindings
    $('#imp_price').on('input', function() { calcImpFromPts(); }); 
    $('#imp_sl_pts').on('input', calcImpFromPts);
    $('#imp_sl_price').on('input', calcImpFromPrice);
    
    // "Full" Checkbox Listeners
    ['t1', 't2', 't3'].forEach(k => {
        $(`#imp_${k}_full`).change(function() {
            if($(this).is(':checked')) {
                $(`#imp_${k}_lots`).val(1000).prop('readonly', true);
            } else {
                $(`#imp_${k}_lots`).prop('readonly', false);
                if($(`#imp_${k}_lots`).val() == 1000) $(`#imp_${k}_lots`).val(0); 
            }
        });
    });

    // Auto-Remove Floating Notifications
    setTimeout(function() {
        $('.floating-alert').fadeOut('slow', function() { $(this).remove(); });
    }, 4000); 

    // --- LOOPS ---
    setInterval(updateClock, 1000); 
    updateClock();
    
    // Background Sync Loop (Indices, Import LTP)
    // Now runs every 1 second for Real-Time feel
    setInterval(updateData, REFRESH_INTERVAL); 
    updateData(); // Initial Call
});

// --- CORE DATA SYNC FUNCTION (Restored) ---
function updateData() {
    // Prepare Payload
    let payload = {
        // Only fetch closed trades if history tab is active (saves bandwidth)
        include_closed: $('#pills-history-tab').hasClass('active'),
        ltp_req: null
    };

    // If Import Modal is Open, piggyback the LTP request
    if ($('#importModal').is(':visible')) {
        let s = $('#imp_sym').val();
        let e = $('#imp_exp').val();
        let st = $('#imp_str').val();
        let t = $('input[name="imp_type"]:checked').val();
        
        // Only request if we have enough info
        if (s && e && st && t) {
            payload.ltp_req = { symbol: s, expiry: e, strike: st, type: t };
        }
    }

    // High-Speed Sync Call
    $.ajax({
        url: '/api/sync',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload),
        success: function(response) {
            // 1. Update Header Indices (Nifty/BankNifty)
            if (response.indices) {
                $('#nifty-ltp').text(response.indices.NIFTY || 0);
                $('#banknifty-ltp').text(response.indices.BANKNIFTY || 0);
                $('#sensex-ltp').text(response.indices.SENSEX || 0);
            }

            // 2. Update System Status
            if (response.status) {
                if (response.status.active) {
                    $('#login-status').html('<span class="badge bg-success">System Active 🟢</span>');
                    $('#login-btn-container').hide();
                } else {
                    let st = response.status.state;
                    let badge = 'bg-secondary';
                    if (st === 'FAILED') badge = 'bg-danger';
                    if (st === 'WORKING') badge = 'bg-warning text-dark';
                    
                    $('#login-status').html(`<span class="badge ${badge}">${st} 🔴</span>`);
                    $('#login-btn-container').show();
                    $('#login-link').attr('href', response.status.login_url);
                }
            }

            // 3. Update Import Modal LTP (If requested)
            if (response.specific_ltp > 0) {
                $('#imp_ltp_display').text(response.specific_ltp);
                
                // Auto-fill price input if empty or previously auto-filled
                if ($('#imp_price').val() == "" || $('#imp_price').data('auto') == "true") {
                    $('#imp_price').val(response.specific_ltp).data('auto', "true");
                    if(typeof calcImpFromPts === 'function') calcImpFromPts();
                }
            }
            
            // 4. Update Closed Trades (Only if on History Tab)
            if (response.closed_trades && typeof renderHistoryTable === 'function') {
                renderHistoryTable(response.closed_trades);
            }
        }
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
    updateData(); // Force fresh data on tab switch
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

// --- IMPORT TRADE LOGIC HELPER FUNCTIONS ---

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

    // --- SYMBOL SPECIFIC OVERRIDE LOGIC ---
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
        pts = 20;
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

// Legacy fallback
function fetchLTP() {
    console.log("Using UpdateData for LTP.");
}
