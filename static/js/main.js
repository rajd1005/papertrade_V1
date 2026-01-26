// Global State
let orbLotSize = 50; 
let orbCheckInterval = null;
let isOrbFirstLoad = true; // <--- NEW: Flag to track initial load

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
    const REFRESH_INTERVAL = 1000; 
    console.log("🚀 RD Algo Terminal Loaded");

    // Init Tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) { return new bootstrap.Tooltip(tooltipTriggerEl) })

    // Load Initial Data
    renderWatchlist();
    if(typeof loadSettings === 'function') loadSettings();
    
    // --- ORB STRATEGY INIT ---
    if ($('#orb_status_badge').length) {
        loadOrbStatus();
        // Poll status every 3 seconds
        orbCheckInterval = setInterval(loadOrbStatus, 3000);
    }
    
    // --- 2. BINDINGS FOR STRICT RULES ---
    
    // A. When Direction Changes -> Enforce Rules Immediately
    $('#orb_direction').change(enforceStrictReEntryRules);

    // B. NEW: Bind Calculation to Multi-Leg Inputs
    // This handles real-time updates of "Total Qty" when user changes leg lots
    $('.orb-leg-input').on('input change', updateOrbCalc);
    
    // C. Intercept ALL interactions on the Checkbox
    // Catches clicks, double-clicks, and keyboard toggles
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

    // Date & Time Defaults
    let now = new Date(); 
    const offset = now.getTimezoneOffset(); 
    let localDate = new Date(now.getTime() - (offset*60*1000));
    $('#hist_date').val(localDate.toISOString().slice(0,10));
    if($('#imp_time').length) $('#imp_time').val(localDate.toISOString().slice(0,16));
    
    // Global Event Bindings
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

    // Import Modal Bindings
    $('#imp_sym').change(() => loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts')); 
    $('#imp_exp').change(() => fillChain('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_str'));
    $('#imp_str').change(fetchLTP); 

    $('input[name="imp_type"]').change(() => loadDetails('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_qty', '#imp_sl_pts'));
    
    // Import Risk Calc
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

    // Loops
    setInterval(updateClock, 1000); updateClock();
    setInterval(updateData, REFRESH_INTERVAL); updateData();
});

// ==========================================
// ORB STRATEGY FUNCTIONS
// ==========================================

function loadOrbStatus() {
    $.get('/api/orb/params', function(data) {
        // 1. Update Lot Size (Always update this as it's informational)
        if (data.lot_size && data.lot_size > 0) {
            orbLotSize = data.lot_size;
            $('#orb_lot_size').text(orbLotSize);
        }

        // --- SYNCHRONIZATION LOGIC ---
        // We ONLY sync inputs from the server if:
        // A) The Bot is RUNNING (Server is Master)
        // B) It is the FIRST load of the page (To populate defaults/last state)
        // If Bot is STOPPED and it's NOT the first load, we DO NOT touch inputs 
        // to prevent overwriting user edits while they are typing.
        let shouldSync = data.active || isOrbFirstLoad;

        if (shouldSync) {
            // 2. Populate Multi-Leg Inputs
            if(data.legs_config && Array.isArray(data.legs_config)) {
                for(let i=0; i<3; i++) {
                    let leg = data.legs_config[i];
                    if(leg) {
                        // Use :focus check just in case, though shouldSync handles most cases
                        if (!$(`#orb_leg${i+1}_lots`).is(':focus')) $(`#orb_leg${i+1}_lots`).val(leg.lots);
                        if (!$(`#orb_leg${i+1}_ratio`).is(':focus')) $(`#orb_leg${i+1}_ratio`).val(leg.ratio);
                        $(`#orb_leg${i+1}_trail`).prop('checked', leg.trail);
                    } else {
                        if (!$(`#orb_leg${i+1}_lots`).is(':focus')) $(`#orb_leg${i+1}_lots`).val(0);
                    }
                }
            }

            // 3. Sync Main Parameters
            if (!$('#orb_mode_input').is(':focus')) $('#orb_mode_input').val(data.current_mode);
            if (!$('#orb_direction').is(':focus')) $('#orb_direction').val(data.current_direction);
            if (!$('#orb_cutoff').is(':focus')) $('#orb_cutoff').val(data.current_cutoff);

            // 4. Sync Re-entry Settings
            $('#orb_reentry_same_sl').prop('checked', data.re_sl);
            $('#orb_reentry_same_filter').val(data.re_sl_filter);
            $('#orb_reentry_opposite').prop('checked', data.re_opp);
        }

        // --- UI STATE MANAGEMENT (Running vs Stopped) ---
        if (data.active) {
            // --- RUNNING STATE ---
            $('#orb_status_badge').removeClass('bg-secondary').addClass('bg-success').text('RUNNING');
            
            $('#btn_orb_start').addClass('d-none');
            $('#btn_orb_stop').removeClass('d-none');

            // Lock ALL Inputs
            $('.orb-leg-input').prop('disabled', true); 
            $('#orb_mode_input, #orb_direction, #orb_cutoff').prop('disabled', true);
            $('#orb_reentry_same_sl, #orb_reentry_same_filter, #orb_reentry_opposite').prop('disabled', true);
            
        } else {
            // --- STOPPED STATE ---
            $('#orb_status_badge').removeClass('bg-success').addClass('bg-secondary').text('STOPPED');
            
            $('#btn_orb_start').removeClass('d-none');
            $('#btn_orb_stop').addClass('d-none');
            
            // Unlock Main Controls
            $('.orb-leg-input').prop('disabled', false); 
            $('#orb_mode_input, #orb_direction, #orb_cutoff').prop('disabled', false);
            $('#orb_reentry_same_sl, #orb_reentry_same_filter').prop('disabled', false);
        }
        
        // 5. Cleanup
        enforceStrictReEntryRules();
        updateOrbCalc();
        isOrbFirstLoad = false; // Disable first-load sync after first run
    });
}

function updateOrbCalc() {
    // Read from new 3-Leg Inputs
    let l1 = parseInt($('#orb_leg1_lots').val()) || 0;
    let l2 = parseInt($('#orb_leg2_lots').val()) || 0;
    let l3 = parseInt($('#orb_leg3_lots').val()) || 0;
    
    let totalLots = l1 + l2 + l3;
    
    // Update UI
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
    
    // Force Disable Opposite if Direction is restricted (Sanity Check)
    let re_opp = $('#orb_reentry_opposite').is(':checked');
    if (direction !== 'BOTH') re_opp = false;

    // --- SCRAPE LEG CONFIG ---
    let legs_config = [];
    // We support 3 legs fixed in UI
    for(let i=1; i<=3; i++) {
        legs_config.push({
            lots: parseInt($(`#orb_leg${i}_lots`).val()) || 0,
            ratio: parseFloat($(`#orb_leg${i}_ratio`).val()) || 0.0,
            trail: $(`#orb_leg${i}_trail`).is(':checked')
        });
    }

    // Calc Total Lots for Validation
    let totalLots = legs_config.reduce((sum, leg) => sum + leg.lots, 0);

    if (action === 'start') {
        if (totalLots < 1) { alert("Total Lots cannot be 0. Please configure at least one leg."); return; }
        if (!cutoff) { alert("Please select a valid Cutoff Time."); return; }
    }

    $('#btn_orb_start, #btn_orb_stop').prop('disabled', true);
    
    let payload = {
        action: action,
        lots: totalLots, 
        mode: mode,
        direction: direction,
        cutoff: cutoff,
        re_sl: re_sl,
        re_sl_filter: re_sl_filter,
        re_opp: re_opp,
        legs_config: legs_config 
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

// ==========================================
// CORE DASHBOARD FUNCTIONS
// ==========================================

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
