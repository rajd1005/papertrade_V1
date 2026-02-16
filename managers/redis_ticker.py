import redis
import json
import threading
import os
import logging

class RedisTicker:
    """
    Connects to the Railway Gateway (Redis) to get market data.
    """
    def __init__(self, api_key=None, access_token=None, debug=False, root=None):
        # We read the REDIS_URL from Railway variables
        self.redis_url = os.getenv("REDIS_URL")
        if not self.redis_url:
             # Fallback for local testing
            self.redis_url = "redis://localhost:6379/0"
            
        self.r = redis.from_url(self.redis_url, decode_responses=True)
        self.pubsub = self.r.pubsub()
        
        # Standard KiteTicker callbacks
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self.on_error = None
        
        self._stop_event = threading.Event()
        self.is_connected_flag = False

    def connect(self, threaded=True):
        self.is_connected_flag = True
        # Simulate immediate connection
        if self.on_connect:
            self.on_connect(self, {"status": "Connected via Gateway"})
            
        if threaded:
            t = threading.Thread(target=self._loop, daemon=True)
            t.start()
        else:
            self._loop()

    def _loop(self):
        """
        Listens to the 'market_ticks' stream from the Gateway.
        """
        try:
            self.pubsub.subscribe('market_ticks')
            print("🔌 Connected to Market Data Pool")
            
            for message in self.pubsub.listen():
                if self._stop_event.is_set(): break
                
                if message['type'] == 'message':
                    try:
                        # Gateway sends single tick or list
                        data = json.loads(message['data'])
                        # Ensure it's a list (Standard Kite format)
                        ticks = [data] if isinstance(data, dict) else data
                        
                        if self.on_ticks:
                            self.on_ticks(self, ticks)
                    except Exception:
                        pass
        except Exception as e:
            if self.on_error:
                self.on_error(self, code=500, reason=str(e))
            self.is_connected_flag = False

    def subscribe(self, instrument_tokens):
        """
        Tells the Gateway to start watching these tokens.
        """
        if not instrument_tokens: return
        
        payload = json.dumps({
            "action": "SUBSCRIBE", 
            "tokens": list(instrument_tokens)
        })
        self.r.publish('gateway_commands', payload)
        print(f"📤 Sent Subscribe Request: {len(instrument_tokens)} tokens")

    def set_mode(self, mode, instrument_tokens):
        # Gateway handles mode automatically
        pass

    def is_connected(self):
        return self.is_connected_flag
