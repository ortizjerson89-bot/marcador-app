"""
providers.py
Conectores a APIs reales de datos deportivos para autocompletar los formularios
en vez de digitar las estadísticas a mano.

Proveedores:
- FootballProvider   -> API-Football (api-sports.io) — v3.football.api-sports.io
- BasketballProvider -> API-Basketball (api-sports.io) — v1.basketball.api-sports.io
- TennisProvider     -> Tennis API ATP/WTA/ITF (RapidAPI) — estructura menos estandarizada,
                        revisar y ajustar claves si tu respuesta real difiere (ver notas abajo).

Autenticación:
- Fútbol y baloncesto comparten la familia api-sports.io: variable de entorno API_SPORTS_KEY,
  enviada en el header 'x-apisports-key'.
- Tenis usa RapidAPI: variable de entorno RAPIDAPI_KEY, enviada en 'X-RapidAPI-Key' +
  'X-RapidAPI-Host'.

Todas las respuestas se cachean en SQLite (tabla cache_api) con un TTL, para no gastar
la cuota diaria gratuita (normalmente 100 llamadas/día) en búsquedas repetidas.
"""

import os
import json
import sqlite3
import time
from pathlib import Path

import requests

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "pronosticos.db"

API_SPORTS_KEY = os.environ.get("API_SPORTS_KEY", "")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

TTL_BUSQUEDA = 60 * 60 * 12       # 12h para búsquedas de equipo/jugador
TTL_STATS = 60 * 60 * 6           # 6h para estadísticas (cambian tras cada partido)


class ProviderError(Exception):
    """Error controlado: falta API key, equipo no encontrado, respuesta inesperada, etc."""
    pass


# ---------------------------------------------------------------------------
# Caché genérica en SQLite (evita quemar la cuota diaria en búsquedas repetidas)
# ---------------------------------------------------------------------------

def _init_cache_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_api (
            clave TEXT PRIMARY KEY,
            valor_json TEXT NOT NULL,
            expira_en REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _cache_get(clave: str):
    _init_cache_table()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT valor_json, expira_en FROM cache_api WHERE clave = ?", (clave,)).fetchone()
    conn.close()
    if row and row[1] > time.time():
        return json.loads(row[0])
    return None


def _cache_set(clave: str, valor: dict, ttl: int):
    _init_cache_table()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO cache_api (clave, valor_json, expira_en) VALUES (?, ?, ?)",
        (clave, json.dumps(valor, ensure_ascii=False), time.time() + ttl),
    )
    conn.commit()
    conn.close()


def _get_con_cache(url: str, headers: dict, params: dict, ttl: int, timeout: int = 12) -> dict:
    clave = f"{url}?{json.dumps(params, sort_keys=True)}"
    cacheado = _cache_get(clave)
    if cacheado is not None:
        return cacheado

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise ProviderError(f"No se pudo conectar con la API ({url}): {e}")

    if resp.status_code == 401 or resp.status_code == 403:
        raise ProviderError("La API rechazó la clave (401/403). Revisa tu variable de entorno de API key.")
    if resp.status_code == 429:
        raise ProviderError("Se agotó la cuota de peticiones de tu plan (429). Intenta más tarde o reduce las búsquedas.")
    if resp.status_code >= 400:
        raise ProviderError(f"La API respondió con error {resp.status_code}: {resp.text[:300]}")

    try:
        data = resp.json()
    except ValueError:
        raise ProviderError(f"La API no devolvió JSON válido. Respuesta cruda: {resp.text[:300]}")

    _cache_set(clave, data, ttl)
    return data


# ---------------------------------------------------------------------------
# FÚTBOL — API-Football (api-sports.io)
# ---------------------------------------------------------------------------

class FootballProvider:
    BASE = "https://v3.football.api-sports.io"

    def _headers(self):
        if not API_SPORTS_KEY:
            raise ProviderError(
                "Falta configurar API_SPORTS_KEY (API-Football / api-sports.io). "
                "Regístrate gratis en https://dashboard.api-football.com y define la variable de entorno."
            )
        return {"x-apisports-key": API_SPORTS_KEY}

    def buscar_equipo(self, nombre: str) -> list:
        data = _get_con_cache(f"{self.BASE}/teams", self._headers(), {"search": nombre}, TTL_BUSQUEDA)
        resultados = []
        for item in data.get("response", []):
            t = item.get("team", {})
            resultados.append({
                "id": t.get("id"),
                "nombre": t.get("name"),
                "pais": t.get("country"),
                "logo": t.get("logo"),
            })
        return resultados

    def _liga_actual(self, team_id: int) -> tuple:
        """Devuelve (league_id, season_year) de la primera liga doméstica activa del equipo."""
        data = _get_con_cache(f"{self.BASE}/leagues", self._headers(), {"team": team_id, "current": "true"}, TTL_BUSQUEDA)
        respuesta = data.get("response", [])
        if not respuesta:
            raise ProviderError("No se encontró una liga activa para este equipo (¿está fuera de temporada?).")
        liga = respuesta[0]
        league_id = liga["league"]["id"]
        temporadas = liga.get("seasons", [])
        season = next((s["year"] for s in temporadas if s.get("current")), temporadas[-1]["year"] if temporadas else None)
        if season is None:
            raise ProviderError("No se pudo determinar la temporada activa del equipo.")
        return league_id, season

    @staticmethod
    def _forma_a_escala5(form_str: str) -> float:
        """Convierte 'WWDLW' -> escala 0-5 (W=1pt, D=0.5pt, L=0pt sobre los últimos 5)."""
        if not form_str:
            return 2.5
        ultimos5 = form_str[-5:]
        puntos = sum(1.0 if c == "W" else 0.5 if c == "D" else 0.0 for c in ultimos5)
        return round(puntos, 2)

    def stats_equipo(self, team_id: int, rol: str) -> dict:
        """rol: 'local' o 'visita'. Devuelve campos listos para el formulario de fútbol."""
        league_id, season = self._liga_actual(team_id)
        data = _get_con_cache(
            f"{self.BASE}/teams/statistics", self._headers(),
            {"league": league_id, "season": season, "team": team_id}, TTL_STATS,
        )
        r = data.get("response")
        if not r:
            raise ProviderError("La API no devolvió estadísticas para este equipo/liga/temporada.")

        goles_for = r.get("goals", {}).get("for", {}).get("average", {})
        goles_against = r.get("goals", {}).get("against", {}).get("average", {})
        forma = self._forma_a_escala5(r.get("form", ""))

        if rol == "local":
            campos = {
                "local_goles_favor_local": float(goles_for.get("home", 0) or 0),
                "local_goles_contra_local": float(goles_against.get("home", 0) or 0),
                "local_forma": forma,
            }
        else:
            campos = {
                "visita_goles_favor_visita": float(goles_for.get("away", 0) or 0),
                "visita_goles_contra_visita": float(goles_against.get("away", 0) or 0),
                "visita_forma": forma,
            }
        campos["_liga_id"] = league_id
        campos["_season"] = season
        campos["_equipo_id"] = team_id
        return campos

    def stats_corners_tiros(self, team_id: int, rol: str, ultimos_n: int = 5) -> dict:
        """
        Promedia corners y tiros a puerta de los últimos N partidos jugados en su
        condición de local o visitante, revisando partido por partido.
        Consume ~ (1 + ultimos_n) llamadas — cuidado con la cuota diaria.
        """
        venue = "home" if rol == "local" else "away"
        data = _get_con_cache(
            f"{self.BASE}/fixtures", self._headers(),
            {"team": team_id, "last": 20, "status": "FT"}, TTL_STATS,
        )
        fixtures = data.get("response", [])
        relevantes = [
            f for f in fixtures
            if (f["teams"]["home"]["id"] == team_id) == (venue == "home")
        ][:ultimos_n]

        if not relevantes:
            return {}

        corners_vals, tiros_vals = [], []
        for f in relevantes:
            fixture_id = f["fixture"]["id"]
            stats_data = _get_con_cache(
                f"{self.BASE}/fixtures/statistics", self._headers(),
                {"fixture": fixture_id, "team": team_id}, TTL_STATS,
            )
            for bloque in stats_data.get("response", []):
                for stat in bloque.get("statistics", []):
                    tipo = (stat.get("type") or "").lower()
                    valor = stat.get("value")
                    if valor is None:
                        continue
                    if "corner" in tipo:
                        corners_vals.append(float(valor))
                    elif tipo == "shots on goal":
                        tiros_vals.append(float(valor))

        prefijo = "local" if rol == "local" else "visita"
        resultado = {}
        if corners_vals:
            resultado[f"{prefijo}_corners_favor"] = round(sum(corners_vals) / len(corners_vals), 2)
        if tiros_vals:
            resultado[f"{prefijo}_tiros_favor"] = round(sum(tiros_vals) / len(tiros_vals), 2)
        resultado["_partidos_usados"] = len(relevantes)
        return resultado

    def buscar_jugador(self, nombre: str, team_id: int, season: int) -> list:
        data = _get_con_cache(
            f"{self.BASE}/players", self._headers(),
            {"search": nombre, "team": team_id, "season": season}, TTL_BUSQUEDA,
        )
        resultados = []
        for item in data.get("response", []):
            j = item.get("player", {})
            resultados.append({"id": j.get("id"), "nombre": j.get("name"), "foto": j.get("photo")})
        return resultados

    def stats_jugador(self, player_id: int, team_id: int, season: int) -> dict:
        data = _get_con_cache(
            f"{self.BASE}/players", self._headers(),
            {"id": player_id, "season": season}, TTL_STATS,
        )
        respuesta = data.get("response", [])
        if not respuesta:
            raise ProviderError("No se encontraron estadísticas para este jugador en la temporada indicada.")

        stats_equipo = None
        for st in respuesta[0].get("statistics", []):
            if st.get("team", {}).get("id") == team_id:
                stats_equipo = st
                break
        if stats_equipo is None and respuesta[0].get("statistics"):
            stats_equipo = respuesta[0]["statistics"][0]
        if stats_equipo is None:
            raise ProviderError("El jugador no tiene estadísticas registradas con este equipo.")

        apariciones = max(int(stats_equipo.get("games", {}).get("appearences") or 0), 1)
        minutos_totales = float(stats_equipo.get("games", {}).get("minutes") or 0)
        goles = float(stats_equipo.get("goals", {}).get("total") or 0)
        asistencias = float(stats_equipo.get("goals", {}).get("assists") or 0)
        tiros_puerta = float(stats_equipo.get("shots", {}).get("on") or 0)

        return {
            "nombre": respuesta[0].get("player", {}).get("name"),
            "goles": round(goles / apariciones, 3),
            "tiros": round(tiros_puerta / apariciones, 2),
            "asistencias": round(asistencias / apariciones, 3),
            "minutos": round(minutos_totales / apariciones, 0),
        }

    # -------------------- Próximo partido (usado por predicciones y bajas) --------------------

    def _proximo_fixture_id(self, team_a_id: int, team_b_id: int):
        data = _get_con_cache(
            f"{self.BASE}/fixtures/headtohead", self._headers(),
            {"h2h": f"{team_a_id}-{team_b_id}", "next": 1}, TTL_STATS,
        )
        respuesta = data.get("response", [])
        if not respuesta:
            return None
        return respuesta[0]["fixture"]["id"]

    def prediccion_oficial(self, team_a_id: int, team_b_id: int) -> dict:
        """
        Trae el pronóstico propio del algoritmo de API-Football para el próximo
        partido entre estos equipos, para comparar contra el de esta app.
        """
        fixture_id = self._proximo_fixture_id(team_a_id, team_b_id)
        if fixture_id is None:
            raise ProviderError("No hay un próximo partido programado entre estos equipos según API-Football.")

        data = _get_con_cache(f"{self.BASE}/predictions", self._headers(), {"fixture": fixture_id}, TTL_STATS)
        respuesta = data.get("response", [])
        if not respuesta:
            raise ProviderError("API-Football no tiene un pronóstico calculado para este partido todavía.")

        pred = respuesta[0].get("predictions", {})
        porcentaje = pred.get("percent", {})
        return {
            "ganador_sugerido": (pred.get("winner") or {}).get("name"),
            "consejo": pred.get("advice"),
            "under_over": pred.get("under_over"),
            "porcentaje_local": porcentaje.get("home"),
            "porcentaje_empate": porcentaje.get("draw"),
            "porcentaje_visita": porcentaje.get("away"),
        }

    def lesionados(self, team_id: int, season: int) -> list:
        """Bajas/lesionados actuales del equipo. Puramente informativo — el motor
        no ajusta sus números automáticamente con esto, pero te avisa antes de que
        confíes en un promedio que ya no refleja la alineación real."""
        data = _get_con_cache(
            f"{self.BASE}/injuries", self._headers(), {"team": team_id, "season": season}, TTL_STATS,
        )
        resultados = []
        for item in data.get("response", [])[:10]:
            jugador = item.get("player", {})
            resultados.append({
                "nombre": jugador.get("name"),
                "motivo": jugador.get("reason") or jugador.get("type"),
            })
        return resultados


# ---------------------------------------------------------------------------
# BALONCESTO — API-Basketball (api-sports.io)
# ---------------------------------------------------------------------------

class BasketballProvider:
    BASE = "https://v1.basketball.api-sports.io"

    def _headers(self):
        if not API_SPORTS_KEY:
            raise ProviderError(
                "Falta configurar API_SPORTS_KEY (API-Basketball / api-sports.io). "
                "Es la misma clave/cuenta que API-Football, pero con cuota separada."
            )
        return {"x-apisports-key": API_SPORTS_KEY}

    def buscar_equipo(self, nombre: str) -> list:
        data = _get_con_cache(f"{self.BASE}/teams", self._headers(), {"search": nombre}, TTL_BUSQUEDA)
        resultados = []
        for t in data.get("response", []):
            resultados.append({"id": t.get("id"), "nombre": t.get("name"), "logo": t.get("logo")})
        return resultados

    def _liga_actual(self, team_id: int) -> tuple:
        data = _get_con_cache(f"{self.BASE}/leagues", self._headers(), {"team": team_id}, TTL_BUSQUEDA)
        respuesta = data.get("response", [])
        if not respuesta:
            raise ProviderError("No se encontró una liga para este equipo.")
        liga = respuesta[-1]  # la más reciente suele venir al final
        league_id = liga["id"]
        temporadas = liga.get("seasons", [])
        if not temporadas:
            raise ProviderError("No se encontró temporada para este equipo.")
        ultima = temporadas[-1]
        season = ultima.get("season") or ultima.get("year")
        return league_id, season

    def stats_equipo(self, team_id: int, rol: str) -> dict:
        league_id, season = self._liga_actual(team_id)
        data = _get_con_cache(
            f"{self.BASE}/teams/statistics", self._headers(),
            {"league": league_id, "season": season, "team": team_id}, TTL_STATS,
        )
        r = data.get("response")
        if not r:
            raise ProviderError("La API no devolvió estadísticas para este equipo/liga/temporada.")

        pts_for = r.get("points", {}).get("for", {}).get("average", {})
        pts_against = r.get("points", {}).get("against", {}).get("average", {})

        if rol == "local":
            return {
                "local_pts_favor": float(pts_for.get("home", 0) or 0),
                "local_pts_contra": float(pts_against.get("home", 0) or 0),
            }
        else:
            return {
                "visita_pts_favor": float(pts_for.get("away", 0) or 0),
                "visita_pts_contra": float(pts_against.get("away", 0) or 0),
            }


# ---------------------------------------------------------------------------
# TENIS — Tennis API ATP/WTA/ITF (RapidAPI)
# NOTA DE HONESTIDAD: a diferencia de fútbol/baloncesto, este proveedor no forma
# parte de la familia api-sports.io y su documentación pública es más delgada.
# No fue posible probar una llamada real en este entorno (sin acceso a internet).
# Si tras configurar tu RAPIDAPI_KEY el mapeo de campos falla, el error incluirá
# un fragmento del JSON crudo para que ajustes las claves aquí abajo.
# ---------------------------------------------------------------------------

class TennisProvider:
    HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
    BASE = f"https://{HOST}"

    def _headers(self):
        if not RAPIDAPI_KEY:
            raise ProviderError(
                "Falta configurar RAPIDAPI_KEY. Suscríbete (tiene plan gratuito limitado) en "
                "https://rapidapi.com/ y busca 'Tennis API ATP WTA ITF'."
            )
        return {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": self.HOST}

    def buscar_jugador(self, nombre: str, tour: str = "atp") -> dict:
        """
        Trae el perfil de un jugador por nombre. Esta API acepta el nombre directamente
        en la URL del perfil en vez de un endpoint de búsqueda separado.
        """
        data = _get_con_cache(f"{self.BASE}/tennis/v2/profile/{nombre}", self._headers(), {}, TTL_BUSQUEDA)
        return data

    def stats_jugador(self, nombre: str, tour: str = "atp") -> dict:
        perfil = self.buscar_jugador(nombre, tour)

        # Intento defensivo de mapeo: la API no documenta un único nombre de campo
        # estable para "puntos de ranking", así que probamos varias claves conocidas.
        posibles_claves_ranking = ["rankingPoints", "ranking_points", "points", "atpPoints", "wtaPoints"]
        posibles_claves_nombre = ["name", "playerName", "fullName"]
        posibles_claves_winrate = ["winPercentage", "win_rate", "winRate"]

        def buscar_clave(d: dict, claves: list):
            for c in claves:
                if c in d:
                    return d[c]
            return None

        contenedor = perfil.get("data", perfil.get("response", perfil))
        if isinstance(contenedor, list):
            contenedor = contenedor[0] if contenedor else {}

        ranking_pts = buscar_clave(contenedor, posibles_claves_ranking)
        nombre_real = buscar_clave(contenedor, posibles_claves_nombre) or nombre
        winrate = buscar_clave(contenedor, posibles_claves_winrate)

        if ranking_pts is None:
            raise ProviderError(
                "No pude ubicar los puntos de ranking en la respuesta de la API de tenis. "
                f"Fragmento crudo para ajustar el mapeo en providers.py: {json.dumps(contenedor)[:400]}"
            )

        return {
            "nombre": nombre_real,
            "ranking_pts": float(ranking_pts),
            "winrate": float(winrate) if winrate is not None else None,
        }


football_provider = FootballProvider()
basketball_provider = BasketballProvider()
tennis_provider = TennisProvider()


# ---------------------------------------------------------------------------
# CUOTAS Y VALUE BETS — The Odds API (the-odds-api.com)
# ---------------------------------------------------------------------------

def _normalizar(texto: str) -> str:
    return "".join(c for c in (texto or "").lower().strip() if c.isalnum() or c.isspace())


def _coincide_equipo(nombre_evento: str, nombre_buscado: str) -> bool:
    a, b = _normalizar(nombre_evento), _normalizar(nombre_buscado)
    if not a or not b:
        return False
    return a in b or b in a or any(palabra in a for palabra in b.split() if len(palabra) > 3)


class OddsProvider:
    BASE = "https://api.the-odds-api.com/v4"

    def _key(self):
        if not ODDS_API_KEY:
            raise ProviderError(
                "Falta configurar ODDS_API_KEY. Regístrate gratis (500 peticiones/mes) en "
                "https://the-odds-api.com/ y define la variable de entorno."
            )
        return ODDS_API_KEY

    def listar_ligas(self, grupo: str) -> list:
        """grupo: 'Soccer', 'Basketball' o 'Tennis'. Llamada a /sports no consume cuota."""
        data = _get_con_cache(f"{self.BASE}/sports", {}, {"apiKey": self._key()}, TTL_BUSQUEDA)
        if isinstance(data, dict) and "message" in data:
            raise ProviderError(f"The Odds API respondió: {data['message']}")
        return [
            {"key": s["key"], "titulo": s["title"]}
            for s in data
            if s.get("group") == grupo and s.get("active")
        ]

    def cuotas_evento(self, sport_key: str, equipo_local: str, equipo_visita: str, mercados: str = "h2h,totals") -> dict:
        data = _get_con_cache(
            f"{self.BASE}/sports/{sport_key}/odds", {},
            {"apiKey": self._key(), "regions": "us,uk,eu", "markets": mercados, "oddsFormat": "decimal"},
            TTL_STATS,
        )
        if isinstance(data, dict) and "message" in data:
            raise ProviderError(f"The Odds API respondió: {data['message']}")

        evento = None
        for ev in data:
            if _coincide_equipo(ev.get("home_team", ""), equipo_local) and _coincide_equipo(ev.get("away_team", ""), equipo_visita):
                evento = ev
                break
        if evento is None:
            raise ProviderError(
                f"No se encontró un partido de {equipo_local} vs {equipo_visita} con cuotas abiertas "
                f"en esa liga/torneo (¿ya empezó o está muy lejos en el calendario?)."
            )

        # Mejor cuota (la más alta = mejor para quien apuesta) por resultado, entre todas las casas
        mejores = {}
        for bk in evento.get("bookmakers", []):
            for mercado in bk.get("markets", []):
                for outcome in mercado.get("outcomes", []):
                    clave = (mercado["key"], outcome.get("name"), outcome.get("point"))
                    precio = outcome.get("price")
                    if precio is None:
                        continue
                    if clave not in mejores or precio > mejores[clave]["precio"]:
                        mejores[clave] = {"precio": precio, "casa": bk.get("title")}

        try:
            _guardar_snapshot_cuotas(sport_key, evento.get("home_team"), evento.get("away_team"), mejores)
        except Exception:
            pass  # el histórico de movimiento es un "nice to have"; nunca debe tumbar la comparación principal

        return {
            "home_team": evento.get("home_team"),
            "away_team": evento.get("away_team"),
            "commence_time": evento.get("commence_time"),
            "num_casas": len(evento.get("bookmakers", [])),
            "mejores_cuotas": mejores,
        }


def _guardar_snapshot_cuotas(sport_key: str, home_team: str, away_team: str, mejores_cuotas: dict):
    """Guarda una foto de las cuotas actuales para poder graficar su movimiento con el tiempo.
    Al estar detrás de la caché de _get_con_cache, la granularidad real es de
    aproximadamente una vez cada TTL_STATS (6h) por partido, no en tiempo real."""
    _init_cache_table()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cuotas_historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL, sport_key TEXT, home_team TEXT, away_team TEXT,
            mercado TEXT, resultado TEXT, punto REAL, cuota REAL, casa TEXT
        )
    """)
    ahora = time.strftime("%Y-%m-%dT%H:%M:%S")
    for (mercado, nombre, punto), info in mejores_cuotas.items():
        conn.execute(
            "INSERT INTO cuotas_historial (fecha, sport_key, home_team, away_team, mercado, resultado, punto, cuota, casa) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ahora, sport_key, home_team, away_team, mercado, nombre, punto, info["precio"], info["casa"]),
        )
    conn.commit()
    conn.close()


def obtener_movimiento_cuotas(home_team: str, away_team: str) -> list:
    _init_cache_table()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    filas = conn.execute(
        "SELECT * FROM cuotas_historial WHERE home_team = ? AND away_team = ? ORDER BY fecha ASC",
        (home_team, away_team),
    ).fetchall()
    conn.close()
    return [dict(f) for f in filas]


def detectar_value_bets(probabilidades_modelo: dict, mejores_cuotas: dict, umbral_valor: float = 0.03) -> list:
    """
    Compara la probabilidad del modelo (0-1) contra la probabilidad implícita de cada
    cuota (1/cuota_decimal). Si el modelo cree que un resultado es más probable de lo
    que sugiere la cuota, en más de `umbral_valor`, lo marca como value bet.
    """
    resultados = []
    for (mercado, nombre, punto), info in mejores_cuotas.items():
        prob_modelo = probabilidades_modelo.get(nombre)
        if prob_modelo is None:
            continue
        precio = info["precio"]
        prob_implicita = 1 / precio if precio > 0 else 1.0
        valor = prob_modelo - prob_implicita
        resultados.append({
            "mercado": mercado,
            "resultado": nombre,
            "punto": punto,
            "cuota": precio,
            "casa": info["casa"],
            "prob_modelo": round(prob_modelo * 100, 1),
            "prob_implicita_cuota": round(prob_implicita * 100, 1),
            "valor_pct": round(valor * 100, 1),
            "es_value_bet": valor > umbral_valor,
        })
    resultados.sort(key=lambda r: r["valor_pct"], reverse=True)
    return resultados


def detectar_arbitraje(mejores_cuotas: dict) -> list:
    """
    Un arbitraje (surebet) existe cuando, tomando la MEJOR cuota de cada resultado
    posible entre distintas casas, la suma de probabilidades implícitas (1/cuota) es
    menor a 1 — repartiendo el dinero proporcionalmente se gana sin importar el
    resultado. Esto es raro y las ventanas se cierran rápido, pero cuesta casi nada
    de calcular ya que reutiliza las mismas cuotas que ya trajimos.
    """
    por_mercado = {}
    for (mercado, nombre, punto), info in mejores_cuotas.items():
        clave = (mercado, punto)
        por_mercado.setdefault(clave, []).append((nombre, info))

    oportunidades = []
    for (mercado, punto), outcomes in por_mercado.items():
        if len(outcomes) < 2:
            continue
        suma_implicita = sum(1 / info["precio"] for _, info in outcomes if info["precio"] > 0)
        if suma_implicita < 1:
            reparto = []
            for nombre, info in outcomes:
                fraccion = (1 / info["precio"]) / suma_implicita
                reparto.append({
                    "resultado": nombre, "cuota": info["precio"], "casa": info["casa"],
                    "pct_del_monto_total": round(fraccion * 100, 1),
                })
            oportunidades.append({
                "mercado": mercado,
                "punto": punto,
                "ganancia_garantizada_pct": round((1 / suma_implicita - 1) * 100, 2),
                "reparto": reparto,
            })
    return oportunidades


odds_provider = OddsProvider()
