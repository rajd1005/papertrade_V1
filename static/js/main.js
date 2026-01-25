// Global State for ORB
let orbLotSize = 50; 
let orbCheckInterval = null;

$(document).ready(function() {
    // Change 3000 to 1000 (1 second) or 500 (0.5 second)
    const REFRESH_INTERVAL = 500;
    // ---------------------

    console.log("🚀 RD Algo Terminal Loaded");

    // Initialize Bootstrap Tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
      return new bootstrap.Tooltip(tooltipTriggerEl)
    })

    renderWatchlist();
    if(typeof loadSettings === 'function') loadSettings();
    
    // --- ORB STRATEGY INIT ---
    if ($('#orb_status_badge').length) {
        loadOrbStatus();
        // Poll status every 3 seconds to keep UI in sync
        orbCheckInterval = setInterval(loadOrbStatus, 3000);
    }

    // Date Logic
    let now = new Date(); 
    const offset = now.getTimezoneOffset(); 
    let localDate = new Date(now.getTime() - (offset*60*1000));
    
    // 1. Set History Date (Existing)
    $('#hist_date').val(localDate.toISOString().slice(0,10)); 
    
    // 2. Set Import Time to Now (New Feature)
    $('#imp_time').val(localDate.toISOString().slice(0,16)); 
    
    // Global Bindings
    $('#hist_date, #hist_filter').change(loadClosedTrades);
    $('#active_filter').change(updateData);
    
    $('input[name="type"]').change(function() {
        let s = $('#sym').val();
        if(s) loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts');
    });
    
    $('#sl_pts, #qty, #lim_pr, #ord').on('input change', calcRisk);
    
    // Bind Search Logic
    bindSearch('#sym', '#sym_list'); 
    bindSearch('#imp_sym', '#sym_list'); 
    bindSearch('#new_watch_sym', '#sym_list'); // Added binding for settings

    // Chain & input Bindings
    $('#sym').change(() => loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts'));
    $('#exp').change(() => fillChain('#sym', '#exp', 'input[name="type"]:checked', '#str'));
    $('#ord').change(function() { if($(this).val() === 'LIMIT') $('#lim_box').show(); else $('#lim_box').hide(); });
    $('#str').change(fetchLTP);

    // Import Modal Bindings
    $('#imp_sym').change(() => loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts')); 
    $('#imp_exp').change(() => fillChain('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_str'));
    
    // 3. Bind Strike Change to fetch LTP (New Feature)
    $('#imp_str').change(fetchLTP);

    $('input[name="imp_type"]').change(() => loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts'));
    
    // Import Risk Calc Bindings
    $('#imp_price').on('input', function() { calcImpFromPts(); }); 
    $('#imp_sl_pts').on('input', calcImpFromPts);
    $('#imp_sl_price').on('input', calcImpFromPrice);
    
    // --- NEW: Import Modal "Full" Checkbox Listeners ---
    ['t1', 't2', 't3'].forEach(k => {
        $(`#imp_${k}_full`).change(function() {
            if($(this).is(':checked')) {
                $(`#imp_${k}_lots`).val(1000).prop('readonly', true);
            } else {
                $(`#imp_${k}_lots`).prop('readonly', false);
                // Optional: restore default lots? For now just unlock.
                if($(`#imp_${k}_lots`).val() == 1000) $(`#imp_${k}_lots`).val(0); 
            }
        });
    });

    // Auto-Remove Floating Notifications
    setTimeout(function() {
        $('.floating-alert').fadeOut('slow', function() {
            $(this).remove();
        });
    }, 4000); 

    // Loops
    setInterval(updateClock, 1000); updateClock();
    
    // Trade Data Update Loop (Uses Configured Interval)
    setInterval(updateData, REFRESH_INTERVAL); updateData();
});

// --- ORB STRATEGY FUNCTIONS ---

function loadOrbStatus() {
    $.get('/api/orb/params', function(data) {
        // 1. Update Lot Size from Server
        if (data.lot_size && data.lot_size > 0) {
            orbLotSize = data.lot_size;
            $('#orb_lot_size').text(orbLotSize);
        }

        // 2. Update UI based on Active State
        if (data.active) {
            $('#orb_status_badge')
                .removeClass('bg-secondary')
                .addClass('bg-success')
                .text('RUNNING');
            
            // Show STOP, Hide START
            $('#btn_orb_start').addClass('d-none');
            $('#btn_orb_stop').removeClass('d-none');
            
            // Lock Input and set value to what is currently running
            // We use .data() to prevent overwriting user typing if they are just looking
            if (!$('#orb_lots_input').is(':focus')) {
                $('#orb_lots_input').val(data.current_lots);
            }
            $('#orb_lots_input').prop('disabled', true);
            
        } else {
            $('#orb_status_badge')
                .removeClass('bg-success')
                .addClass('bg-secondary')
                .text('STOPPED');
            
            // Show START, Hide STOP
            $('#btn_orb_start').removeClass('d-none');
            $('#btn_orb_stop').addClass('d-none');
            
            // Unlock Input
            $('#orb_lots_input').prop('disabled', false);
        }
        
        // 3. Recalculate Totals
        updateOrbCalc();
    });
}

function updateOrbCalc() {
    // Get user input (ensure at least 1)
    let userLots = parseInt($('#orb_lots_input').val()) || 0;
    if (userLots < 1) userLots = 1;
    
    // Calculate Total
    let totalQty = userLots * orbLotSize;
    
    // Update Text
    $('#orb_total_qty').text(totalQty);
}

function toggleOrb(action) {
    let lots = parseInt($('#orb_lots_input').val()) || 1;
    
    // Safety check
    if (action === 'start' && lots < 1) {
        alert("Please enter at least 1 lot.");
        return;
    }

    // Disable buttons to prevent double-click
    $('#btn_orb_start, #btn_orb_stop').prop('disabled', true);
    
    let payload = {
        action: action,
        lots: lots
    };

    $.ajax({
        url: '/api/orb/toggle',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload),
        success: function(res) {
            // Show success message
            if(window.showFloatingAlert) {
                showFloatingAlert(res.message, res.status === 'success' ? 'success' : 'danger');
            } else {
                alert(res.message);
            }
            
            // Refresh status immediately
            loadOrbStatus();
        },
        error: function(err) {
            alert("Request Failed: " + err.statusText);
        },
        complete: function() {
            // Re-enable buttons
            $('#btn_orb_start, #btn_orb_stop').prop('disabled', false);
        }
    });
}

// --- CORE DASHBOARD FUNCTIONS ---

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

// --- IMPORT TRADE LOGIC ---

// Helper for Quantity +/- Buttons
function adjImpQty(dir) {
    let q = $('#imp_qty');
    let v = parseInt(q.val()) || 0;
    // Attempt to use global curLotSize from trade.js, default to 1 if missing
    let step = (typeof curLotSize !== 'undefined' && curLotSize > 0) ? curLotSize : 1;
    let n = v + (dir * step);
    if(n < step) n = step;
    q.val(n);
}

// New Helpers for Import SL Calculation
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

// --- UPDATED: Calculate Import Targets with Symbol Overrides ---
function calculateImportTargets(entry, pts) {
    if(!entry || !pts) return;
    
    // Default Ratios from Paper Settings
    let ratios = settings.modes.PAPER.ratios || [0.5, 1.0, 1.5];
    let t1_pts = pts * ratios[0];
    let t2_pts = pts * ratios[1];
    let t3_pts = pts * ratios[2];

    // --- CHECK FOR SYMBOL SPECIFIC OVERRIDE ---
    let sVal = $('#imp_sym').val();
    if(sVal) {
        // Normalize symbol (remove expiry/exchange parts)
        // Use global normalize function if available, else simple split
        let normS = (typeof normalizeSymbol === 'function') 
            ? normalizeSymbol(sVal) 
            : sVal.split(':')[0].trim().toUpperCase();
        
        let paperSettings = settings.modes.PAPER;
        if(paperSettings && paperSettings.symbol_sl && paperSettings.symbol_sl[normS]) {
            let sData = paperSettings.symbol_sl[normS];
            
            // Check if object structure exists and has targets (Points)
            if (typeof sData === 'object' && sData.targets && sData.targets.length === 3) {
                // Use specific points defined in global settings for this symbol
                // Override the ratio-based points
                t1_pts = sData.targets[0];
                t2_pts = sData.targets[1];
                t3_pts = sData.targets[2];
            }
        }
    }
    // ------------------------------------------

    $('#imp_t1').val((entry + t1_pts).toFixed(2));
    $('#imp_t2').val((entry + t2_pts).toFixed(2));
    $('#imp_t3').val((entry + t3_pts).toFixed(2));
    
    // Visual & Readonly update for full exit checkboxes
    ['t1', 't2', 't3'].forEach(k => {
        if ($(`#imp_${k}_full`).is(':checked')) {
            $(`#imp_${k}_lots`).val(1000).prop('readonly', true);
        } else {
            $(`#imp_${k}_lots`).prop('readonly', false);
        }
    });
}

function calculateImportRisk() {
    // Triggered by Button: Use existing values to refresh targets, or default if empty
    let entry = parseFloat($('#imp_price').val()) || 0;
    let pts = parseFloat($('#imp_sl_pts').val()) || 0;
    let price = parseFloat($('#imp_sl_price').val()) || 0;
    
    if(entry === 0) return;

    if (pts > 0) {
        // Recalc price based on points
        $('#imp_sl_price').val((entry - pts).toFixed(2));
    } else if (price > 0) {
        // Recalc points based on price
        pts = entry - price;
        $('#imp_sl_pts').val(pts.toFixed(2));
    } else {
        // Default fallbacks
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
        sl: parseFloat($('#imp_sl_price').val()), // Send SL Price to Backend
        
        // Broadcast Channel
        target_channel: $('input[name="imp_channel"]:checked').val() || 'main',

        // New Settings
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

// --- ALERT HELPER (Fallback) ---
if (typeof showFloatingAlert === 'undefined') {
    window.showFloatingAlert = function(message, type='primary') {
        let alertHtml = `
            <div class="alert alert-${type} alert-dismissible fade show floating-alert py-3 border-0" role="alert">
                <span class="fw-bold">${message}</span>
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
        // Append to container if exists, else body
        if ($('.notification-container').length) {
            $('.notification-container').append(alertHtml);
        } else {
            $('body').append('<div class="notification-container" style="position: fixed; top: 20px; right: 20px; z-index: 9999;">' + alertHtml + '</div>');
        }
        
        // Auto dismiss
        setTimeout(() => {
            $('.alert').last().alert('close');
        }, 5000);
    };
}
