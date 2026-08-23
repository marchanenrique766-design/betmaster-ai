#!/usr/bin/env python3
"""BetMaster AI local server. Keeps the football-data.org token on the server."""
import json
import os
from datetime import date, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
import urllib.request

# ---------- IA (LLM) multi-proveedor: sk-or- / gsk_ / AIza ----------
RAW_AI_KEY = (os.environ.get("MATE_AI_KEY") or os.environ.get("OPENROUTER_API_KEY")
              or os.environ.get("GROQ_API_KEY") or os.environ.get("GEMINI_API_KEY") or "")
_MODELS_CACHE = {}

def ai_prov():
    if not RAW_AI_KEY:
        return None
    if RAW_AI_KEY.startswith("sk-or-"):
        return "openrouter"
    if RAW_AI_KEY.startswith("gsk_"):
        return "groq"
    if RAW_AI_KEY.startswith("AIza"):
        return "gemini"
    return "gemini"

def _get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def _post_json(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

def _cands(p):
    key = "cand_" + p
    if key in _MODELS_CACHE:
        return _MODELS_CACHE[key]
    ids = []
    try:
        if p == "groq":
            data = _get_json("https://api.groq.com/openai/v1/models", {"Authorization": "Bearer " + RAW_AI_KEY})
            ids = [m.get("id", "") for m in data.get("data", [])]
            vis = [i for i in ids if any(k in i for k in ("scout", "llama-4", "vision", "vl"))]
            txt = [i for i in ids if any(k in i for k in ("llama-3.3", "llama-3.1", "qwen", "gpt-oss"))]
            c = (vis + txt + ids)[:5] or ["meta-llama/llama-4-scout-17b-16e-instruct"]
        else:
            data = _get_json("https://openrouter.ai/api/v1/models", {"Authorization": "Bearer " + RAW_AI_KEY})
            ids = [m.get("id", "") for m in data.get("data", []) if str(m.get("id", "")).endswith(":free")]
            vis = [i for i in ids if any(k in i for k in ("llama-4", "vl", "vision", "gemma", "gemini", "qwen"))]
            c = (vis + ids)[:5] or ["meta-llama/llama-4-scout:free"]
    except Exception:
        c = ["meta-llama/llama-4-scout:free"] if p == "openrouter" else ["meta-llama/llama-4-scout-17b-16e-instruct"]
    _MODELS_CACHE[key] = c
    return c

def ask_llm(prompt):
    p = ai_prov()
    if p == "gemini":
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               "gemini-2.0-flash:generateContent?key=" + RAW_AI_KEY)
        data = _post_json(url, {"contents": [{"parts": [{"text": prompt}]}]})
        return data["candidates"][0]["content"]["parts"][0]["text"]
    url = ("https://api.groq.com/openai/v1/chat/completions" if p == "groq"
           else "https://openrouter.ai/api/v1/chat/completions")
    last = None
    for m in _cands(p):
        try:
            data = _post_json(url, {"model": m,
                                    "messages": [{"role": "user", "content": prompt}]},
                              {"Authorization": "Bearer " + RAW_AI_KEY})
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 404, 429):
                _MODELS_CACHE.pop("cand_" + p, None)
                continue
            raise
    raise last or RuntimeError("sin_modelos")
API_BASE = "https://api.football-data.org/v4"
TOKEN = os.environ.get("FOOTBALL_DATA_API_KEY", "")

# Ventana de días hacia atrás para consultar partidos terminados (rachas BTTS/Over).
FINISHED_WINDOW_DAYS = 150

import time, hashlib

# ---------- CONTROL DE ACCESO (venta por codigos) ----------
ACCESS_SECRET = os.environ.get("ACCESS_SECRET", "goalinsight-2026")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "betmaster2026")
ACCESS_FILE = ROOT / "accesos.json"

def load_access():
    try:
        return json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"GOL-DEMO-2026": {"devices": [], "max": 2, "until": "2026-09-30"}}

def save_access(a):
    ACCESS_FILE.write_text(json.dumps(a, indent=1), encoding="utf-8")

def mk_token(code):
    return hashlib.sha256((code + ACCESS_SECRET).encode()).hexdigest()[:32]

def _cookie(self, name):
    for part in (self.headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None

def session_ok(self):
    tok = _cookie(self, "gi_access")
    if not tok:
        return False
    today = date.today().isoformat()
    for code, info in load_access().items():
        if mk_token(code) == tok and info.get("until", "") >= today:
            return True
    return False

# ---------- cache de datos (multiplica la cuota gratuita) ----------
_FD_CACHE = {}
_FD_TTL = 600  # 10 minutos

def _cached_football(handler_self, endpoint):
    now = time.time()
    hit = _FD_CACHE.get(endpoint)
    if hit and now - hit[0] < _FD_TTL:
        return hit[1], hit[2]
    st, data = handler_self._football_data(endpoint)
    _FD_CACHE[endpoint] = (now, st, data)
    return st, data


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

    def do_POST(self):
        if self.path == "/api/login":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            except Exception:
                return self._json(400, {"ok": False, "error": "Peticion invalida."})
            code = str(body.get("code", "")).strip().upper()[:24]
            dev = str(body.get("device", "anon"))[:64]
            acc = load_access()
            info = acc.get(code)
            today = date.today().isoformat()
            if not info or info.get("until", "") < today:
                return self._json(200, {"ok": False, "error": "Codigo no valido o vencido. Pide el tuyo al admin."})
            devs = info.setdefault("devices", [])
            if dev not in devs and len(devs) >= int(info.get("max", 2)):
                return self._json(200, {"ok": False, "error": "Este codigo ya esta activo en el maximo de dispositivos."})
            if dev not in devs:
                devs.append(dev)
                save_access(acc)
            tok = mk_token(code)
            body2 = json.dumps({"ok": True, "until": info.get("until", "")}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body2)))
            self.send_header("Set-Cookie", "gi_access=" + tok + "; Path=/; Max-Age=2592000; SameSite=Lax")
            self.end_headers()
            self.wfile.write(body2)
            return
        if self.path.startswith("/api/admin/"):
            if self.headers.get("X-Admin", "") != ADMIN_KEY:
                return self._json(200, {"ok": False, "error": "Clave de admin incorrecta."})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            except Exception:
                return self._json(400, {"ok": False, "error": "Peticion invalida."})
            acc = load_access()
            import random, string
            today = date.today()
            if self.path == "/api/admin/create":
                days = int(body.get("days", 30))
                code = str(body.get("code", "")).strip().upper()[:20]
                if not code:
                    code = "GOL-" + "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4)) + "-26"
                until = (today + timedelta(days=days)).isoformat() if days < 9000 else "2099-12-31"
                acc[code] = {"devices": [], "max": 2, "until": until}
                save_access(acc)
                return self._json(200, {"ok": True, "code": code, "until": until})
            if self.path == "/api/admin/renew":
                code = str(body.get("code", "")).strip().upper()
                days = int(body.get("days", 30))
                if code in acc:
                    acc[code]["until"] = (date.today() + timedelta(days=days)).isoformat() if days < 9000 else "2099-12-31"
                    acc[code]["devices"] = []
                    save_access(acc)
                    return self._json(200, {"ok": True, "code": code, "until": acc[code]["until"]})
                return self._json(200, {"ok": False, "error": "Codigo no existe"})
            if self.path == "/api/admin/delete":
                code = str(body.get("code", "")).strip().upper()
                if code in acc:
                    acc.pop(code)
                    save_access(acc)
                    return self._json(200, {"ok": True})
                return self._json(200, {"ok": False, "error": "Codigo no existe"})
            return self._json(404, {"ok": False})
        self._json(404, {"ok": False, "error": "Ruta no encontrada."})

    def do_GET(self):
        if self.path == "/api/session":
            self._json(200, {"ok": session_ok(self)})
            return
        if self.path == "/api/admin/list":
            if self.headers.get("X-Admin", "") != ADMIN_KEY:
                return self._json(200, {"ok": False, "error": "Clave de admin incorrecta."})
            acc = load_access()
            out = {c: {"devices": len(i.get("devices", [])), "max": i.get("max", 2), "until": i.get("until", "")} for c, i in acc.items()}
            return self._json(200, {"ok": True, "codes": out})
        if self.path.startswith("/api/") and not self.path.startswith("/api/admin/") and self.path not in ("/api/status", "/api/session"):
            if not session_ok(self):
                self._json(403, {"ok": False, "locked": True, "error": "Necesitas un codigo de acceso."})
                return
        if self.path == "/api/status":
            enmascarada = (TOKEN[:4] + "..." + TOKEN[-4:]) if TOKEN else ""
            self._json(200, {"ok": bool(TOKEN), "provider": "football-data.org", "ai": ai_prov(), "message": "Conectado" if TOKEN else "Configura la clave en FOOTBALL_DATA_API_KEY.", "longitud": len(TOKEN), "vista": enmascarada})
            return
        if self.path == "/api/ai" and self.command == "POST":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            except Exception:
                return self._json(400, {"ok": False, "error": "Peticion invalida."})
            if not ai_prov():
                return self._json(200, {"ok": False, "error": "IA no configurada (MATE_AI_KEY)."})
            datos = str(body.get("data", ""))[:1500]
            prompt = ("Eres un analista estadistico de futbol experto en mercados BTTS (ambos anotan) y Over 2.5. "
                      "Datos del partido calculados por el motor de la app:\n" + datos +
                      "\n\nEscribe un veredicto breve (60-90 palabras) en espanol, tono profesional y sobrio. "
                      "Indica que mercado (BTTS u Over 2.5) tiene mejor perspectiva segun rachas y goles esperados, "
                      "cierra con una advertencia de que ningun mercado es seguro. Texto plano sin markdown.")
            try:
                texto = ask_llm(prompt)
                return self._json(200, {"ok": True, "texto": texto.strip()[:900], "ia": ai_prov()})
            except Exception as e:
                return self._json(200, {"ok": False, "error": "Fallo la IA: %s" % e})
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
        # NUEVO: partidos terminados de los ultimos FINISHED_WINDOW_DAYS dias.
        # Con esto la app calcula rachas reales de BTTS y Over 2.5 por equipo.
        if self.path.startswith("/api/finished/"):
            code = self.path.rsplit("/", 1)[-1].upper()
            if not code.replace("-", "").isalnum():
                self._json(400, {"ok": False, "error": "Código de competición inválido."})
                return
            today = date.today()
            date_from = today - timedelta(days=FINISHED_WINDOW_DAYS)
            endpoint = (
                f"/competitions/{code}/matches"
                f"?status=FINISHED&dateFrom={date_from.isoformat()}&dateTo={today.isoformat()}"
            )
            status, data = self._football_data(endpoint)
            self._json(status, data)
            return
        super().do_GET()


if __name__ == "__main__":
    os.chdir(ROOT)
    port = int(os.environ.get("PORT", "8080"))
    print(f"BetMaster AI escuchando en http://0.0.0.0:{port}")
    print("Proveedor: football-data.org | Token configurado:", "sí" if TOKEN else "no")
    ThreadingHTTPServer(("0.0.0.0", port), AppHandler).serve_forever()
