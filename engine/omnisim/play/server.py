"""Play server: one living game on localhost, many turns.

Run:  PYTHONPATH=src python -m omnisim.play.server  ->  http://127.0.0.1:8472
  GET  /view       player-view JSON (map, sidebar, menu, log, nearby)
  POST /do         {"cmd": "go market"} -> {"msg": ..., plus full view}
  POST /reset      new game (a fresh morning)
Text clients welcome: every response is plain JSON with plain strings.
"""
from __future__ import annotations
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from omnisim.play.session import PlaySession

PORT = 8472


class Handler(BaseHTTPRequestHandler):
    session = PlaySession()

    def _send(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/view":
            self._send(self.session.view())
        else:
            self._send({"help": "GET /view | POST /do {cmd} | POST /reset",
                        "try": ["look", "board", "go market", "work 3", "day"]})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except Exception:
            body = {}
        if self.path == "/reset":
            Handler.session = PlaySession()
            self._send({"msg": "A fresh morning.", **self.session.view()})
        elif self.path == "/do":
            self._send(self.session.do(str(body.get("cmd", "look"))))
        else:
            self._send({"error": "nope"}, 404)

    def log_message(self, *a):
        pass


def main(port=PORT):
    print(f"Play API on http://127.0.0.1:{port}/view  (POST /do {{\"cmd\": ...}})")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
