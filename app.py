"""Bot Telegram para gols da Série A: recebe webhooks da Live Football API."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request

log = logging.getLogger("gols")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
# HTTPX registra URLs completas; nunca exibir credenciais em query strings.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

API_URL = "https://live-football-api.com/api/v1"
API_KEY = os.getenv("LFA_API_KEY", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "@legendaskwkwkek").strip()
SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
WEB_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
DB_PATH = os.getenv("DB_PATH", "/tmp/gols.sqlite3")
LEAGUE_ID = os.getenv("LFA_LEAGUE_ID", "").strip()
REGISTERED = False
TELEGRAM_READY = False

ALIASES = {
    "Paranaense": "Athletico-PR",
    "Athletico Paranaense": "Athletico-PR",
    "Atletico Paranaense": "Athletico-PR",
    "Atletico Mineiro": "Atlético-MG",
    "Atletico MG": "Atlético-MG",
    "Gremio": "Grêmio",
    "Sao Paulo": "São Paulo",
    "Vasco da Gama": "Vasco",
    "Bragantino": "RB Bragantino",
}


def configured() -> bool:
    return bool(API_KEY and TG_TOKEN and TG_CHAT and SECRET and len(SECRET) >= 24
                and WEB_URL.startswith("https://"))


def database():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    return con


def setup_db():
    with database() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS goals (
            key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            tries INTEGER NOT NULL DEFAULT 0,
            next_try REAL NOT NULL DEFAULT 0,
            telegram_id TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS known_players (
            id TEXT PRIMARY KEY, name TEXT NOT NULL
        )""")
        con.execute("UPDATE goals SET status='pending' WHERE status='sending'")


def required_score(goal: dict, home: dict, away: dict) -> tuple[int, int]:
    s = str(goal.get("score") or "").strip()
    if s:
        m = re.fullmatch(r"(\d+)\s*[-:x]\s*(\d+)", s)
        if not m:
            raise ValueError("Placar de evento inválido")
        return int(m.group(1)), int(m.group(2))
    try:
        return int(home["score"]), int(away["score"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("Placar ausente") from exc


def parse_goal(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get("event") != "goal":
        raise ValueError("Evento inválido")
    match = payload.get("match") or {}
    goal = payload.get("goal") or {}
    if not isinstance(match, dict) or not isinstance(goal, dict):
        raise ValueError("Partida inválida")
    match_id = str(match.get("id") or "")
    home = match.get("home") or {}
    away = match.get("away") or {}
    side = str(goal.get("side") or "")
    if not match_id or side not in ("home", "away") or not home.get("name") or not away.get("name"):
        raise ValueError("Dados essenciais do gol ausentes")
    h, a = required_score(goal, home, away)
    player = goal.get("player") if isinstance(goal.get("player"), dict) else {}
    assist = goal.get("assist") if isinstance(goal.get("assist"), dict) else {}
    own = bool(goal.get("is_own_goal") is True or goal.get("own_goal") is True or
               str(goal.get("type") or "").lower() in ("own_goal", "own goal"))
    return {
        "id": match_id, "home": ALIASES.get(home["name"], home["name"]),
        "away": ALIASES.get(away["name"], away["name"]),
        "side": side, "h": h, "a": a, "score": f"{h}-{a}",
        "scorer": str(player.get("name") or "Autor não informado"),
        "assist": assist, "own": own,
    }


def goal_id(g: dict) -> str:
    # Cada placar pós-gol é único dentro da mesma partida.
    return hashlib.sha256(f"{g['id']}|{g['score']}".encode()).hexdigest()


def full_name(player) -> str | None:
    if not isinstance(player, dict):
        return None
    first = str(player.get("first_name") or player.get("firstName") or "").strip()
    last = str(player.get("last_name") or player.get("lastName") or "").strip()
    if first and last and not first.endswith(".") and not last.endswith("."):
        return first + " " + last
    name = str(player.get("name") or "").strip()
    words = name.split()
    if len(words) >= 2 and len(words[0]) > 1 and not words[0].endswith("."):
        return name
    return None


def format_goal(g: dict, assister: str | None = None) -> str:
    beneficiary = g["home"] if g["side"] == "home" else g["away"]
    gc = " (GC)" if g["own"] else ""
    assist = " 🥾 " + assister if assister and not g["own"] else ""
    return (
        f"🇧🇷 GOL DO {beneficiary.upper()}!\n"
        f"⚽️ {g['scorer']}{gc}{assist}\n"
        f"🇧🇷 {g['home']} {g['h']} x {g['a']} {g['away']} 🇧🇷\n"
        "🏆 Brasileirão Série A\n\n"
        "📸 Reprodução\n"
        "🔔 Compartilhe e siga o canal!"
    )


async def api(client: httpx.AsyncClient, method: str, endpoint: str,
              json_data=None, **params):
    """Nunca registrar URLs ou mensagens de erro com a API key."""
    r = await client.request(
        method, f"{API_URL}/{endpoint}",
        params={**params, "api_key": API_KEY},
        json=json_data, timeout=12,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"API HTTP {r.status_code} em {endpoint}")
    data = r.json()
    if data.get("success") is False:
        raise RuntimeError(f"API rejeitou {endpoint}")
    return data.get("data", data)


async def get_league_id(client: httpx.AsyncClient) -> str:
    if LEAGUE_ID:
        return LEAGUE_ID
    data = await api(client, "GET", "leagues")
    groups = data.get("data") or data.get("countries") or []
    if not isinstance(groups, list):
        raise RuntimeError("Lista de ligas inesperada")
    ids = []
    for country in groups:
        if str(country.get("country") or "").lower() not in ("brazil", "brasil"):
            continue
        for league in country.get("leagues") or []:
            name = str(league.get("name") or "").casefold()
            if name in ("serie a", "série a", "brasileiro serie a",
                        "brasileiro série a", "brasileirão série a"):
                ids.append(str(league["id"]))
    if len(ids) != 1:
        raise RuntimeError("Série A não identificada: defina LFA_LEAGUE_ID")
    return ids[0]


async def register_webhook(client: httpx.AsyncClient):
    global REGISTERED
    league_id = await get_league_id(client)
    url = WEB_URL + "/webhooks/football/" + SECRET
    existing = await api(client, "GET", "webhook/register")
    for item in existing.get("webhooks") or []:
        if item.get("webhook_url") == url:
            await api(client, "PATCH", "webhook/register",
                      id=item["id"], json_data={
                          "events": ["goal"], "leagues": [league_id], "is_active": True
                      })
            REGISTERED = True
            log.info("Webhook atualizado: Série A")
            return
    await api(client, "POST", "webhook/register", json_data={
        "webhook_url": url,
        "label": "Bot Gols Brasileirão Telegram",
        "events": ["goal"], "leagues": [league_id],
    })
    REGISTERED = True
    log.info("Webhook registrado: Série A")


async def registration_loop():
    while True:
        if configured():
            try:
                async with httpx.AsyncClient() as client:
                    await register_webhook(client)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Não foi possível registrar webhook (%s)", type(exc).__name__)
        await asyncio.sleep(300)


async def player_name(client: httpx.AsyncClient, player: dict) -> str | None:
    if (name := full_name(player)):
        return name
    pid = str(player.get("id") or "").strip()
    if not pid:
        return None
    with database() as con:
        cached = con.execute("SELECT name FROM known_players WHERE id=?", (pid,)).fetchone()
    if cached:
        return cached["name"]
    try:
        data = await api(client, "GET", "player", player_id=pid)
        name = full_name(data)
        if name:
            with database() as con:
                con.execute("INSERT OR REPLACE INTO known_players(id,name) VALUES(?,?)", (pid, name))
        return name
    except Exception as exc:
        log.info("Assistência por extenso indisponível (%s)", type(exc).__name__)
        return None


async def enrich(client: httpx.AsyncClient, g: dict):
    try:
        d = await api(client, "GET", "live_match_details", match_id=g["id"])
        for ev in d.get("events") or []:
            if ev.get("type") not in ("goal", "own_goal"):
                continue
            details = ev.get("detail") or {}
            if str(details.get("score") or "").strip().replace(" ", "") != g["score"]:
                continue
            g["own"] = bool(g["own"] or ev.get("type") == "own_goal" or
                            details.get("is_own_goal") or details.get("own_goal"))
            if not g["assist"] and isinstance(details.get("assist"), dict):
                g["assist"] = details["assist"]
            return
    except Exception as exc:
        log.info("Detalhes indisponíveis (%s)", type(exc).__name__)


async def send_message(client: httpx.AsyncClient, message: str) -> str:
    r = await client.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": message, "disable_web_page_preview": True},
        timeout=15,
    )
    data = r.json()
    if r.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(f"Telegram HTTP {r.status_code}: {str(data.get('description', 'erro'))[:100]}")
    return str((data.get("result") or {}).get("message_id", ""))


async def send_pending(client: httpx.AsyncClient) -> bool:
    with database() as con:
        row = con.execute(
            "SELECT * FROM goals WHERE status='pending' AND next_try<=? ORDER BY rowid LIMIT 1",
            (time.time(),),
        ).fetchone()
        if not row:
            return False
        con.execute("UPDATE goals SET status='sending' WHERE key=?", (row["key"],))
    try:
        g = parse_goal(json.loads(row["payload"]))
        await enrich(client, g)
        assister = None if g["own"] else await player_name(client, g["assist"])
        message_id = await send_message(client, format_goal(g, assister))
        with database() as con:
            con.execute("UPDATE goals SET status='sent', telegram_id=? WHERE key=?",
                        (message_id, row["key"]))
        log.info("Gol publicado: partida %s, placar %s", g["id"], g["score"])
    except asyncio.CancelledError:
        with database() as con:
            con.execute("UPDATE goals SET status='pending' WHERE key=?", (row["key"],))
        raise
    except Exception as exc:
        tries = int(row["tries"]) + 1
        backoff = min(300, 3 * 2 ** min(tries, 6))
        with database() as con:
            con.execute("UPDATE goals SET status='pending', tries=?, next_try=? WHERE key=?",
                        (tries, time.time() + backoff, row["key"]))
        log.warning("Falha no envio, tentativa %s (%s)", tries, type(exc).__name__)
    return True


async def telegram_diagnostics():
    """Verifica se o bot tem acesso ao canal sem publicar mensagens de teste."""
    global TELEGRAM_READY
    async with httpx.AsyncClient(timeout=12) as client:
        while True:
            try:
                if TG_TOKEN and TG_CHAT:
                    me = await client.post(
                        f"https://api.telegram.org/bot{TG_TOKEN}/getMe")
                    mine = me.json()
                    if not mine.get("ok"):
                        raise RuntimeError("Token do Telegram não aceito")
                    bot_id = mine["result"]["id"]
                    member = await client.post(
                        f"https://api.telegram.org/bot{TG_TOKEN}/getChatMember",
                        json={"chat_id": TG_CHAT, "user_id": bot_id},
                    )
                    status = member.json()
                    info = status.get("result") or {}
                    TELEGRAM_READY = bool(
                        status.get("ok") and info.get("status") in ("administrator", "creator")
                        and info.get("can_post_messages", True))
                    if TELEGRAM_READY:
                        log.info("Bot reconhecido como administrador com permissão de publicar")
                    else:
                        log.warning("Bot ainda não tem permissão para publicar no canal")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                TELEGRAM_READY = False
                log.warning("Diagnóstico Telegram: %s", type(exc).__name__)
            await asyncio.sleep(300)


async def worker():
    async with httpx.AsyncClient() as client:
        while True:
            try:
                if configured() and await send_pending(client):
                    continue
                await asyncio.sleep(0.6)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Erro na fila")
                await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_db()
    registrar = asyncio.create_task(registration_loop())
    sender = asyncio.create_task(worker())
    verifier = asyncio.create_task(telegram_diagnostics())
    yield
    for task in (registrar, sender, verifier):
        task.cancel()
    for task in (registrar, sender):
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Gols do Brasileirão", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.get("/health")
async def health():
    with database() as con:
        sent = con.execute("SELECT count(*) FROM goals WHERE status='sent'").fetchone()[0]
        waiting = con.execute("SELECT count(*) FROM goals WHERE status='pending'").fetchone()[0]
    return {"ok": True, "configured": configured(), "webhook_registered": REGISTERED,
            "telegram_admin": TELEGRAM_READY,
            "goals_sent": sent, "pending": waiting}


@app.post("/webhooks/football/{secret}")
async def webhook(secret: str, request: Request):
    if not SECRET or not hmac.compare_digest(secret, SECRET):
        raise HTTPException(status_code=404, detail="Not found")
    if int(request.headers.get("content-length", "0") or 0) > 65000:
        raise HTTPException(status_code=413, detail="Body too large")
    body = await request.body()
    if len(body) > 65000:
        raise HTTPException(status_code=413, detail="Body too large")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("JSON inválido")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="JSON inválido")
    if payload.get("event") != "goal":
        return {"ok": True, "ignored": True}
    try:
        g = parse_goal(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    key = goal_id(g)
    with database() as con:
        result = con.execute(
            "INSERT OR IGNORE INTO goals(key,payload) VALUES(?,?)",
            (key, json.dumps(payload, ensure_ascii=False)),
        )
    return {"ok": True, "queued": result.rowcount == 1}
