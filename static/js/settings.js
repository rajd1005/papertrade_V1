function loadSettings() {
    $.get('/api/settings/load', function(data) {
        if(data) {
            settings = data;
            
            // --- Existing Exchanges Logic ---
            if(settings.exchanges) {
                $('input[name="exch_select"]').prop('checked', false);
                settings.exchanges.forEach(e => $(`#exch_${e}`).prop('checked', true));
            }

            $('#first_trade_toggle').prop('checked', settings.first_trade_logic || false);
            
            let storedMode = settings.default_trade_mode || 'PAPER';
            let storedChannel = settings.default_broadcast_channel || 'vip'; 

            let applyMode = storedMode;
            let applyChannel = storedChannel;

            if (settings.first_trade_logic && settings.is_first_trade) {
                applyMode = 'SHADOW';
                applyChannel = 'free'; 
            }

            $('#def_trade_mode').val(storedMode);
            $('#def_vip').prop('checked', storedChannel === 'vip');
            $('#def_free').prop('checked', storedChannel === 'free');
            $('#def_z2h').prop('checked', storedChannel === 'z2h');

            setTimeout(() => {
                let btn = $(`.btn[onclick*="setMode"][onclick*="'${applyMode}'"]`);
                if(btn.length && !btn.hasClass('active')) {
                    btn.click();
                }
            }, 200); 

            $('input[name="target_channel"]').prop('checked', false);
            if(applyChannel === 'vip') $('#rad_vip').prop('checked', true);
            else if(applyChannel === 'free') $('#rad_free').prop('checked', true);
            else if(applyChannel === 'z2h') $('#rad_z2h').prop('checked', true);

            renderWatchlist();

            ['PAPER', 'LIVE'].forEach(m => {
                let k = m.toLowerCase();
                let s = settings.modes[m];
                if(s) {
                    $(`#${k}_qty_mult`).val(s.qty_mult);
                    $(`#${k}_r1`).val(s.ratios[0]);
                    $(`#${k}_r2`).val(s.ratios[1]);
                    $(`#${k}_r3`).val(s.ratios[2]);
                    $(`#${k}_def_trail`).val(s.trailing_sl || 0);
                    $(`#${k}_order_type`).val(s.order_type || 'MARKET');
                    $(`#${k}_trail_limit`).val(s.sl_to_entry || 0);
                    $(`#${k}_exit_mult`).val(s.exit_multiplier || 1);
                    $(`#${k}_time`).val(s.universal_exit_time || "15:25");
                    $(`#${k}_max_loss`).val(s.max_loss || 0);
                    $(`#${k}_pl_start`).val(s.profit_lock || 0);
                    $(`#${k}_pl_min`).val(s.profit_min || 0);
                    $(`#${k}_pl_trail`).val(s.profit_trail || 0);

                    let tgts = s.targets || [
                        {active: true, lots: 0, full: false, trail_to_entry: false},
                        {active: true, lots: 0, full: false, trail_to_entry: false},
                        {active: true, lots: 1000, full: true, trail_to_entry: false}
                    ];
                    ['1', '2', '3'].forEach((i, idx) => {
                         let t = tgts[idx];
                         $(`#${k}_a${i}`).prop('checked', t.active);
                         $(`#${k}_l${i}`).val(t.lots > 0 && !t.full ? t.lots : '');
                         $(`#${k}_f${i}`).prop('checked', t.full);
                         $(`#${k}_c${i}`).prop('checked', t.trail_to_entry || false);
                    });
                    renderSLTable(m);
                }
            });
            
            if(settings.modes.LIVE) { settings.modes.SHADOW = settings.modes.LIVE; }

            if(settings.telegram) {
                $('#tg_bot_token').val(settings.telegram.bot_token || '');
                $('#tg_enable').prop('checked', settings.telegram.enable_notifications || false);
                $('#tg_channel_id').val(settings.telegram.channel_id || '');
                $('#tg_system_channel_id').val(settings.telegram.system_channel_id || ''); 
                $('#tg_vip_channel_id').val(settings.telegram.vip_channel_id || '');
                $('#tg_free_channel_id').val(settings.telegram.free_channel_id || '');
                $('#tg_z2h_channel_id').val(settings.telegram.z2h_channel_id || '');
                $('#tg_z2h_channel_name').val(settings.telegram.z2h_channel_name || 'Zero To Hero');
                let toggles = settings.telegram.event_toggles || {};
                $('#tg_evt_new').prop('checked', toggles.NEW_TRADE !== false);
                $('#tg_evt_active').prop('checked', toggles.ACTIVE !== false);
                $('#tg_evt_update').prop('checked', toggles.UPDATE !== false);
                $('#tg_evt_sl').prop('checked', toggles.SL_HIT !== false);
                $('#tg_evt_tgt').prop('checked', toggles.TARGET_HIT !== false);
                $('#tg_evt_high').prop('checked', toggles.HIGH_MADE !== false);
                let tpls = settings.telegram.templates || {};
                $('#tpl_new').val(tpls.NEW_TRADE || "");
                $('#tpl_active').val(tpls.ACTIVE || "");
                $('#tpl_update').val(tpls.UPDATE || "");
                $('#tpl_sl').val(tpls.SL_HIT || "");
                $('#tpl_tgt').val(tpls.TARGET_HIT || "");
                $('#tpl_high').val(tpls.HIGH_MADE || "");
                $('#tpl_free_header').val(tpls.FREE_HEADER || ""); 
            }

            // --- NEW: Dynamic Auth Config Loader ---
            $.get('/api/config/auth', function(res) {
                if(res && res.auth) {
                    $('#cfg_api_key').val(res.auth.API_KEY || '');
                    $('#cfg_api_secret').val(res.auth.API_SECRET || '');
                    $('#cfg_user_id').val(res.auth.ZERODHA_USER_ID || '');
                    $('#cfg_password').val(res.auth.ZERODHA_PASSWORD || '');
                    $('#cfg_totp_secret').val(res.auth.TOTP_SECRET || '');
                    $('#display_callback_url').text(res.callback_url || 'N/A');
                }
            });

            if (typeof updateDisplayValues === "function") updateDisplayValues(); 
        }
    });
}

function saveSettings() {
    // --- NEW: Save Auth Credentials First ---
    let authData = {
        API_KEY: $('#cfg_api_key').val().trim(),
        API_SECRET: $('#cfg_api_secret').val().trim(),
        ZERODHA_USER_ID: $('#cfg_user_id').val().trim(),
        ZERODHA_PASSWORD: $('#cfg_password').val().trim(),
        TOTP_SECRET: $('#cfg_totp_secret').val().trim()
    };

    $.ajax({
        type: "POST",
        url: '/api/config/auth',
        data: JSON.stringify(authData),
        contentType: "application/json",
        success: function(response) {
            console.log("Auth config updated successfully");
        }
    });

    // --- Standard Settings Save Logic ---
    let selectedExchanges = [];
    $('input[name="exch_select"]:checked').each(function() { selectedExchanges.push($(this).val()); });
    settings.exchanges = selectedExchanges;

    settings.first_trade_logic = $('#first_trade_toggle').is(':checked'); 
    settings.default_trade_mode = $('#def_trade_mode').val(); 

    let def_channel = 'vip'; 
    if($('#def_vip').is(':checked')) def_channel = 'vip';
    else if($('#def_free').is(':checked')) def_channel = 'free';
    else if($('#def_z2h').is(':checked')) def_channel = 'z2h';
    settings.default_broadcast_channel = def_channel;

    ['PAPER', 'LIVE'].forEach(m => {
        let k = m.toLowerCase();
        let s = settings.modes[m];
        s.qty_mult = parseInt($(`#${k}_qty_mult`).val()) || 1;
        s.ratios = [parseFloat($(`#${k}_r1`).val()), parseFloat($(`#${k}_r2`).val()), parseFloat($(`#${k}_r3`).val())];
        s.trailing_sl = float($(`#${k}_def_trail`).val()) || 0;
        s.order_type = $(`#${k}_order_type`).val();
        s.sl_to_entry = parseInt($(`#${k}_trail_limit`).val()) || 0;
        s.exit_multiplier = parseInt($(`#${k}_exit_mult`).val()) || 1;
        s.universal_exit_time = $(`#${k}_time`).val();
        s.max_loss = parseFloat($(`#${k}_max_loss`).val()) || 0;
        s.profit_lock = parseFloat($(`#${k}_pl_start`).val()) || 0;
        s.profit_min = parseFloat($(`#${k}_pl_min`).val()) || 0;
        s.profit_trail = parseFloat($(`#${k}_pl_trail`).val()) || 0;
        s.targets = [];
        ['1', '2', '3'].forEach(i => {
            s.targets.push({
                active: $(`#${k}_a${i}`).is(':checked'),
                full: $(`#${k}_f${i}`).is(':checked'),
                lots: $(`#${k}_f${i}`).is(':checked') ? 1000 : (parseInt($(`#${k}_l${i}`).val()) || 0),
                trail_to_entry: $(`#${k}_c${i}`).is(':checked')
            });
        });
    });

    settings.telegram = {
        bot_token: $('#tg_bot_token').val().trim(),
        enable_notifications: $('#tg_enable').is(':checked'),
        channel_id: $('#tg_channel_id').val().trim(),
        system_channel_id: $('#tg_system_channel_id').val().trim(),
        vip_channel_id: $('#tg_vip_channel_id').val().trim(),
        free_channel_id: $('#tg_free_channel_id').val().trim(),
        z2h_channel_id: $('#tg_z2h_channel_id').val().trim(),
        z2h_channel_name: $('#tg_z2h_channel_name').val().trim() || 'Zero To Hero',
        event_toggles: {
            NEW_TRADE: $('#tg_evt_new').is(':checked'),
            ACTIVE: $('#tg_evt_active').is(':checked'),
            UPDATE: $('#tg_evt_update').is(':checked'),
            SL_HIT: $('#tg_evt_sl').is(':checked'),
            TARGET_HIT: $('#tg_evt_tgt').is(':checked'),
            HIGH_MADE: $('#tg_evt_high').is(':checked')
        },
        templates: {
            NEW_TRADE: $('#tpl_new').val(),
            ACTIVE: $('#tpl_active').val(),
            UPDATE: $('#tpl_update').val(),
            SL_HIT: $('#tpl_sl').val(),
            TARGET_HIT: $('#tpl_tgt').val(),
            HIGH_MADE: $('#tpl_high').val(),
            FREE_HEADER: $('#tpl_free_header').val()
        }
    };

    $.ajax({ 
        type: "POST", 
        url: '/api/settings/save', 
        data: JSON.stringify(settings), 
        contentType: "application/json", 
        success: () => { 
            $('#settingsModal').modal('hide'); 
            loadSettings(); 
        } 
    });
}

    settings.telegram = {
        bot_token: $('#tg_bot_token').val().trim(),
        enable_notifications: $('#tg_enable').is(':checked'),
        channel_id: $('#tg_channel_id').val().trim(),
        system_channel_id: $('#tg_system_channel_id').val().trim(),
        vip_channel_id: $('#tg_vip_channel_id').val().trim(),
        free_channel_id: $('#tg_free_channel_id').val().trim(),
        z2h_channel_id: $('#tg_z2h_channel_id').val().trim(),
        z2h_channel_name: $('#tg_z2h_channel_name').val().trim() || 'Zero To Hero',
        event_toggles: {
            NEW_TRADE: $('#tg_evt_new').is(':checked'),
            ACTIVE: $('#tg_evt_active').is(':checked'),
            UPDATE: $('#tg_evt_update').is(':checked'),
            SL_HIT: $('#tg_evt_sl').is(':checked'),
            TARGET_HIT: $('#tg_evt_tgt').is(':checked'),
            HIGH_MADE: $('#tg_evt_high').is(':checked')
        },
        templates: {
            NEW_TRADE: $('#tpl_new').val(),
            ACTIVE: $('#tpl_active').val(),
            UPDATE: $('#tpl_update').val(),
            SL_HIT: $('#tpl_sl').val(),
            TARGET_HIT: $('#tpl_tgt').val(),
            HIGH_MADE: $('#tpl_high').val(),
            FREE_HEADER: $('#tpl_free_header').val()
        }
    };

    $.ajax({ 
        type: "POST", 
        url: '/api/settings/save', 
        data: JSON.stringify(settings), 
        contentType: "application/json", 
        success: () => { 
            $('#settingsModal').modal('hide'); 
            loadSettings(); 
        } 
    });
}

function testTelegram() {
    let token = $('#tg_bot_token').val().trim();
    let chat = $('#tg_channel_id').val().trim();
    if(!token || !chat) { alert("Enter Token & Main Channel ID first"); return; }
    $.post('/api/test_telegram', { token: token, chat_id: chat }, function(res) {
        if(res.status === 'success') alert("✅ Message Sent Successfully!");
        else alert("❌ Error: " + res.message);
    });
}

function renderWatchlist() {
    if (typeof settings === 'undefined' || !settings.watchlist) return;
    let wl = settings.watchlist || [];
    
    // Dashboard & Import Selects
    let mainOpts = '<option value="">📺 Select</option>';
    wl.forEach(w => { mainOpts += `<option value="${w}">${w}</option>`; });
    $('#trade_watch').html(mainOpts);
    if($('#imp_watch').length) $('#imp_watch').html(mainOpts);

    // Settings Remove Select (New Sync Logic)
    let remOpts = '<option value="">Select to Remove...</option>';
    wl.forEach(w => { remOpts += `<option value="${w}">${w}</option>`; });
    if($('#remove_watch_sym').length) $('#remove_watch_sym').html(remOpts);
}

function addToWatchlist(inputId) {
    let val = $(inputId).val();
    if(val && val.length > 2) {
        if(val.includes('(')) val = val.split('(')[0].trim();
        else if(val.includes(':')) val = val.split(':')[0].trim();
        
        if(!settings.watchlist) settings.watchlist = [];
        if(!settings.watchlist.includes(val)) {
            settings.watchlist.push(val);
            $.ajax({ 
                type: "POST", 
                url: '/api/settings/save', 
                data: JSON.stringify(settings), 
                contentType: "application/json", 
                success: () => { 
                    renderWatchlist();
                    let btn = $(inputId).next(); let originalText = btn.text(); btn.text("✅"); setTimeout(() => btn.text(originalText), 1000);
                }
            });
        } else { alert("Symbol already in Watchlist"); }
    }
}

function removeFromWatchlist(selectId) {
    let val = $('#' + selectId).val();
    if(val) {
        if(confirm("Remove " + val + " from watchlist?")) {
            settings.watchlist = settings.watchlist.filter(item => item !== val);
            $.ajax({ type: "POST", url: '/api/settings/save', data: JSON.stringify(settings), contentType: "application/json", success: () => { renderWatchlist(); }});
        }
    }
}

function loadWatchlist(selectId, inputId) {
    let val = $('#' + selectId).val();
    if(val) {
        $(inputId).val(val).trigger('change');
        $(inputId).trigger('input'); 
    }
}

function applyBulkSL(mode) {
    let k = mode.toLowerCase();
    let text = $(`#${k}_bulk_sl`).val();
    if(!text) { alert("Please enter SYMBOL|SL or SYMBOL|SL|T1|T2|T3"); return; }
    let lines = text.split('\n'); let count = 0;
    
    if(!settings.modes[mode].symbol_sl) settings.modes[mode].symbol_sl = {};
    
    lines.forEach(l => {
        let parts = l.split('|');
        if(parts.length >= 2) {
            let s = normalizeSymbol(parts[0]); 
            let sl = parseFloat(parts[1]);
            
            if(s && sl > 0) { 
                let entry = { sl: sl, targets: [] };
                
                // Parse optional targets (T1, T2, T3)
                if(parts.length >= 5) {
                    entry.targets = [
                        parseFloat(parts[2]) || 0,
                        parseFloat(parts[3]) || 0,
                        parseFloat(parts[4]) || 0
                    ];
                }
                
                settings.modes[mode].symbol_sl[s] = entry; 
                count++; 
            }
        }
    });
    renderSLTable(mode); $(`#${k}_bulk_sl`).val('');
    alert(`Successfully updated ${count} symbols for ${mode}. Click Save Changes.`);
}

function renderSLTable(mode) {
    let k = mode.toLowerCase();
    let tbody = $(`#${k}_sl_table_body`).empty();
    let slMap = settings.modes[mode].symbol_sl || {};
    
    Object.keys(slMap).forEach(sym => {
        let val = slMap[sym];
        let displaySL = 0;
        let t = ['-', '-', '-'];
        
        // Handle New Object Format vs Legacy Int Format
        if (typeof val === 'object') {
            displaySL = val.sl;
            if(val.targets && val.targets.length === 3) {
                t = val.targets;
            }
        } else {
            displaySL = val;
        }

        tbody.append(`<tr>
            <td class="fw-bold">${sym}</td>
            <td>${displaySL}</td>
            <td>${t[0]}</td>
            <td>${t[1]}</td>
            <td>${t[2]}</td>
            <td>
                <button class="btn btn-sm btn-outline-secondary py-0" onclick="editSymSL('${mode}', '${sym}')">✏️</button> 
                <button class="btn btn-sm btn-outline-danger py-0" onclick="deleteSymSL('${mode}', '${sym}')">🗑️</button>
            </td>
        </tr>`);
    });
}

function saveSymSL(mode) {
    let k = mode.toLowerCase();
    let s = normalizeSymbol($(`#${k}_set_sym`).val());
    let sl = parseFloat($(`#${k}_set_sl`).val());
    let t1 = parseFloat($(`#${k}_set_t1`).val()) || 0;
    let t2 = parseFloat($(`#${k}_set_t2`).val()) || 0;
    let t3 = parseFloat($(`#${k}_set_t3`).val()) || 0;

    if(s && sl) {
        if(!settings.modes[mode].symbol_sl) settings.modes[mode].symbol_sl = {};
        
        let entry = { sl: sl, targets: [] };
        // Only add targets if at least one is specified
        if(t1 > 0 || t2 > 0 || t3 > 0) {
            entry.targets = [t1, t2, t3];
        }
        
        settings.modes[mode].symbol_sl[s] = entry;
        renderSLTable(mode);
        
        // Clear inputs
        $(`#${k}_set_sym`).val(''); $(`#${k}_set_sl`).val('');
        $(`#${k}_set_t1`).val(''); $(`#${k}_set_t2`).val(''); $(`#${k}_set_t3`).val('');
    }
}

function editSymSL(mode, sym) { 
    let k = mode.toLowerCase(); 
    let data = settings.modes[mode].symbol_sl[sym];
    $(`#${k}_set_sym`).val(sym); 
    
    if (typeof data === 'object') {
        $(`#${k}_set_sl`).val(data.sl);
        if(data.targets && data.targets.length === 3) {
            $(`#${k}_set_t1`).val(data.targets[0]);
            $(`#${k}_set_t2`).val(data.targets[1]);
            $(`#${k}_set_t3`).val(data.targets[2]);
        } else {
            $(`#${k}_set_t1`).val(''); $(`#${k}_set_t2`).val(''); $(`#${k}_set_t3`).val('');
        }
    } else {
        // Legacy Support
        $(`#${k}_set_sl`).val(data); 
        $(`#${k}_set_t1`).val(''); $(`#${k}_set_t2`).val(''); $(`#${k}_set_t3`).val('');
    }
}

function deleteSymSL(mode, sym) { 
    delete settings.modes[mode].symbol_sl[sym]; 
    renderSLTable(mode); 
}
