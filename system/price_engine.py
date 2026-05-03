import websocket
import json
import threading
import time
from datetime import datetime, timezone

FINNHUB_KEY = 'd7kct31r01qiqbctt7r0d7kct31r01qiqbctt7rg'
SYMBOL = 'OANDA:XAU_USD'

class PriceEngine:
    def __init__(self):
        self.current_price = None
        self.prev_price = None
        self.price_history = []
        self.session_high = None
        self.session_low = None
        self.last_update = None
        self.running = False
        self.ws = None

    def get_session(self):
        hour = datetime.now(timezone.utc).hour
        if 0 <= hour < 7:
            return 'Tokyo'
        elif 7 <= hour < 13:
            return 'London'
        elif 13 <= hour < 22:
            return 'NewYork'
        return 'Sydney'

    def get_direction(self, lookback=5):
        if len(self.price_history) < lookback:
            return 'unknown'
        recent = self.price_history[-lookback:]
        if recent[-1] > recent[0] + 0.05:
            return 'up'
        elif recent[-1] < recent[0] - 0.05:
            return 'down'
        return 'ranging'

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get('type') == 'trade':
                for trade in data.get('data', []):
                    price = float(trade['p'])
                    self.prev_price = self.current_price
                    self.current_price = price
                    self.last_update = datetime.now(timezone.utc).isoformat()
                    self.price_history.append(price)
                    if len(self.price_history) > 60:
                        self.price_history.pop(0)
                    if self.session_high is None or price > self.session_high:
                        self.session_high = price
                    if self.session_low is None or price < self.session_low:
                        self.session_low = price
        except:
            pass

    def on_error(self, ws, error):
        pass

    def on_close(self, ws, *args):
        time.sleep(5)
        self.start()

    def on_open(self, ws):
        ws.send(json.dumps({'type': 'subscribe', 'symbol': SYMBOL}))
        self.running = True

    def start(self):
        self.ws = websocket.WebSocketApp(
            f'wss://ws.finnhub.io?token={FINNHUB_KEY}',
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open
        )
        t = threading.Thread(target=self.ws.run_forever)
        t.daemon = True
        t.start()
