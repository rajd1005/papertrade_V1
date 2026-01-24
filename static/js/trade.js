function loadDetails(symId, expId, typeSelector, qtyId, slId) {
    let s = $(symId).val(); if(!s) return;
    
    let settingsKey = normalizeSymbol(s);
    let selectedMode = $('#mode_input').val();
    let mode = 'PAPER'; 
    
    // --- Determine Mode ---
    if (symId === '#h_sym' || $('#history').is(':visible')) mode = 'SIMULATOR'; 
    else if (symId === '#imp_sym') mode = 'PAPER'; // Import always defaults to PAPER settings
    else {
        mode = (selectedMode === 'SHADOW') ? 'PAPER' : selectedMode;
    }
    
    let modeSettings = settings.modes[mode] || settings.modes.PAPER;
    
    // Update Visuals
    if(symId === '#sym') updateModeVisuals(selectedMode);

    // Auto-fill SL
    if(slId) {
        let rawData = (modeSettings.symbol_sl && modeSettings.symbol_sl[settingsKey]);
        let savedSL = 20; 

        if (rawData) {
            if (typeof rawData === 'object') {
                savedSL = rawData.sl || 20;
            } else {
                savedSL = rawData;
            }
        }
        $(slId).val(savedSL);
    }
    
    // Auto-fill Settings
    if(mode !== 'SIMULATOR') {
        let prefix = (symId === '#imp_sym') ? '#imp_' : '#'; 
        let trailVal = modeSettings.trailing_sl || 0;
        
        if(prefix === '#imp_') {
             // --- IMPORT MODAL POPULATION (FIXED) ---
             $('#imp_trail_sl').val(trailVal);
             $('#imp_trail_limit').val(modeSettings.sl_to_entry || 0);
             $('#imp_exit_mult').val(modeSettings.exit_multiplier || 1);
             
             // Populate Targets (Active | Lots | Full | Cost)
             if(modeSettings.targets) {
                ['t1', 't2', 't3'].forEach((k, i) => {
                    let conf = modeSettings.targets[i] || {};
                    // Checkboxes
                    $(`#imp_${k}_active`).prop('checked', conf.active !== false); 
                    $(`#imp_${k}_full`).prop('checked', conf.full === true);
                    $(`#imp_${k}_cost`).prop('checked', conf.trail_to_entry === true);
                    
                    // Lots & Readonly State
                    if(conf.full) {
                        $(`#imp_${k}_lots`).val(1000).prop('readonly', true);
                    } else {
                        $(`#imp_${k}_lots`).val(conf.lots || 0).prop('readonly', false);
                    }
                });
             }
        } else {
             // --- MAIN TRADE FORM POPULATION ---
             $('#trail_sl').val(trailVal);
             $('#ord').val(modeSettings.order_type || 'MARKET').trigger('change');
             $('select[name="sl_to_entry"]').val(modeSettings.sl_to_entry || 0);
             $('#exit_mult').val(modeSettings.exit_multiplier || 1);
             
             if(modeSettings.targets) {
                ['t1', 't2', 't3'].forEach((k, i) => {
                    let conf = modeSettings.targets[i] || {};
                    $(`#${k}_active`).prop('checked', conf.active !== false);
                    $(`#${k}_full`).prop('checked', conf.full === true);
                    $(`input[name="${k}_cost"]`).prop('checked', conf.trail_to_entry === true);
                    
                    if(conf.full) {
                        $(`#${k}_lots`).val(1000).prop('readonly', true);
                    } else {
                        $(`#${k}_lots`).val(conf.lots || 0).prop('readonly', false);
                    }
                });
             }
        }
    }
    
    if(mode === 'SIMULATOR' && typeof calcSimSL === 'function') calcSimSL('pts'); 
    else if (symId !== '#imp_sym') calcRisk(); 

    $.get('/api/details', {symbol: s}, function(d) { 
        symLTP[symId] = d.ltp; 
        if(d.lot_size > 0) {
            curLotSize = d.lot_size;
            if(symId !== '#imp_sym') $('#lot').text(curLotSize); 
            let mult = parseInt(modeSettings.qty_mult) || 1;
            $(qtyId).val(curLotSize * mult).attr('step', curLotSize).attr('min', curLotSize);
        }
        window[symId+'_fut'] = d.fut_expiries; 
        window[symId+'_opt'] = d.opt_expiries;
        
        let typeVal = $(typeSelector).val();
        if (typeVal) fillExp(expId, typeSelector, symId);
        else {
            $(expId).empty();
            let strId = (expId === '#exp') ? '#str' : (expId === '#imp_exp' ? '#imp_str' : '#h_str');
            $(strId).empty().append('<option>Select Type First</option>');
        }
    });
}

function adjQty(inputId, dir) {
    let val = parseInt($(inputId).val()) || curLotSize;
    let step = curLotSize;
    let newVal = val + (dir * step);
    if(newVal >= step) {
        $(inputId).val(newVal).trigger('input');
    }
}

function adjLiveQty(dir) {
    let val = parseInt($('#live_qty').val()) || curLotSize;
    let step = curLotSize;
    let newVal = val + (dir * step);
    if(newVal >= step) {
        $('#live_qty').val(newVal).trigger('input');
    }
}

function fillExp(expId, typeSelector, symId) { 
    let typeVal = $(typeSelector).val();
    let l = typeVal=='FUT' ? window[symId+'_fut'] : window[symId+'_opt']; 
    let $e = $(expId).empty(); 
    if(l) l.forEach(d => $e.append(`<option value="${d}">${d}</option>`)); 
    
    if(expId === '#exp') fillChain('#sym', '#exp', 'input[name="type"]:checked', '#str');
    if(expId === '#h_exp') fillChain('#h_sym', '#h_exp', 'input[name="h_type"]:checked', '#h_str');
    if(expId === '#imp_exp') fillChain('#imp_sym', '#imp_exp', 'input[name="imp_type"]:checked', '#imp_str');
}

function fillChain(sym, exp, typeSelector, str) {
    let spot = symLTP[sym] || 0; 
    let sVal = $(sym).val(); if(!sVal) return;
    if(sVal.includes(':')) sVal = sVal.split(':')[0].trim();
    
    $.get('/api/chain', {
        symbol: sVal, 
        expiry: $(exp).val(), 
        type: $(typeSelector).val(), 
        ltp: spot
    }, function(d) {
        let $s = $(str).empty(); 
        d.forEach(r => { 
            let style = r.label.includes('ATM') ? 'style="color:red; font-weight:bold;"' : ''; 
            let selected = r.label.includes('ATM') ? 'selected' : ''; 
            let mark = r.label.includes('ATM') ? '🔴' : '';
            $s.append(`<option value="${r.strike}" ${selected} ${style}>${mark} ${r.strike} ${r.label}</option>`); 
        });
        if(sym === '#imp_sym') fetchLTP();
    });
}

function fetchLTP() {
    let sVal = $('#sym').val(); 
    if(!sVal) return;
    if(sVal.includes(':')) sVal = sVal.split(':')[0].trim();
    
    $.get('/api/specific_ltp', {
        symbol: sVal, 
        expiry: $('#exp').val(), 
        strike: $('#str').val(), 
        type: $('input[name="type"]:checked').val()
    }, function(d) {
        if(d.ltp) {
            curLTP = d.ltp; 
            $('#inst_ltp').text("LTP: " + curLTP); 
            if ($('#ord').val() === 'LIMIT' && !$('#lim_pr').val()) $('#lim_pr').val(curLTP);
            calcRisk();
        }
    });

    if($('#importModal').is(':visible')) {
        let iSym = $('#imp_sym').val();
        if(iSym && $('#imp_exp').val() && $('#imp_str').val()) {
             $.get('/api/specific_ltp', {
                symbol: iSym, 
                expiry: $('#imp_exp').val(), 
                strike: $('#imp_str').val(), 
                type: $('input[name="imp_type"]:checked').val()
            }, function(d) {
                if(d.ltp > 0) {
                    $('#imp_ltp').text("LTP: "+d.ltp);
                    if(!$('#imp_price').val()) $('#imp_price').val(d.ltp);
                }
            });
        }
    }
}

function calcSLPriceFromPts(ptsId, priceId) {
    let pts = parseFloat($(ptsId).val()) || 0;
    let basePrice = ($('#ord').val() === 'LIMIT' && $('#lim_pr').val() > 0) ? parseFloat($('#lim_pr').val()) : curLTP;
    if(basePrice > 0) {
        let price = basePrice - pts;
        $(priceId).val(price.toFixed(2));
        calcRisk();
    }
}

function calcSLPtsFromPrice(priceId, ptsId) {
    let price = parseFloat($(priceId).val()) || 0;
    let basePrice = ($('#ord').val() === 'LIMIT' && $('#lim_pr').val() > 0) ? parseFloat($('#lim_pr').val()) : curLTP;
    if(basePrice > 0 && price > 0) {
        let pts = basePrice - price;
        $(ptsId).val(pts.toFixed(2));
        calcRisk();
    }
}

function calcLiveSLPriceFromPts() {
    let pts = parseFloat($('#live_sl_pts').val()) || 0;
    let basePrice = ($('#ord').val() === 'LIMIT' && $('#lim_pr').val() > 0) ? parseFloat($('#lim_pr').val()) : curLTP;
    if(basePrice > 0) {
        let price = basePrice - pts;
        $('#live_p_sl').val(price.toFixed(2));
        calcLivePnl(); 
    }
}

function calcLiveSLPtsFromPrice() {
    let price = parseFloat($('#live_p_sl').val()) || 0;
    let basePrice = ($('#ord').val() === 'LIMIT' && $('#lim_pr').val() > 0) ? parseFloat($('#lim_pr').val()) : curLTP;
    if(basePrice > 0 && price > 0) {
        let pts = basePrice - price;
        $('#live_sl_pts').val(pts.toFixed(2));
        calcLivePnl(); 
    }
}

function calcLivePnl() {
    let p_input = parseFloat($('#live_sl_pts').val())||0; 
    let qty = parseInt($('#live_qty').val())||1; 
    let basePrice = ($('#ord').val() === 'LIMIT' && $('#lim_pr').val() > 0) ? parseFloat($('#lim_pr').val()) : curLTP;
    
    if(basePrice <= 0) return;
    
    let slPrice = parseFloat($('#live_p_sl').val()) || (basePrice - p_input);
    let pnl_sl = (slPrice - basePrice) * qty;
    $('#live_pnl_sl').text(`₹ ${pnl_sl.toFixed(0)}`);
    
    ['t1', 't2', 't3'].forEach(k => {
        let tPrice = parseFloat($(`#live_p_${k}`).val()) || 0;
        if(tPrice > 0) {
            let pnl = (tPrice - basePrice) * qty;
            $(`#live_pnl_${k}`).text(`₹ ${pnl.toFixed(0)}`);
        }
    });
}

function calcRisk() {
    let p_input = parseFloat($('#sl_pts').val())||0; 
    let qty = parseInt($('#qty').val())||1;
    let basePrice = ($('#ord').val() === 'LIMIT' && $('#lim_pr').val() > 0) ? parseFloat($('#lim_pr').val()) : curLTP;
    
    if(basePrice <= 0) return;

    let selectedMode = $('#mode_input').val();
    let isShadow = (selectedMode === 'SHADOW');

    function getModePnL(modeKey, defaultSL) {
        let mObj = settings.modes[modeKey] || settings.modes.PAPER;
        let ratios = mObj.ratios || [0.5, 1.0, 1.5];
        let effectiveSL = defaultSL;

        let sVal = $('#sym').val();
        if(sVal && mObj.symbol_sl) {
            let normS = normalizeSymbol(sVal);
            let sData = mObj.symbol_sl[normS];
            if (sData) {
                let ovSL = 0;
                if (typeof sData === 'object') ovSL = sData.sl;
                else ovSL = sData;

                if (ovSL > 0) {
                    effectiveSL = ovSL;
                    if (typeof sData === 'object' && sData.targets && sData.targets.length === 3) {
                        ratios = [
                            sData.targets[0] / ovSL,
                            sData.targets[1] / ovSL,
                            sData.targets[2] / ovSL
                        ];
                    }
                }
            }
        }
        
        let t1 = basePrice + effectiveSL * ratios[0];
        let t2 = basePrice + effectiveSL * ratios[1];
        let t3 = basePrice + effectiveSL * ratios[2];
        let slPrice = basePrice - effectiveSL;
        
        return {
            slPts: effectiveSL,
            t1: t1, t2: t2, t3: t3, slPrice: slPrice,
            pnl_t1: (t1 - basePrice) * qty,
            pnl_t2: (t2 - basePrice) * qty,
            pnl_t3: (t3 - basePrice) * qty,
            pnl_sl: (slPrice - basePrice) * qty,
            ratios: ratios,
            trailing: mObj.trailing_sl || 0,
            slEntry: mObj.sl_to_entry || 0,
            exitMult: mObj.exit_multiplier || 1,
            targets: mObj.targets || []
        };
    }

    // 1. MAIN CARD
    let paperMode = isShadow ? 'PAPER' : selectedMode;
    let std = getModePnL(paperMode, p_input);
    
    if (!document.activeElement || !['p_t1', 'p_t2', 'p_t3'].includes(document.activeElement.id)) {
        $('#p_t1').val(std.t1.toFixed(2)); 
        $('#p_t2').val(std.t2.toFixed(2)); 
        $('#p_t3').val(std.t3.toFixed(2));
    }
    if (!document.activeElement || document.activeElement.id !== 'p_sl') {
        $('#p_sl').val(std.slPrice.toFixed(2));
    }
    
    $('#pnl_t1').text(`₹ ${std.pnl_t1.toFixed(0)}`); 
    $('#pnl_t2').text(`₹ ${std.pnl_t2.toFixed(0)}`); 
    $('#pnl_t3').text(`₹ ${std.pnl_t3.toFixed(0)}`);
    $('#pnl_sl').text(`₹ ${std.pnl_sl.toFixed(0)}`);
    
    $('#r_t1').text(std.ratios[0].toFixed(1));
    $('#r_t2').text(std.ratios[1].toFixed(1));
    $('#r_t3').text(std.ratios[2].toFixed(1));

    // 2. LIVE CARD
    if (isShadow) {
        let liveActive = document.activeElement && document.activeElement.id.startsWith('live_');
        
        if (!liveActive) {
            let live = getModePnL('LIVE', p_input); 
            
            $('#live_sl_pts').val(live.slPts);
            $('#live_p_sl').val(live.slPrice.toFixed(2));
            $('#live_pnl_sl').text(`₹ ${live.pnl_sl.toFixed(0)}`);
            
            $('#live_trail_sl').val(live.trailing);
            $('#live_sl_to_entry').val(live.slEntry);
            $('#live_exit_mult').val(live.exitMult);
            
            $('#live_qty').val(qty); 
            
            $('#live_p_t1').val(live.t1.toFixed(2));
            $('#live_p_t2').val(live.t2.toFixed(2));
            $('#live_p_t3').val(live.t3.toFixed(2));
            
            $('#live_pnl_t1').text(`₹ ${live.pnl_t1.toFixed(0)}`);
            $('#live_pnl_t2').text(`₹ ${live.pnl_t2.toFixed(0)}`);
            $('#live_pnl_t3').text(`₹ ${live.pnl_t3.toFixed(0)}`);
            
            $('#live_r_t1').text(live.ratios[0].toFixed(1));
            $('#live_r_t2').text(live.ratios[1].toFixed(1));
            $('#live_r_t3').text(live.ratios[2].toFixed(1));
            
            ['t1', 't2', 't3'].forEach((k, i) => {
                let conf = live.targets[i] || {};
                $(`#live_${k}_active`).prop('checked', conf.active !== false);
                $(`#live_${k}_full`).prop('checked', conf.full === true);
                $(`#live_${k}_cost`).prop('checked', conf.trail_to_entry === true);
                
                if (conf.full) {
                    $(`#live_${k}_lots`).val(1000).prop('readonly', true);
                } else {
                    $(`#live_${k}_lots`).val(conf.lots || 0).prop('readonly', false);
                }
            });
        }
    }

    // Enforce Readonly logic for Main Card as well
    ['t1', 't2', 't3'].forEach(k => {
        if ($(`#${k}_full`).is(':checked')) {
            $(`#${k}_lots`).val(1000).prop('readonly', true);
        } else {
            $(`#${k}_lots`).prop('readonly', false);
        }
    });
}

function updateModeVisuals(mode) {
    let btn = $('#submit_btn');
    let mainCard = $('#main_pnl_card');
    let shadowCard = $('#shadow_live_card');
    let mainTitle = $('#main_pnl_title');
    
    // Select the button more robustly (the ID doesn't exist in HTML provided, but class and type work)
    let submitBtn = $('button[type="submit"][form="tradeForm"]');
    submitBtn.removeClass('btn-dark btn-danger btn-primary btn-warning btn-success');

    if (mode === "SHADOW") {
        shadowCard.slideDown();
        mainCard.css('border', '2px solid #007bff'); 
        mainTitle.text("📄 Projected P&L (PAPER / BROADCAST)");
        submitBtn.text("👻 Execute Shadow Trade");
        submitBtn.addClass('btn-dark');
    } else {
        shadowCard.hide();
        if (mode === "LIVE") {
            mainCard.css('border', '1px solid red');
            mainTitle.text("🛡️ Projected P&L (LIVE)");
            submitBtn.text("⚡ Execute LIVE Trade");
            submitBtn.addClass('btn-danger');
        } else {
            mainCard.css('border', '1px solid #007bff');
            mainTitle.text("🛡️ Projected P&L (PAPER)");
            submitBtn.text("🚀 Execute Paper Trade");
            submitBtn.addClass('btn-primary');
        }
    }
}

// --- NEW HELPER: Show Custom Floating Alert ---
function showFloatingAlert(message, status) {
    // Determine color based on status
    let alertClass = 'alert-info';
    if(status === 'success') alertClass = 'alert-success';
    if(status === 'error') alertClass = 'alert-danger';
    if(status === 'warning') alertClass = 'alert-warning';
    
    let html = `
        <div class="alert ${alertClass} alert-dismissible fade show floating-alert shadow" role="alert" style="position: fixed; top: 20px; right: 20px; z-index: 9999; min-width: 300px;">
            <strong>${status.toUpperCase()}!</strong> ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
    `;
    $('body').append(html);
    
    // Auto-remove after 4 seconds
    setTimeout(function() {
        $('.floating-alert').fadeOut('slow', function() {
            $(this).remove();
        });
    }, 4000);
}

$(function() {
    // --- NEW: AJAX Form Submission (No Reload) ---
    $('#tradeForm').on('submit', function(e) {
        e.preventDefault();
        
        let btn = $(this).find('button[type="submit"]');
        let originalText = btn.text();
        
        // Disable button & show spinner
        btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Processing...');

        let formData = new FormData(this);

        $.ajax({
            url: '/trade',
            type: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: function(response) {
                // Restore button state
                btn.prop('disabled', false).text(originalText);
                
                // Show notification
                showFloatingAlert(response.message, response.status);
                
                // If success, refresh the data table immediately
                if(response.status === 'success' || response.status === 'warning') {
                     if(typeof updateData === 'function') updateData();
                     
                     // Optional: Switch to Active Positions tab if not already there
                     // switchTab('pos'); 
                }
            },
            error: function(xhr) {
                btn.prop('disabled', false).text(originalText);
                let err = "Network Error";
                if(xhr.responseJSON && xhr.responseJSON.message) err = xhr.responseJSON.message;
                showFloatingAlert(err, 'error');
            }
        });
    });
    // ---------------------------------------------

    $('#ord').change(function() {
        if($(this).val() === 'LIMIT') {
            $('#lim_box').show();
            $('#lim_pr').prop('required', true);
        } else {
            $('#lim_box').hide();
            $('#lim_pr').prop('required', false);
        }
    });
    
    $('#mode_input').change(function() {
        let val = $(this).val();
        updateModeVisuals(val);
        if($('#sym').val()) {
            loadDetails('#sym', '#exp', 'input[name="type"]:checked', '#qty', '#sl_pts');
        }
    });

    $('#ord').trigger('change');
    updateModeVisuals($('#mode_input').val());
});
