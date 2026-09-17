import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, render_template, g

from motor import predecir
from calibracion import ajuste_empirico
from providers import (
    football_provider, basketball_provider, tennis_provider, odds_provider,
    detectar_value_bets, detectar_arbitraje, obtener_movimiento_cuotas, ProviderError,
)

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "pronosticos.db"

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analisis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            deporte TEXT NOT NULL,
            equipo_local TEXT,
            equipo_visita TEXT,
            entrada_json TEXT NOT NULL,
            resultado_json TEXT NOT NULL,
            pronostico TEXT,
            confianza REAL,
            resultado_real TEXT
        )
    """)
    # Migración suave para bases de datos creadas por una versión anterior de la app
    columnas = [r[1] for r in conn.execute("PRAGMA table_info(analisis)").fetchall()]
    if "resultado_real" not in columnas:
        conn.execute("ALTER TABLE analisis ADD COLUMN resultado_real TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS apuestas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            deporte TEXT,
            partido TEXT,
            mercado TEXT,
            seleccion TEXT,
            cuota REAL NOT NULL,
            monto REAL NOT NULL,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            ganancia REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cuotas_historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            sport_key TEXT,
            home_team TEXT,
            away_team TEXT,
            mercado TEXT,
            resultado TEXT,
            punto REAL,
            cuota REAL,
            casa TEXT
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Límite de peticiones — implementación propia en memoria (sin dependencias)
# Ventana deslizante simple por IP. Se reinicia si reinicias el proceso; para un
# despliegue real con varios workers habría que moverlo a algo compartido
# (Redis, etc.) pero para uso personal/prototipo esto es suficiente.
# ---------------------------------------------------------------------------

from collections import defaultdict
import time as _time
import threading

_peticiones_por_ip = defaultdict(list)
_lock_rate_limit = threading.Lock()
LIMITE_PETICIONES = 60          # peticiones
VENTANA_SEGUNDOS = 60           # por minuto


def _limite_excedido(ip: str) -> bool:
    ahora = _time.time()
    with _lock_rate_limit:
        historial = _peticiones_por_ip[ip]
        historial[:] = [t for t in historial if ahora - t < VENTANA_SEGUNDOS]
        if len(historial) >= LIMITE_PETICIONES:
            return True
        historial.append(ahora)
        return False


@app.before_request
def limitar_peticiones():
    if request.path.startswith("/api/") or request.path == "/analizar":
        ip = request.remote_addr or "desconocida"
        if _limite_excedido(ip):
            return jsonify({"error": "Demasiadas peticiones. Espera un minuto e intenta de nuevo."}), 429


@app.route("/")
def index():
    return render_template(
        "index.html",
        api_futbol_baloncesto_ok=bool(os.environ.get("API_SPORTS_KEY")),
        api_tenis_ok=bool(os.environ.get("RAPIDAPI_KEY")),
        api_cuotas_ok=bool(os.environ.get("ODDS_API_KEY")),
    )


@app.route("/analizar", methods=["POST"])
def analizar():
    payload = request.get_json(force=True)
    deporte = payload.get("deporte")
    datos = payload.get("datos", {})

    if deporte not in ("futbol", "baloncesto", "tenis"):
        return jsonify({"error": "Deporte no soportado"}), 400

    try:
        resultado = predecir(deporte, datos)
    except Exception as e:
        return jsonify({"error": f"Error al calcular el pronóstico: {e}"}), 400

    resultado["calibracion"] = ajuste_empirico(resultado["confianza"], deporte)

    equipo_local = datos.get("local_nombre") or datos.get("jugador_a_nombre") or "Local"
    equipo_visita = datos.get("visita_nombre") or datos.get("jugador_b_nombre") or "Visitante"

    db = get_db()
    db.execute(
        """INSERT INTO analisis (fecha, deporte, equipo_local, equipo_visita, entrada_json, resultado_json, pronostico, confianza)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now().isoformat(timespec="seconds"),
            deporte,
            equipo_local,
            equipo_visita,
            json.dumps(datos, ensure_ascii=False),
            json.dumps(resultado, ensure_ascii=False),
            resultado["pronostico_principal"],
            resultado["confianza"],
        ),
    )
    db.commit()

    return jsonify(resultado)


@app.route("/historial")
def historial():
    db = get_db()
    filas = db.execute(
        "SELECT * FROM analisis ORDER BY id DESC LIMIT 100"
    ).fetchall()
    items = [dict(f) for f in filas]
    for it in items:
        it["resultado"] = json.loads(it["resultado_json"])
    return render_template("historial.html", items=items)


@app.route("/api/historial")
def api_historial():
    db = get_db()
    filas = db.execute("SELECT * FROM analisis ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify([dict(f) for f in filas])


@app.route("/api/buscar_equipo")
def api_buscar_equipo():
    deporte = request.args.get("deporte", "")
    q = request.args.get("q", "")
    if not q or len(q) < 2:
        return jsonify({"resultados": []})
    try:
        if deporte == "futbol":
            resultados = football_provider.buscar_equipo(q)
        elif deporte == "baloncesto":
            resultados = basketball_provider.buscar_equipo(q)
        else:
            return jsonify({"error": "Deporte no soportado para búsqueda de equipos"}), 400
        return jsonify({"resultados": resultados})
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/autocompletar_equipo", methods=["POST"])
def api_autocompletar_equipo():
    payload = request.get_json(force=True)
    deporte = payload.get("deporte")
    team_id = payload.get("team_id")
    rol = payload.get("rol")  # 'local' o 'visita'
    incluir_corners = payload.get("incluir_corners_tiros", False)

    try:
        if deporte == "futbol":
            campos = football_provider.stats_equipo(team_id, rol)
            if incluir_corners:
                campos.update(football_provider.stats_corners_tiros(team_id, rol))
        elif deporte == "baloncesto":
            campos = basketball_provider.stats_equipo(team_id, rol)
        else:
            return jsonify({"error": "Deporte no soportado"}), 400
        return jsonify({"campos": campos})
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/buscar_jugador")
def api_buscar_jugador():
    q = request.args.get("q", "")
    team_id = request.args.get("team_id")
    season = request.args.get("season")
    if not q or len(q) < 2 or not team_id or not season:
        return jsonify({"resultados": []})
    try:
        resultados = football_provider.buscar_jugador(q, int(team_id), int(season))
        return jsonify({"resultados": resultados})
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/autocompletar_jugador", methods=["POST"])
def api_autocompletar_jugador():
    payload = request.get_json(force=True)
    try:
        campos = football_provider.stats_jugador(
            payload.get("player_id"), payload.get("team_id"), payload.get("season")
        )
        return jsonify({"campos": campos})
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/autocompletar_tenis", methods=["POST"])
def api_autocompletar_tenis():
    payload = request.get_json(force=True)
    nombre = payload.get("nombre", "")
    if not nombre:
        return jsonify({"error": "Falta el nombre del jugador"}), 400
    try:
        campos = tennis_provider.stats_jugador(nombre)
        return jsonify({"campos": campos})
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/listar_ligas_cuotas")
def api_listar_ligas_cuotas():
    deporte = request.args.get("deporte", "")
    grupo = {"futbol": "Soccer", "baloncesto": "Basketball", "tenis": "Tennis"}.get(deporte)
    if not grupo:
        return jsonify({"error": "Deporte no soportado"}), 400
    try:
        return jsonify({"ligas": odds_provider.listar_ligas(grupo)})
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/comparar_cuotas", methods=["POST"])
def api_comparar_cuotas():
    payload = request.get_json(force=True)
    sport_key = payload.get("sport_key")
    equipo_local = payload.get("equipo_local", "")
    equipo_visita = payload.get("equipo_visita", "")
    probabilidades = payload.get("probabilidades", {})  # claves genéricas: local/empate/visita/over/under

    try:
        cuotas = odds_provider.cuotas_evento(sport_key, equipo_local, equipo_visita)

        # Remapeamos de claves genéricas a los nombres reales que usa la casa de apuestas
        # (evento['home_team'] / evento['away_team']), en vez de exigir que el usuario haya
        # escrito el nombre exactamente igual al oficial.
        probabilidades_reales = {}
        if "local" in probabilidades:
            probabilidades_reales[cuotas["home_team"]] = probabilidades["local"]
        if "empate" in probabilidades:
            probabilidades_reales["Draw"] = probabilidades["empate"]
        if "visita" in probabilidades:
            probabilidades_reales[cuotas["away_team"]] = probabilidades["visita"]
        if "over" in probabilidades:
            probabilidades_reales["Over"] = probabilidades["over"]
        if "under" in probabilidades:
            probabilidades_reales["Under"] = probabilidades["under"]

        value_bets = detectar_value_bets(probabilidades_reales, cuotas["mejores_cuotas"])
        arbitraje = detectar_arbitraje(cuotas["mejores_cuotas"])
        return jsonify({
            "evento": {
                "home_team": cuotas["home_team"],
                "away_team": cuotas["away_team"],
                "commence_time": cuotas["commence_time"],
                "num_casas": cuotas["num_casas"],
            },
            "comparacion": value_bets,
            "arbitraje": arbitraje,
        })
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/movimiento_cuotas")
def api_movimiento_cuotas():
    home = request.args.get("home", "")
    away = request.args.get("away", "")
    if not home or not away:
        return jsonify({"error": "Faltan equipos"}), 400
    filas = obtener_movimiento_cuotas(home, away)
    return jsonify({"movimiento": filas})


@app.route("/api/prediccion_oficial", methods=["POST"])
def api_prediccion_oficial():
    payload = request.get_json(force=True)
    try:
        campos = football_provider.prediccion_oficial(payload.get("team_a_id"), payload.get("team_b_id"))
        return jsonify({"prediccion": campos})
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/lesionados")
def api_lesionados():
    team_id = request.args.get("team_id")
    season = request.args.get("season")
    if not team_id or not season:
        return jsonify({"error": "Faltan team_id/season"}), 400
    try:
        resultados = football_provider.lesionados(int(team_id), int(season))
        return jsonify({"lesionados": resultados})
    except ProviderError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/historial/<int:analisis_id>/marcar", methods=["POST"])
def marcar_resultado(analisis_id):
    payload = request.get_json(force=True)
    valor = payload.get("resultado_real")
    if valor not in ("acierto", "fallo", None):
        return jsonify({"error": "Valor inválido"}), 400
    db = get_db()
    db.execute("UPDATE analisis SET resultado_real = ? WHERE id = ?", (valor, analisis_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/calibracion")
def calibracion():
    db = get_db()
    filas = db.execute(
        "SELECT confianza, resultado_real FROM analisis WHERE resultado_real IS NOT NULL"
    ).fetchall()
    total_marcados = db.execute(
        "SELECT COUNT(*) c FROM analisis"
    ).fetchone()["c"]

    buckets_def = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    buckets = []
    for lo, hi in buckets_def:
        en_bucket = [f for f in filas if lo <= f["confianza"] < hi]
        aciertos = sum(1 for f in en_bucket if f["resultado_real"] == "acierto")
        total = len(en_bucket)
        pct_acierto = round(aciertos / total * 100, 1) if total else None
        buckets.append({
            "rango": f"{lo}–{hi if hi <= 100 else 100}%",
            "total": total,
            "aciertos": aciertos,
            "pct_acierto": pct_acierto,
        })

    return render_template(
        "calibracion.html",
        buckets=buckets,
        total_marcados=len(filas),
        total_analisis=total_marcados,
    )


@app.route("/historial/exportar.csv")
def exportar_csv():
    import csv
    import io

    db = get_db()
    filas = db.execute("SELECT * FROM analisis ORDER BY id DESC").fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "fecha", "deporte", "equipo_local", "equipo_visita", "pronostico", "confianza", "resultado_real"])
    for f in filas:
        writer.writerow([f["id"], f["fecha"], f["deporte"], f["equipo_local"], f["equipo_visita"], f["pronostico"], f["confianza"], f["resultado_real"] or ""])

    from flask import Response
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=historial_pronosticos.csv"},
    )


@app.route("/apuestas")
def apuestas():
    db = get_db()
    filas = [dict(f) for f in db.execute("SELECT * FROM apuestas ORDER BY id DESC").fetchall()]

    resueltas = [f for f in filas if f["estado"] != "pendiente"]
    total_apostado = sum(f["monto"] for f in resueltas)
    ganancia_neta = sum((f["ganancia"] or 0) for f in resueltas)
    roi = round(ganancia_neta / total_apostado * 100, 1) if total_apostado > 0 else None

    return render_template(
        "apuestas.html",
        apuestas=filas,
        total_apostado=round(total_apostado, 2),
        ganancia_neta=round(ganancia_neta, 2),
        roi=roi,
        pendientes=len([f for f in filas if f["estado"] == "pendiente"]),
    )


@app.route("/api/apuestas", methods=["POST"])
def api_registrar_apuesta():
    payload = request.get_json(force=True)
    try:
        monto = float(payload.get("monto"))
        cuota = float(payload.get("cuota"))
    except (TypeError, ValueError):
        return jsonify({"error": "Monto y cuota deben ser numéricos"}), 400
    if monto <= 0 or cuota <= 1:
        return jsonify({"error": "Monto debe ser > 0 y cuota > 1"}), 400

    db = get_db()
    db.execute(
        """INSERT INTO apuestas (fecha, deporte, partido, mercado, seleccion, cuota, monto, estado)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pendiente')""",
        (
            datetime.now().isoformat(timespec="seconds"),
            payload.get("deporte", ""),
            payload.get("partido", ""),
            payload.get("mercado", ""),
            payload.get("seleccion", ""),
            cuota,
            monto,
        ),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/apuestas/<int:apuesta_id>/resolver", methods=["POST"])
def api_resolver_apuesta(apuesta_id):
    payload = request.get_json(force=True)
    estado = payload.get("estado")
    if estado not in ("ganada", "perdida"):
        return jsonify({"error": "Estado inválido"}), 400

    db = get_db()
    fila = db.execute("SELECT * FROM apuestas WHERE id = ?", (apuesta_id,)).fetchone()
    if fila is None:
        return jsonify({"error": "No existe esa apuesta"}), 404

    ganancia = (fila["monto"] * fila["cuota"] - fila["monto"]) if estado == "ganada" else -fila["monto"]
    db.execute("UPDATE apuestas SET estado = ?, ganancia = ? WHERE id = ?", (estado, ganancia, apuesta_id))
    db.commit()
    return jsonify({"ok": True, "ganancia": round(ganancia, 2)})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
