import logging
import threading
from kiteconnect import KiteTicker

# Global Ticker Instance
ticker = None

class ZerodhaTicker:
    def __init__(self, api_key, access_token):
        self.kws = KiteTicker(api_key, access_token)
        # Cache: { instrument_token: last_price }
        self.ltp_cache = {}
        self.subscribed_tokens = set()
        self.lock = threading.Lock()
        
        # Bind Events
        self.kws.on_ticks = self.on_ticks
        self.kws.on_connect = self.on_connect
        self.kws.on_error = self.on_error
        self.kws.on_close = self.on_close

    def start_background(self):
        """Starts the WebSocket in a separate thread"""
        self.thread = threading.Thread(target=self.kws.connect, kwargs={'threaded': True})
        self.thread.daemon = True
        self.thread.start()

    def on_connect(self, ws, response):
        print("🟢 [TICKER] Connected to Zerodha WebSocket")
        # Re-subscribe to everything we know about on reconnection
        with self.lock:
            if self.subscribed_tokens:
                ws.subscribe(list(self.subscribed_tokens))
                ws.set_mode(ws.MODE_LTP, list(self.subscribed_tokens))

    def on_close(self, ws, code, reason):
        print(f"🔴 [TICKER] Closed: {code} - {reason}")

    def on_ticks(self, ws, ticks):
        """Updates the in-memory cache with new prices"""
        with self.lock:
            for tick in ticks:
                token = tick['instrument_token']
                price = tick['last_price']
                self.ltp_cache[token] = price

    def on_error(self, ws, code, reason):
        print(f"🔴 [TICKER] Error: {code} - {reason}")

    def subscribe(self, tokens):
        """
        Subscribes to a list of Instrument Tokens.
        FIX: Removed strict filtering. If Risk Engine requests it, we send it.
        This fixes the deadlock where data never starts if the first sub failed.
        """
        with self.lock:
            # Deduplicate input list
            tokens_to_send = list(set(tokens))
            
            if tokens_to_send:
                # Always send subscribe command to ensure connection
                self.kws.subscribe(tokens_to_send)
                self.kws.set_mode(self.kws.MODE_LTP, tokens_to_send)
                
                # Update our tracking set
                self.subscribed_tokens.update(tokens_to_send)

    def get_ltp(self, token):
        """Returns cached LTP or None if not available"""
        return self.ltp_cache.get(token)

def initialize_ticker(api_key, access_token):
    global ticker
    if ticker is None:
        ticker = ZerodhaTicker(api_key, access_token)
        ticker.start_background()
    return ticker
