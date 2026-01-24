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

    def start_background(self):
        """Starts the WebSocket in a separate thread"""
        self.thread = threading.Thread(target=self.kws.connect, kwargs={'threaded': True})
        self.thread.daemon = True
        self.thread.start()

    def on_connect(self, ws, response):
        print("🟢 [TICKER] Connected to Zerodha WebSocket")
        # Re-subscribe if connection was lost
        with self.lock:
            if self.subscribed_tokens:
                ws.subscribe(list(self.subscribed_tokens))
                ws.set_mode(ws.MODE_LTP, list(self.subscribed_tokens))

    def on_ticks(self, ws, ticks):
        """Updates the in-memory cache with new prices"""
        with self.lock:
            for tick in ticks:
                token = tick['instrument_token']
                price = tick['last_price']
                self.ltp_cache[token] = price
        # Note: You can trigger Risk Engine here directly for ultra-low latency

    def on_error(self, ws, code, reason):
        print(f"🔴 [TICKER] Error: {code} - {reason}")

    def subscribe(self, tokens):
        """Subscribes to a list of Instrument Tokens"""
        with self.lock:
            # Filter only new tokens to avoid redundant calls
            new_tokens = [t for t in tokens if t not in self.subscribed_tokens]
            if new_tokens:
                self.kws.subscribe(new_tokens)
                self.kws.set_mode(self.kws.MODE_LTP, new_tokens)
                self.subscribed_tokens.update(new_tokens)
                print(f"📡 [TICKER] Subscribed to {len(new_tokens)} new tokens")

    def get_ltp(self, token):
        """Returns cached LTP or None if not available"""
        return self.ltp_cache.get(token)

def initialize_ticker(api_key, access_token):
    global ticker
    if ticker is None:
        ticker = ZerodhaTicker(api_key, access_token)
        ticker.start_background()
    return ticker
