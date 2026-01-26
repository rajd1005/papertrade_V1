// Global State
let orbLotSize = 50; 
let orbCheckInterval = null;
let isOrbFirstLoad = true; // Flag to track initial load for sync logic

// --- 1. STRICT ENFORCEMENT LOGIC ---
function enforceStrictReEntryRules() {
    let dir = $('#orb_direction').val(); 
    let $oppCheckbox = $('#orb_reentry_opposite');
    let isBotRunning = $('#orb_direction').prop('disabled'); 

    if (dir && dir !== 'BOTH') {
        if (!$oppCheckbox.prop('disabled')) $oppCheckbox.prop('disabled', true);
        if ($oppCheckbox.prop('checked')) $oppCheckbox.prop('checked', false);
    } 
    else if (!isBotRunning) {
        if ($oppCheckbox.prop('disabled')) $oppCheckbox.prop('disabled', false);
    }
}

$(document).ready(function() {
    const REFRESH_INTERVAL = 1000; 
    console.log("🚀 RD Algo Terminal Loaded");

    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) { return new bootstrap.Tooltip(tooltipTriggerEl) })

    renderWatchlist();
    if(typeof loadSettings === 'function') loadSettings();
    
    // --- ORB STRATEGY INIT ---
    if ($('#orb_status_badge').length) {
        loadOrbStatus();
        orbCheckInterval = setInterval(loadOrbStatus, 3000);
    }
    
    $('#orb_direction').change(enforceStrictReEntryRules);
    $('.orb-leg-input, .orb-leg-check').on('input change', updateOrbCalc);
    
    $('#orb_reentry_opposite').on('click mousedown mouseup change', function(e) {
        let dir = $('#orb_direction').val();
        if (dir && dir !== 'BOTH') {
            e.preventDefault();
            e.stopPropagation();
            $(this).prop('checked', false);
            $(this).prop('disabled', true);
            return false;
        }
    });

    let now = new Date(); 
    const offset = now.getTimezoneOffset(); 
    let localDate = new Date(now.getTime() - (offset*60*1000));
    $('#hist_date').val(localDate.toISOString().slice(0,10));
    
    // Default Backtest Date
    if($('#orb_backtest_date').length) $('#orb_backtest_date').val(localDate.toISOString().slice(0,10));
    if($('#imp_time').length) $('#imp_time').val(localDate.toISOString().slice(0,16));
    
    $('#hist_date, #hist_filter').change(loadClosedTrades);
    $('#active_filter').change(updateData);
    
    $('input[name="type"]').change(function() {
        let s = $('#sym').val();
        if(s) loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts');
    });
    
    $('#sl_pts, #qty, #lim_pr, #ord').on('input change', calcRisk);
    
    bindSearch('#sym', '#sym_list'); 
    bindSearch('#imp_sym', '#sym_list'); 
    bindSearch('#new_watch_sym', '#sym_list');

    $('#sym').change(() => loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts'));
    $('#exp').change(() => fillChain('#sym', '#exp', 'input[name="type"]:checked', '#str'));
    $('#ord').change(function() { if($(this).val() === 'LIMIT') $('#lim_box').show(); else $('#lim_box').hide(); });
    $('#str').change(fetchLTP);

    $('#imp_sym').change(() => loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts')); 
    $('#imp_exp').change(() => fillChain('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_str'));
    $('#imp_str').change(fetchLTP); 

    $('input[name="imp_type"]').change(() => loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts'));
    
    $('#imp_price').on('input', function() { calcImpFromPts(); }); 
    $('#imp_sl_pts').on('input', calcImpFromPts);
    $('#imp_sl_price').on('input', calcImpFromPrice);
    
    ['t1', 't2', 't3'].forEach(k => {
        $(`#imp_${k}_full`).change(function() {
            if($(this).is(':checked')) {
                $(`#imp_${k}_lots`).val(1000).prop('readonly', true);
            } else {
                $(`#imp_${k}_lots`).prop('readonly', false).val(0); 
            }
        });
    });

    setInterval(updateClock, 1000); updateClock();
    setInterval(updateData, REFRESH_INTERVAL); updateData();
});

// ==========================================
// ORB STRATEGY FUNCTIONS
// ==========================================

function loadOrbStatus() {
    $.get('/api/orb/params', function(data) {
        if (data.lot_size && data.lot_size > 0) {
            orbLotSize = data.lot_size;
            $('#orb_lot_size').text(orbLotSize);
        }

        let shouldSync = data.active || isOrbFirstLoad;

        if (shouldSync) {
            // Legs
            if(data.legs_config && Array.isArray(data.legs_config)) {
                for(let i=0; i<3; i++) {
                    let leg = data.legs_config[i];
                    if(leg) {
                        if (!$(`#orb_leg${i+1}_lots`).is(':focus')) $(`#orb_leg${i+1}_lots`).val(leg.lots);
                        if (!$(`#orb_leg${i+1}_ratio`).is(':focus')) $(`#orb_leg${i+1}_ratio`).val(leg.ratio);
                        $(`#orb_leg${i+1}_active`).prop('checked', leg.active !== false);
                        $(`#orb_leg${i+1}_full`).prop('checked', leg.full);
                        $(`#orb_leg${i+1}_trail`).prop('checked', leg.trail);
                    } else {
                        if (!$(`#orb_leg${i+1}_lots`).is(':focus')) $(`#orb_leg${i+1}_lots`).val(0);
                        $(`#orb_leg${i+1}_active`).prop('checked', i===0);
                    }
                }
            }

            // Risk
            let r = data.risk || {};
            if (!$('#orb_max_loss').is(':focus')) $('#orb_max_loss').val(r.max_loss);
            if (!$('#orb_trail_pts').is(':focus')) $('#orb_trail_pts').val(r.trail_pts);
            $('#orb_sl_to_entry').val(r.sl_entry);
            
            if (!$('#orb_profit_active').is(':focus')) $('#orb_profit_active').val(r.p_active);
            if (!$('#orb_profit_min').is(':focus')) $('#orb_profit_min').val(r.p_min);
            if (!$('#orb_profit_trail').is(':focus')) $('#orb_profit_trail').val(r.p_trail);
            
            // PnL
            let pnl = parseFloat(r.session_pnl || 0);
            $('#orb_session_pnl').text(pnl.toFixed(2));
            if(pnl >= 0) $('#orb_session_pnl').removeClass('text-danger').addClass('text-success');
            else $('#orb_session_pnl').removeClass('text-success').addClass('text-danger');

            // Main
            if (!$('#orb_mode_input').is(':focus')) $('#orb_mode_input').val(data.current_mode);
            if (!$('#orb_direction').is(':focus')) $('#orb_direction').val(data.current_direction);
            if (!$('#orb_cutoff').is(':focus')) $('#orb_cutoff').val(data.current_cutoff);

            $('#orb_reentry_same_sl').prop('checked', data.re_sl);
            $('#orb_reentry_same_filter').val(data.re_sl_filter);
            $('#orb_reentry_opposite').prop('checked', data.re_opp);
        }

        if (data.active) {
            $('#orb_status_badge').removeClass('bg-secondary').addClass('bg-success').text('RUNNING');
            $('#btn_orb_start').addClass('d-none');
            $('#btn_orb_stop').removeClass('d-none');

            // Lock Inputs including .orb-trail-check
            $('.orb-leg-input, .orb-leg-check, .orb-full-check, .orb-trail-check').prop('disabled', true); 
            $('#orb_mode_input, #orb_direction, #orb_cutoff').prop('disabled', true);
            $('#orb_reentry_same_sl, #orb_reentry_same_filter, #orb_reentry_opposite').prop('disabled', true);
            
            $('#orb_max_loss, #orb_trail_pts, #orb_sl_to_entry').prop('disabled', true);
            $('#orb_profit_active, #orb_profit_min, #orb_profit_trail').prop('disabled', true);
            
        } else {
            $('#orb_status_badge').removeClass('bg-success').addClass('bg-secondary').text('STOPPED');
            $('#btn_orb_start').removeClass('d-none');
            $('#btn_orb_stop').addClass('d-none');
            
            // Unlock Inputs
            $('.orb-leg-input, .orb-leg-check, .orb-full-check, .orb-trail-check').prop('disabled', false); 
            $('#orb_mode_input, #orb_direction, #orb_cutoff').prop('disabled', false);
            $('#orb_reentry_same_sl, #orb_reentry_same_filter').prop('disabled', false);
            
            $('#orb_max_loss, #orb_trail_pts, #orb_sl_to_entry').prop('disabled', false);
            $('#orb_profit_active, #orb_profit_min, #orb_profit_trail').prop('disabled', false);
        }
        
        enforceStrictReEntryRules();
        updateOrbCalc();
        isOrbFirstLoad = false;
    });
}

function updateOrbCalc() {
    let l1 = $('#orb_leg1_active').is(':checked') ? (parseInt($('#orb_leg1_lots').val()) || 0) : 0;
    let l2 = $('#orb_leg2_active').is(':checked') ? (parseInt($('#orb_leg2_lots').val()) || 0) : 0;
    let l3 = $('#orb_leg3_active').is(':checked') ? (parseInt($('#orb_leg3_lots').val()) || 0) : 0;
    
    let totalLots = l1 + l2 + l3;
    $('#orb_calc_total').text(totalLots);
    $('#orb_total_qty').text(totalLots * orbLotSize);
}

function toggleOrb(action) {
    let mode = $('#orb_mode_input').val(); 
    let direction = $('#orb_direction').val();
    let cutoff = $('#orb_cutoff').val();

    let re_sl = $('#orb_reentry_same_sl').is(':checked');
    let re_sl_filter = $('#orb_reentry_same_filter').val();
    let re_opp = $('#orb_reentry_opposite').is(':checked');
    if (direction !== 'BOTH') re_opp = false;

    // Scrape Legs
    let legs_config = [];
    for(let i=1; i<=3; i++) {
        legs_config.push({
            active: $(`#orb_leg${i}_active`).is(':checked'),
            lots: parseInt($(`#orb_leg${i}_lots`).val()) || 0,
            full: $(`#orb_leg${i}_full`).is(':checked'),
            ratio: parseFloat($(`#orb_leg${i}_ratio`).val()) || 0.0,
            trail: $(`#orb_leg${i}_trail`).is(':checked')
        });
    }

    // Scrape Risk
    let risk = {
        max_loss: parseFloat($('#orb_max_loss').val()) || 0,
        trail_pts: parseFloat($('#orb_trail_pts').val()) || 0,
        sl_entry: parseInt($('#orb_sl_to_entry').val()) || 0,
        p_active: parseFloat($('#orb_profit_active').val()) || 0,
        p_min: parseFloat($('#orb_profit_min').val()) || 0,
        p_trail: parseFloat($('#orb_profit_trail').val()) || 0
    };

    let totalLots = 0;
    legs_config.forEach(l => { if(l.active) totalLots += l.lots; });

    if (action === 'start') {
        if (totalLots < 1) { alert("Total Active Lots cannot be 0. Please enable and configure at least one leg."); return; }
        if (!cutoff) { alert("Please select a valid Cutoff Time."); return; }
    }

    $('#btn_orb_start, #btn_orb_stop').prop('disabled', true);
    
    let payload = {
        action: action,
        mode: mode, direction: direction, cutoff: cutoff,
        re_sl: re_sl, re_sl_filter: re_sl_filter, re_opp: re_opp,
        legs_config: legs_config,
        risk: risk
    };

    $.ajax({
        url: '/api/orb/toggle',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload),
        success: function(res) {
            if(window.showFloatingAlert) showFloatingAlert(res.message, res.status === 'success' ? 'success' : 'danger');
            else alert(res.message);
            loadOrbStatus();
        },
        error: function(err) { alert("Request Failed: " + err.statusText); },
        complete: function() { $('#btn_orb_start, #btn_orb_stop').prop('disabled', false); }
    });
}

function runOrbBacktest() {
    let date = $('#orb_backtest_date').val();
    if(!date) { alert("Please select a date."); return; }
    
    let btn = $(event.target);
    let originalText = btn.html();
    btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> Running...');
    
    // --- SCRAPE UI SETTINGS TO SEND ---
    let legs_config = [];
    for(let i=1; i<=3; i++) {
        legs_config.push({
            active: $(`#orb_leg${i}_active`).is(':checked'),
            lots: parseInt($(`#orb_leg${i}_lots`).val()) || 0,
            full: $(`#orb_leg${i}_full`).is(':checked'),
            ratio: parseFloat($(`#orb_leg${i}_ratio`).val()) || 0.0,
            trail: $(`#orb_leg${i}_trail`).is(':checked')
        });
    }

    let risk = {
        trail_pts: parseFloat($('#orb_trail_pts').val()) || 0,
        sl_entry: parseInt($('#orb_sl_to_entry').val()) || 0
    };

    $.ajax({
        url: '/api/orb/backtest',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ 
            date: date,
            execute: true,
            legs_config: legs_config, // Pass legs
            risk: risk // Pass risk
        }),
        success: function(res) {
            if(res.status === 'success') {
                if(window.showFloatingAlert) showFloatingAlert(res.message, 'success');
                else alert(res.message);
                if(typeof updateData === 'function') updateData(); 
            } else {
                alert(res.message);
            }
        },
        error: function(err) { alert("Backtest Failed: " + err.statusText); },
        complete: function() { btn.prop('disabled', false).html(originalText); }
    });
}

// ... (Rest of dashboard functions remain unchanged) ...
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
