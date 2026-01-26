// Global State
let orbLotSize = 50; 
let orbCheckInterval = null;

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
    
    // --- BINDINGS FOR ORB ---
    $('#orb_direction').change(enforceStrictReEntryRules);
    
    // Auto-calculate Total Lots when Leg lots change
    $('.orb-leg-input').on('input change', updateOrbCalc);

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

        // Populate Legs if available (Support up to 3)
        if(data.legs_config && Array.isArray(data.legs_config)) {
            for(let i=0; i<3; i++) {
                let leg = data.legs_config[i];
                if(leg) {
                    $(`#orb_leg${i+1}_lots`).val(leg.lots);
                    $(`#orb_leg${i+1}_ratio`).val(leg.ratio);
                    $(`#orb_leg${i+1}_trail`).prop('checked', leg.trail);
                } else {
                    // Default zero if not configured
                    $(`#orb_leg${i+1}_lots`).val(0);
                }
            }
        }

        if (data.active) {
            $('#orb_status_badge').removeClass('bg-secondary').addClass('bg-success').text('RUNNING');
            $('#btn_orb_start').addClass('d-none');
            $('#btn_orb_stop').removeClass('d-none');
            
            if (!$('#orb_mode_input').is(':focus')) $('#orb_mode_input').val(data.current_mode);
            if (!$('#orb_direction').is(':focus')) $('#orb_direction').val(data.current_direction);
            if (!$('#orb_cutoff').is(':focus')) $('#orb_cutoff').val(data.current_cutoff);

            $('#orb_reentry_same_sl').prop('checked', data.re_sl);
            $('#orb_reentry_same_filter').val(data.re_sl_filter);
            $('#orb_reentry_opposite').prop('checked', data.re_opp);

            // Disable Inputs
            $('#orb_mode_input, #orb_direction, #orb_cutoff').prop('disabled', true);
            $('.orb-leg-input').prop('disabled', true); // Lock leg inputs
            $('#orb_reentry_same_sl, #orb_reentry_same_filter, #orb_reentry_opposite').prop('disabled', true);
            
        } else {
            $('#orb_status_badge').removeClass('bg-success').addClass('bg-secondary').text('STOPPED');
            $('#btn_orb_start').removeClass('d-none');
            $('#btn_orb_stop').addClass('d-none');
            
            // Enable Inputs
            $('#orb_mode_input, #orb_direction, #orb_cutoff').prop('disabled', false);
            $('.orb-leg-input').prop('disabled', false); 
            $('#orb_reentry_same_sl, #orb_reentry_same_filter').prop('disabled', false);
        }
        
        enforceStrictReEntryRules();
        updateOrbCalc();
    });
}

function updateOrbCalc() {
    let l1 = parseInt($('#orb_leg1_lots').val()) || 0;
    let l2 = parseInt($('#orb_leg2_lots').val()) || 0;
    let l3 = parseInt($('#orb_leg3_lots').val()) || 0;
    
    let totalLots = l1 + l2 + l3;
    $('#orb_calc_total').text(totalLots);
    
    let totalQty = totalLots * orbLotSize;
    $('#orb_total_qty').text(totalQty);
}

function toggleOrb(action) {
    let mode = $('#orb_mode_input').val(); 
    let direction = $('#orb_direction').val();
    let cutoff = $('#orb_cutoff').val();

    let re_sl = $('#orb_reentry_same_sl').is(':checked');
    let re_sl_filter = $('#orb_reentry_same_filter').val();
    let re_opp = $('#orb_reentry_opposite').is(':checked');
    if (direction !== 'BOTH') re_opp = false;

    // Scrape Legs Config
    let legs_config = [];
    for(let i=1; i<=3; i++) {
        legs_config.push({
            lots: parseInt($(`#orb_leg${i}_lots`).val()) || 0,
            ratio: parseFloat($(`#orb_leg${i}_ratio`).val()) || 0.0,
            trail: $(`#orb_leg${i}_trail`).is(':checked')
        });
    }

    // Validation
    let totalLots = legs_config.reduce((sum, leg) => sum + leg.lots, 0);

    if (action === 'start') {
        if (totalLots < 1) { alert("Total Lots cannot be 0."); return; }
        if (!cutoff) { alert("Please select a valid Cutoff Time."); return; }
    }

    $('#btn_orb_start, #btn_orb_stop').prop('disabled', true);
    
    let payload = {
        action: action,
        mode: mode,
        direction: direction,
        cutoff: cutoff,
        re_sl: re_sl,
        re_sl_filter: re_sl_filter,
        re_opp: re_opp,
        legs_config: legs_config // Send array
    };

    $.ajax({
        url: '/api/orb/toggle',
        type: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload),
        success: function(res) {
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

// ... Rest of the file (updateDisplayValues, switchTab, etc.) remains unchanged ...
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
    let ratios = settings.modes.PAPER.ratios || [0.5, 1.0, 1.5];
    let t1_pts = pts * ratios[0];
    let t2_pts = pts * ratios[1];
    let t3_pts = pts * ratios[2];

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
