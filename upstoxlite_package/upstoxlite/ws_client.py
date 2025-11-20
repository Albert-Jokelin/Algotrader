import threading
import json
from websocket import WebSocketApp

class UpstoxWebsocketClient:
    def __init__(self, ws_url: str, token: str, on_message=None):
        self.ws_url = ws_url
        self.token = token
        self.on_message = on_message or (lambda m: print("msg", m))
        self._ws = None
        self._thread = None

    def _on_open(self, ws):
        print("Websocket opened")

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except:
            data = message
        self.on_message(data)

    def _on_error(self, ws, err):
        print("WS error", err)

    def _on_close(self, ws, code, reason):
        print("WS closed", code, reason)

    def run_forever(self):
        headers = [f"Authorization: Bearer {self.token}"]
        self._ws = WebSocketApp(self.ws_url,
                                on_open=self._on_open,
                                on_message=self._on_message,
                                on_error=self._on_error,
                                on_close=self._on_close,
                                header=headers)
        self._thread = threading.Thread(target=self._ws.run_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._ws:
            self._ws.close()
        if self._thread:
            self._thread.join(timeout=2)
