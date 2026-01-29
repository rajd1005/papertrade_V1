# managers/truedata_manager.py
import os
import threading
import time
import logging
from truedata_ws.websocket.TD import TD
import config

# Configure logging
logger = logging.getLogger(__name__)

class TrueDataFeed:
    def __init__(self):
        self.td_app = None
        self.connected = False
        # Cache Structure: {'NSE:RELIANCE': 2450.05, 'NFO:NIFTY24JANFUT': 21500.0}
        self.price_cache = {} 
        self.subscribed_symbols = set()
        self.lock = threading.Lock()

    def connect(self):
        """Initializes the connection to TrueData WebSocket."""
        if not config.TRUEDATA_USER or not config.TRUEDATA_PASS:
            print("⚠️ TrueData: Credentials missing in Config.")
            return

        print(f"🔌 Connecting to TrueData ({config.TRUEDATA_USER})...")
        try:
            # Initialize TD Object (live_port=8084 for SSL)
            self.td_app = TD(config.TRUEDATA_USER, config.TRUEDATA_PASS, live_port=config.TRUEDATA_PORT)
            self.connected = True
            
            # Start the Data Reader Thread
            t = threading.Thread(target=self._data_loop, daemon=True)
            t.start()
            print("✅ TrueData Connected & Listening.")
            
        except Exception as e:
            print(f"❌ TrueData Connection Failed: {e}")
            self.connected = False

    def subscribe(self, kite_symbols):
        """
        Accepts a list of KITE format symbols (e.g., 'NSE:RELIANCE', 'NFO:NIFTY24JANFUT')
        Converts them to TrueData format and subscribes.
        """
        if not self.td_app or not self.connected:
            return

        symbols_to_send = []
        
        with self.lock:
            for s in kite_symbols:
                if s not in self.subscribed_symbols:
                    # Conversion Logic: "NSE:RELIANCE" -> "RELIANCE"
                    # TrueData usually just needs the symbol name without exchange for EQ/F&O
                    clean_symbol = s.split(':')[-1] 
                    
                    # Special Handling for Indices if needed
                    if clean_symbol == "NIFTY 50": clean_symbol = "NIFTY 50"
                    if clean_symbol == "NIFTY BANK": clean_symbol = "NIFTY BANK"
                    
                    symbols_to_send.append(clean_symbol)
                    self.subscribed_symbols.add(s) # Track full name to avoid re-subscribing

        if symbols_to_send:
            try:
                # TrueData handles duplicate subs gracefully
                self.td_app.start_live_data(symbols_to_send)
                print(f"📡 TrueData: Subscribed to {len(symbols_to_send)} new symbols")
            except Exception as e:
                print(f"❌ Subscription Error: {e}")

    def _data_loop(self):
        """Background loop to fetch ticks from buffer and update cache."""
        while self.connected:
            try:
                # Fetch all available ticks from the library buffer
                ticks = self.td_app.get_live_data()
                
                if ticks:
                    for tick in ticks:
                        # Tick structure: {'symbol': 'RELIANCE', 'ltp': 2450.0, ...}
                        symbol = tick.get('symbol')
                        ltp = tick.get('ltp')
                        
                        if symbol and ltp:
                            # We must Map TrueData symbol BACK to Kite format for your app
                            # Heuristic: We map to ALL possible exchanges to ensure hit
                            # (Since TrueData sends 'RELIANCE', we update 'NSE:RELIANCE' and 'BSE:RELIANCE')
                            
                            self.price_cache[f"NSE:{symbol}"] = ltp
                            self.price_cache[f"NFO:{symbol}"] = ltp
                            self.price_cache[f"MCX:{symbol}"] = ltp
                            self.price_cache[f"BSE:{symbol}"] = ltp
                            self.price_cache[f"CDS:{symbol}"] = ltp
                            
                            # Raw symbol backup
                            self.price_cache[symbol] = ltp

                time.sleep(0.001) # 1ms sleep to prevent CPU spike
            except Exception as e:
                print(f"⚠️ Data Loop Error: {e}")
                time.sleep(1)

    def get_ltp(self, symbol):
        """Returns LTP from Cache. Returns 0 if not found."""
        return self.price_cache.get(symbol, 0)

    def get_bulk_ltp(self, symbol_list):
        """
        Mimics `kite.quote(list)`. 
        Returns: {'NSE:RELIANCE': {'last_price': 2400}}
        Auto-subscribes if symbol is missing.
        """
        response = {}
        missing = []

        for sym in symbol_list:
            if sym in self.price_cache:
                response[sym] = {'last_price': self.price_cache[sym]}
            else:
                missing.append(sym)
                response[sym] = {'last_price': 0} # Return 0 initially
        
        # Lazy Subscription: If we asked for it but don't have it, subscribe now.
        if missing:
            self.subscribe(missing)
            
        return response

# Global Singleton
feed = TrueDataFeed()
