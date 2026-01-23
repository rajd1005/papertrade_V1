// --- GLOBAL VARIABLES (Shared across all files) ---
var settings = { 
    exchanges: ['NSE', 'NFO', 'MCX', 'CDS', 'BSE', 'BFO'],
    watchlist: [],
    modes: {
        LIVE: {qty_mult: 1, ratios: [0.5, 1.0, 1.5], symbol_sl: {}},
        PAPER: {qty_mult: 1, ratios: [0.5, 1.0, 1.5], symbol_sl: {}}
    }
};

var curLotSize = 1;
var symLTP = {}; 
var activeTradesList = []; 
var allClosedTrades = [];
var curLTP = 0;

// --- HELPER FUNCTIONS ---

// Debounce Function (Prevents rapid-fire API calls)
function debounce(func, wait) {
    let timeout;
    return function(...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), wait);
    };
}

function normalizeSymbol(s) {
    if(!s) return "";
    s = s.toUpperCase().trim();
    if(s.includes('(')) s = s.split('(')[0].trim();
    if(s.includes(':')) s = s.split(':')[0].trim();
    
    if(['NIFTY', 'NIFTY 50', 'NIFTY50'].includes(s)) return 'NIFTY';
    if(['BANKNIFTY', 'NIFTY BANK', 'BANK NIFTY'].includes(s)) return 'BANKNIFTY';
    if(['FINNIFTY', 'NIFTY FIN SERVICE'].includes(s)) return 'FINNIFTY';
    if(['SENSEX', 'BSE SENSEX'].includes(s)) return 'SENSEX';
    return s;
}

function getTradeCategory(t) { 
    if (t.mode === 'LIVE') return 'LIVE'; 
    return 'PAPER'; 
}

function getMarkBadge(category) { 
    if (category === 'LIVE') return '<span class="badge bg-danger" style="font-size:0.7rem;">LIVE</span>'; 
    return '<span class="badge bg-warning text-dark" style="font-size:0.7rem;">PAPER</span>'; 
}

function updateClock() { 
    $('#live_clock').text(new Date().toLocaleTimeString('en-US', { hour12: false })); 
}

function showLogs(tradeId, type) {
    let trade = (type === 'active') ? activeTradesList.find(x => x.id == tradeId) : allClosedTrades.find(x => x.id == tradeId);
    if (trade && trade.logs) { 
        $('#logModalBody').html(trade.logs.map(l => `<div class="log-entry border-bottom py-1">${l}</div>`).join('')); 
        new bootstrap.Modal(document.getElementById('logModal')).show(); 
    } else {
        alert("No logs available.");
    }
}

function bindSearch(id, listId) { 
    // Apply Debounce (Wait 300ms after last keystroke)
    $(id).on('input', debounce(function() { 
        let val = this.value;
        if(val.length > 2) {
            $.get('/api/search', {q: val}, function(d) { 
                $(listId).empty(); 
                d.forEach(s => $(listId).append(`<option value="${s}">`)); 
            }); 
        }
    }, 300)); 
}

function showToast(msg, type='info') {
    let color = type === 'error' ? 'bg-danger' : (type === 'success' ? 'bg-success' : 'bg-info');
    let html = `
    <div class="toast align-items-center text-white ${color} border-0 show floating-alert" role="alert" aria-live="assertive" aria-atomic="true">
        <div class="d-flex">
            <div class="toast-body">${msg}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    </div>`;
    
    let container = $('#toast-container');
    if(container.length === 0) {
        $('body').append('<div id="toast-container" class="toast-container position-fixed bottom-0 end-0 p-3" style="z-index: 1100"></div>');
        container = $('#toast-container');
    }
    container.append(html);
    setTimeout(() => { container.find('.toast').first().fadeOut(function() { $(this).remove(); }); }, 4000);
}
