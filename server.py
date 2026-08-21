#!/usr/bin/env python3
"""BetMaster AI local server. Keeps the football-data.org token on the server."""
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
API_BASE = "https://api.football-data.org/v4"
TOKEN = os.environ.get("FOOTBALL_DATA_API_KEY", "")

class AppHandler(SimpleHTTPRequestHandler):
    def _json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _football_data(self, endpoint):
        if not TOKEN:
            return 503, {"ok": False, "error": "FOOTBALL_DATA_API_KEY no está configurada en el servidor."}
        request = Request(API_BASE + endpoint, headers={"X-Auth-Token": TOKEN})
        try:
            with urlopen(request, timeout=15) as response:
                return 200, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            return error.code, {"ok": False, "error": "football-data.org respondió con un error.", "detail": detail}
        except URLError:
            return 502, {"ok": False, "error": "No fue posible conectar con football-data.org."}

    def do_GET(self):
        if self.path == "/api/status":
            self._json(200, {"ok": bool(TOKEN), "provider": "football-data.org", "message": "Conectado" if TOKEN else "Configura la clave en FOOTBALL_DATA_API_KEY."})
            return
        if self.path == "/api/competitions":
            status, data = self._football_data("/competitions")
            self._json(status, data)
            return
        if self.path.startswith("/api/teams/"):
            code = self.path.rsplit("/", 1)[-1].upper()
            if not code.replace("-", "").isalnum():
                self._json(400, {"ok": False, "error": "Código de competición inválido."})
                return
            status, data = self._football_data(f"/competitions/{code}/teams")
            self._json(status, data)
            return
        if self.path.startswith("/api/matches/"):
            code = self.path.rsplit("/", 1)[-1].upper()
            status, data = self._football_data(f"/competitions/{code}/matches?status=SCHEDULED")
            self._json(status, data)
            return
        super().do_GET()

if __name__ == "__main__":
    os.chdir(ROOT)
    port = int(os.environ.get("PORT", "8080"))
    print(f"BetMaster AI escuchando en http://0.0.0.0:{port}")
    print("Proveedor: football-data.org | Token configurado:", "sí" if TOKEN else "no")
    ThreadingHTTPServer(("0.0.0.0", port), AppHandler).serve_forever()
