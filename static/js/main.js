// Global State for ORB
let orbLotSize = 50; 
let orbCheckInterval = null;

$(document).ready(function() {
    console.log("🚀 RD Algo Terminal Loaded");
    
    // Initialize Bootstrap Tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'))
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
      return new bootstrap.Tooltip(tooltipTriggerEl)
    })

    // --- ORB STRATEGY INIT ---
    // Only run if the ORB panel exists on the page
    if ($('#orb_status_badge').length) {
        loadOrbStatus();
        // Poll status every 3 seconds to keep UI in sync
        orbCheckInterval = setInterval(loadOrbStatus, 3000);
    }
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
            if(window.showFloatingAlert) {
                showFloatingAlert("Request Failed: " + err.statusText, 'danger');
            } else {
                alert("Request Failed");
            }
        },
        complete: function() {
            // Re-enable buttons
            $('#btn_orb_start, #btn_orb_stop').prop('disabled', false);
        }
    });
}

// --- DASHBOARD NAVIGATION FUNCTIONS ---

function switchTab(tabName) {
    // Hide all contents
    $('.tab-content').hide();
    $('.nav-btn').removeClass('active');
    
    // Show selected
    $('#tab-' + tabName).fadeIn();
    
    // Update button state
    // We iterate through buttons to find the one matching the click
    $('.nav-btn').each(function() {
        // Simple check to match the onclick attribute
        if ($(this).attr('onclick').includes(tabName)) {
            $(this).addClass('active');
        }
    });
}

// --- GLOBAL PANIC EXIT ---
function panicExit() {
    if(!confirm("⚠️ ARE YOU SURE?\n\nThis will immediately CLOSE ALL open positions and cancel all pending orders.\n\nProceed?")) return;
    
    $.post('/api/panic_exit', {}, function(res) {
        if(res.status === 'success') {
            alert(res.message);
            location.reload();
        } else {
            alert("Error: " + res.message);
        }
    });
}

// --- ALERT HELPER (Fallback if utils.js is missing) ---
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
