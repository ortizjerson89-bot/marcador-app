"""
motor.py
Motor de análisis y pronóstico deportivo.

Contiene los modelos estadísticos:
- Fútbol: distribución de Poisson sobre fuerzas de ataque/defensa (modelo estilo Dixon-Coles simplificado)
- Baloncesto: modelo normal sobre diferencial de puntos esperado
- Tenis: modelo logístico tipo Elo combinado con % de victorias y H2H

Todas las funciones devuelven un diccionario JSON-serializable con:
- probabilidades de cada mercado
- pronóstico principal
- porcentaje de confianza (0-100)
- desglose de "por qué" (factores que more pesaron)
"""

import math
from itertools import product

# ---------------------------------------------------------------------------
# Utilidades generales
# ---------------------------------------------------------------------------

def poisson_pmf(k: int, lam: float) -> float:
    """Probabilidad de que ocurran exactamente k eventos con media lam (Poisson)."""
    if lam <= 0:
        lam = 0.01
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def normal_cdf(x: float) -> float:
    """Función de distribución acumulada de la normal estándar."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def clamp(value, low, high):
    return max(low, min(high, value))


def confianza_por_margen(probs: list, completitud: float) -> float:
    """
    Calcula un % de confianza combinando:
    - el margen entre la probabilidad más alta y la segunda más alta
      (mientras más separadas, más "clara" es la predicción)
    - la completitud de los datos suministrados por el usuario (0-1)
    """
    ordenadas = sorted(probs, reverse=True)
    margen = ordenadas[0] - (ordenadas[1] if len(ordenadas) > 1 else 0)
    base = ordenadas[0] * 100  # qué tan alta es la probabilidad ganadora en sí
    bonus_margen = margen * 60  # separación entre 1º y 2º lugar
    score = (base * 0.55) + (bonus_margen * 0.45)
    score = score * (0.55 + 0.45 * completitud)  # penaliza datos incompletos
    return round(clamp(score, 5, 97), 1)


# ---------------------------------------------------------------------------
# FÚTBOL — modelo de Poisson con fuerzas de ataque/defensa
# ---------------------------------------------------------------------------

def predict_futbol(datos: dict) -> dict:
    liga_prom_local = float(datos.get("liga_prom_goles_local", 1.5)) or 1.5
    liga_prom_visita = float(datos.get("liga_prom_goles_visita", 1.15)) or 1.15

    gf_local_local = float(datos.get("local_goles_favor_local", 1.5))   # goles a favor del local, jugando de local
    gc_local_local = float(datos.get("local_goles_contra_local", 1.1))  # goles en contra del local, de local
    gf_visita_visita = float(datos.get("visita_goles_favor_visita", 1.1))
    gc_visita_visita = float(datos.get("visita_goles_contra_visita", 1.4))

    corners_favor_local = float(datos.get("local_corners_favor", 5.5))
    corners_contra_local = float(datos.get("local_corners_contra", 4.5))
    corners_favor_visita = float(datos.get("visita_corners_favor", 4.5))
    corners_contra_visita = float(datos.get("visita_corners_contra", 5.5))

    tiros_favor_local = float(datos.get("local_tiros_favor", 12.0))
    tiros_favor_visita = float(datos.get("visita_tiros_favor", 10.0))

    forma_local = float(datos.get("local_forma", 3)) / 5.0   # 0-5 -> 0-1 (puntos últimos 5 partidos /15 normalizado a /5)
    forma_visita = float(datos.get("visita_forma", 3)) / 5.0

    h2h_local_wins = float(datos.get("h2h_local_wins", 0))
    h2h_visita_wins = float(datos.get("h2h_visita_wins", 0))
    h2h_empates = float(datos.get("h2h_empates", 0))

    # Fuerzas relativas al promedio de la liga
    fuerza_ataque_local = gf_local_local / liga_prom_local
    fuerza_defensa_local = gc_local_local / liga_prom_visita
    fuerza_ataque_visita = gf_visita_visita / liga_prom_visita
    fuerza_defensa_visita = gc_visita_visita / liga_prom_local

    lam_local = fuerza_ataque_local * fuerza_defensa_visita * liga_prom_local
    lam_visita = fuerza_ataque_visita * fuerza_defensa_local * liga_prom_visita

    # Ajuste leve por forma reciente (+-15% máx)
    lam_local *= (0.85 + 0.30 * forma_local)
    lam_visita *= (0.85 + 0.30 * forma_visita)

    lam_local = clamp(lam_local, 0.1, 5.0)
    lam_visita = clamp(lam_visita, 0.1, 5.0)

    MAX_GOLES = 8
    matriz = [[poisson_pmf(i, lam_local) * poisson_pmf(j, lam_visita)
               for j in range(MAX_GOLES + 1)] for i in range(MAX_GOLES + 1)]

    p_local, p_empate, p_visita = 0.0, 0.0, 0.0
    p_over25, p_btts = 0.0, 0.0
    marcador_probable, p_marcador = (0, 0), 0.0

    for i, j in product(range(MAX_GOLES + 1), repeat=2):
        p = matriz[i][j]
        if i > j:
            p_local += p
        elif i == j:
            p_empate += p
        else:
            p_visita += p
        if i + j >= 3:
            p_over25 += p
        if i >= 1 and j >= 1:
            p_btts += p
        if p > p_marcador:
            p_marcador = p
            marcador_probable = (i, j)

    # Ajuste leve por historial directo (H2H)
    total_h2h = h2h_local_wins + h2h_visita_wins + h2h_empates
    if total_h2h > 0:
        peso_h2h = clamp(total_h2h / 10.0, 0, 0.25)  # máx 25% de influencia
        h2h_local_pct = h2h_local_wins / total_h2h
        h2h_visita_pct = h2h_visita_wins / total_h2h
        h2h_empate_pct = h2h_empates / total_h2h
        p_local = p_local * (1 - peso_h2h) + h2h_local_pct * peso_h2h
        p_visita = p_visita * (1 - peso_h2h) + h2h_visita_pct * peso_h2h
        p_empate = p_empate * (1 - peso_h2h) + h2h_empate_pct * peso_h2h

    # Normalizar por si el ajuste H2H descuadró la suma
    suma = p_local + p_empate + p_visita
    p_local, p_empate, p_visita = p_local / suma, p_empate / suma, p_visita / suma

    # Corners estimados (regresión simple por promedio de fuerzas ofensivas/defensivas)
    corners_local_esperados = (corners_favor_local + corners_contra_visita) / 2
    corners_visita_esperados = (corners_favor_visita + corners_contra_local) / 2
    total_corners = corners_local_esperados + corners_visita_esperados
    # Prob de over 9.5 corners vía aproximación normal (std ~ 3.2 típico en fútbol)
    std_corners = 3.2
    p_over_corners_95 = 1 - normal_cdf((9.5 - total_corners) / std_corners)

    total_tiros = tiros_favor_local + tiros_favor_visita

    mercados = {
        "1x2": {
            "gana_local": round(p_local * 100, 1),
            "empate": round(p_empate * 100, 1),
            "gana_visita": round(p_visita * 100, 1),
        },
        "goles": {
            "marcador_mas_probable": f"{marcador_probable[0]}-{marcador_probable[1]}",
            "prob_marcador_exacto": round(p_marcador * 100, 1),
            "goles_esperados_local": round(lam_local, 2),
            "goles_esperados_visita": round(lam_visita, 2),
            "total_goles_esperados": round(lam_local + lam_visita, 2),
            "over_2_5": round(p_over25 * 100, 1),
            "under_2_5": round((1 - p_over25) * 100, 1),
            "ambos_anotan_si": round(p_btts * 100, 1),
            "ambos_anotan_no": round((1 - p_btts) * 100, 1),
        },
        "corners": {
            "corners_esperados_local": round(corners_local_esperados, 1),
            "corners_esperados_visita": round(corners_visita_esperados, 1),
            "total_corners_esperados": round(total_corners, 1),
            "over_9_5": round(p_over_corners_95 * 100, 1),
            "under_9_5": round((1 - p_over_corners_95) * 100, 1),
        },
        "tiros": {
            "total_tiros_esperados": round(total_tiros, 1),
        },
    }

    resultado_principal = max(
        [("Gana local", p_local), ("Empate", p_empate), ("Gana visitante", p_visita)],
        key=lambda x: x[1],
    )

    campos_clave = [gf_local_local, gc_local_local, gf_visita_visita, gc_visita_visita,
                     corners_favor_local, corners_favor_visita, tiros_favor_local, tiros_favor_visita]
    completitud = sum(1 for v in campos_clave if v not in (0, None)) / len(campos_clave)

    confianza = confianza_por_margen([p_local, p_empate, p_visita], completitud)

    jugadores_local = analizar_jugadores("local", datos, lam_local, gf_local_local)
    jugadores_visita = analizar_jugadores("visita", datos, lam_visita, gf_visita_visita)

    return {
        "deporte": "futbol",
        "pronostico_principal": resultado_principal[0],
        "probabilidad_pronostico": round(resultado_principal[1] * 100, 1),
        "confianza": confianza,
        "mercados": mercados,
        "jugadores": {
            "local": jugadores_local,
            "visita": jugadores_visita,
        },
        "factores": [
            f"Fuerza de ataque local relativa a la liga: {round(fuerza_ataque_local, 2)}",
            f"Fuerza de ataque visitante relativa a la liga: {round(fuerza_ataque_visita, 2)}",
            f"Forma reciente — local: {round(forma_local * 5, 1)}/5, visitante: {round(forma_visita * 5, 1)}/5",
            f"Historial directo considerado: {int(total_h2h)} partidos" if total_h2h > 0 else "Sin historial directo suministrado",
        ],
    }


# ---------------------------------------------------------------------------
# BALONCESTO — modelo normal sobre diferencial de puntos
# ---------------------------------------------------------------------------

def predict_baloncesto(datos: dict) -> dict:
    pts_favor_local = float(datos.get("local_pts_favor", 105))
    pts_contra_local = float(datos.get("local_pts_contra", 100))
    pts_favor_visita = float(datos.get("visita_pts_favor", 102))
    pts_contra_visita = float(datos.get("visita_pts_contra", 104))

    ritmo_local = float(datos.get("local_ritmo", 98))   # posesiones por partido (pace)
    ritmo_visita = float(datos.get("visita_ritmo", 98))

    forma_local = float(datos.get("local_forma", 3)) / 5.0
    forma_visita = float(datos.get("visita_forma", 3)) / 5.0

    ventaja_local_pts = float(datos.get("ventaja_localia", 2.5))  # ventaja histórica de local, en puntos

    pts_esperados_local = (pts_favor_local + pts_contra_visita) / 2 + ventaja_local_pts / 2
    pts_esperados_visita = (pts_favor_visita + pts_contra_local) / 2 - ventaja_local_pts / 2

    # Ajuste por ritmo de juego combinado
    ritmo_prom = (ritmo_local + ritmo_visita) / 2
    factor_ritmo = ritmo_prom / 98.0
    pts_esperados_local *= factor_ritmo
    pts_esperados_visita *= factor_ritmo

    # Ajuste leve por forma
    pts_esperados_local *= (0.94 + 0.12 * forma_local)
    pts_esperados_visita *= (0.94 + 0.12 * forma_visita)

    diferencial = pts_esperados_local - pts_esperados_visita
    std_diferencial = 11.5  # desviación estándar típica del margen en baloncesto profesional

    prob_gana_local = 1 - normal_cdf((0 - diferencial) / std_diferencial)
    prob_gana_visita = 1 - prob_gana_local

    total_puntos = pts_esperados_local + pts_esperados_visita
    linea_total = float(datos.get("linea_total_puntos", round(total_puntos)))
    std_total = 13.0
    prob_over = 1 - normal_cdf((linea_total - total_puntos) / std_total)

    campos_clave = [pts_favor_local, pts_contra_local, pts_favor_visita, pts_contra_visita, ritmo_local, ritmo_visita]
    completitud = sum(1 for v in campos_clave if v not in (0, None)) / len(campos_clave)
    confianza = confianza_por_margen([prob_gana_local, prob_gana_visita], completitud)

    ganador = "Gana local" if prob_gana_local > prob_gana_visita else "Gana visitante"

    return {
        "deporte": "baloncesto",
        "pronostico_principal": ganador,
        "probabilidad_pronostico": round(max(prob_gana_local, prob_gana_visita) * 100, 1),
        "confianza": confianza,
        "mercados": {
            "moneyline": {
                "gana_local": round(prob_gana_local * 100, 1),
                "gana_visita": round(prob_gana_visita * 100, 1),
            },
            "puntos": {
                "puntos_esperados_local": round(pts_esperados_local, 1),
                "puntos_esperados_visita": round(pts_esperados_visita, 1),
                "total_esperado": round(total_puntos, 1),
                "diferencial_esperado": round(diferencial, 1),
                "linea_total_evaluada": linea_total,
                "over": round(prob_over * 100, 1),
                "under": round((1 - prob_over) * 100, 1),
            },
        },
        "factores": [
            f"Ritmo combinado estimado: {round(ritmo_prom, 1)} posesiones/partido",
            f"Ventaja de localía aplicada: {ventaja_local_pts} pts",
            f"Forma reciente — local: {round(forma_local*5,1)}/5, visitante: {round(forma_visita*5,1)}/5",
        ],
    }


# ---------------------------------------------------------------------------
# TENIS — modelo logístico tipo Elo + % de victorias + H2H
# ---------------------------------------------------------------------------

def predict_tenis(datos: dict) -> dict:
    ranking_a = float(datos.get("jugador_a_ranking_pts", 3000))
    ranking_b = float(datos.get("jugador_b_ranking_pts", 3000))

    winrate_a = float(datos.get("jugador_a_winrate", 60)) / 100
    winrate_b = float(datos.get("jugador_b_winrate", 60)) / 100

    winrate_superficie_a = float(datos.get("jugador_a_winrate_superficie", 60)) / 100
    winrate_superficie_b = float(datos.get("jugador_b_winrate_superficie", 60)) / 100

    forma_a = float(datos.get("jugador_a_forma", 3)) / 5.0
    forma_b = float(datos.get("jugador_b_forma", 3)) / 5.0

    h2h_a = float(datos.get("h2h_jugador_a", 0))
    h2h_b = float(datos.get("h2h_jugador_b", 0))

    # 1) Probabilidad tipo Elo a partir de puntos de ranking
    diff_ranking = ranking_a - ranking_b
    prob_elo_a = 1 / (1 + 10 ** (-diff_ranking / 2000))

    # 2) Probabilidad por % de victorias general y en superficie (60/40 superficie/general)
    wr_a_comb = 0.4 * winrate_a + 0.6 * winrate_superficie_a
    wr_b_comb = 0.4 * winrate_b + 0.6 * winrate_superficie_b
    prob_wr_a = wr_a_comb / (wr_a_comb + wr_b_comb) if (wr_a_comb + wr_b_comb) > 0 else 0.5

    # 3) Ajuste por forma reciente
    prob_forma_a = forma_a / (forma_a + forma_b) if (forma_a + forma_b) > 0 else 0.5

    # Combinación ponderada de los 3 métodos
    prob_a = 0.40 * prob_elo_a + 0.40 * prob_wr_a + 0.20 * prob_forma_a

    # Ajuste por historial directo
    total_h2h = h2h_a + h2h_b
    if total_h2h > 0:
        peso_h2h = clamp(total_h2h / 8.0, 0, 0.3)
        h2h_pct_a = h2h_a / total_h2h
        prob_a = prob_a * (1 - peso_h2h) + h2h_pct_a * peso_h2h

    prob_a = clamp(prob_a, 0.02, 0.98)
    prob_b = 1 - prob_a

    campos_clave = [ranking_a, ranking_b, winrate_a, winrate_b, winrate_superficie_a, winrate_superficie_b]
    completitud = sum(1 for v in campos_clave if v not in (0, None)) / len(campos_clave)
    confianza = confianza_por_margen([prob_a, prob_b], completitud)

    nombre_a = datos.get("jugador_a_nombre", "Jugador A")
    nombre_b = datos.get("jugador_b_nombre", "Jugador B")
    ganador = nombre_a if prob_a > prob_b else nombre_b

    return {
        "deporte": "tenis",
        "pronostico_principal": f"Gana {ganador}",
        "probabilidad_pronostico": round(max(prob_a, prob_b) * 100, 1),
        "confianza": confianza,
        "mercados": {
            "ganador_partido": {
                nombre_a: round(prob_a * 100, 1),
                nombre_b: round(prob_b * 100, 1),
            },
            "desglose_modelo": {
                "prob_por_ranking_elo": round(prob_elo_a * 100, 1),
                "prob_por_winrate": round(prob_wr_a * 100, 1),
                "prob_por_forma": round(prob_forma_a * 100, 1),
            },
        },
        "factores": [
            f"Diferencia de puntos de ranking: {int(diff_ranking)}",
            f"% victorias en esta superficie — {nombre_a}: {round(winrate_superficie_a*100,1)}%, {nombre_b}: {round(winrate_superficie_b*100,1)}%",
            f"Historial directo considerado: {int(total_h2h)} partidos" if total_h2h > 0 else "Sin historial directo suministrado",
        ],
    }


def analizar_jugadores(prefijo: str, datos: dict, lambda_equipo_partido: float, gf_equipo_hist: float) -> list:
    """
    Analiza hasta 3 jugadores de un equipo (prefijo = 'local' o 'visita').
    Escala el rendimiento histórico del jugador según qué tan favorecido está
    su equipo en ESTE partido puntual (lambda_equipo_partido / gf_equipo_hist),
    en vez de tomar su promedio suelto sin contexto de rival.
    """
    if gf_equipo_hist <= 0:
        gf_equipo_hist = 0.01
    factor_matchup = clamp(lambda_equipo_partido / gf_equipo_hist, 0.4, 2.2)

    jugadores = []
    for i in (1, 2, 3):
        nombre = datos.get(f"{prefijo}_j{i}_nombre", "").strip()
        if not nombre:
            continue
        goles_prom = float(datos.get(f"{prefijo}_j{i}_goles", 0) or 0)
        tiros_prom = float(datos.get(f"{prefijo}_j{i}_tiros", 0) or 0)
        asist_prom = float(datos.get(f"{prefijo}_j{i}_asistencias", 0) or 0)
        min_prom = float(datos.get(f"{prefijo}_j{i}_minutos", 90) or 90)

        factor_minutos = clamp(min_prom / 90.0, 0.3, 1.0)

        lam_goles = clamp(goles_prom * factor_matchup * factor_minutos, 0.01, 4.0)
        lam_tiros = max(tiros_prom * factor_matchup * factor_minutos, 0.01)
        lam_asist = clamp(asist_prom * factor_matchup * factor_minutos, 0.01, 3.0)

        p_marca_1_mas = 1 - poisson_pmf(0, lam_goles)
        p_marca_2_mas = p_marca_1_mas - poisson_pmf(1, lam_goles)
        p_asiste_1_mas = 1 - poisson_pmf(0, lam_asist)

        jugadores.append({
            "nombre": nombre,
            "goles_esperados": round(lam_goles, 2),
            "prob_anota_1_mas": round(clamp(p_marca_1_mas, 0, 1) * 100, 1),
            "prob_anota_2_mas": round(clamp(p_marca_2_mas, 0, 1) * 100, 1),
            "tiros_esperados": round(lam_tiros, 1),
            "asistencias_esperadas": round(lam_asist, 2),
            "prob_asiste_1_mas": round(clamp(p_asiste_1_mas, 0, 1) * 100, 1),
            "factor_matchup_aplicado": round(factor_matchup, 2),
        })
    return jugadores


def predecir(deporte: str, datos: dict) -> dict:
    if deporte == "futbol":
        return predict_futbol(datos)
    elif deporte == "baloncesto":
        return predict_baloncesto(datos)
    elif deporte == "tenis":
        return predict_tenis(datos)
    else:
        raise ValueError(f"Deporte no soportado: {deporte}")
