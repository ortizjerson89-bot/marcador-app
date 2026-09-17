"""
calibracion.py
Compara la confianza que reporta el motor contra qué tan seguido acierta
realmente, usando los análisis que el usuario marcó en /historial.

Esto NO reentrena los pesos internos del modelo (para eso se necesitaría un
dataset histórico mucho más grande que las marcas manuales de un usuario).
Lo que sí hace: si hay suficientes partidos marcados en un rango de confianza
parecido, muestra junto al % del modelo un % "ajustado" basado en tu propio
historial de aciertos — una capa honesta de retroalimentación, no una promesa
de que el modelo se corrige solo.
"""

import sqlite3
from pathlib import Path

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "pronosticos.db"

MINIMO_MUESTRAS = 8   # por debajo de esto, no mostramos ajuste (ruido estadístico)
ANCHO_VENTANA = 15    # +/- puntos porcentuales alrededor de la confianza a evaluar


def ajuste_empirico(confianza_cruda: float, deporte: str = None) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    query = "SELECT confianza, resultado_real, deporte FROM analisis WHERE resultado_real IS NOT NULL"
    filas = conn.execute(query).fetchall()
    conn.close()

    lo, hi = confianza_cruda - ANCHO_VENTANA, confianza_cruda + ANCHO_VENTANA
    en_ventana = [
        f for f in filas
        if lo <= f["confianza"] <= hi and (deporte is None or f["deporte"] == deporte)
    ]

    if len(en_ventana) < MINIMO_MUESTRAS:
        return {
            "disponible": False,
            "muestras": len(en_ventana),
            "muestras_necesarias": MINIMO_MUESTRAS,
        }

    aciertos = sum(1 for f in en_ventana if f["resultado_real"] == "acierto")
    pct_real = round(aciertos / len(en_ventana) * 100, 1)

    return {
        "disponible": True,
        "muestras": len(en_ventana),
        "confianza_ajustada": pct_real,
        "diferencia": round(pct_real - confianza_cruda, 1),
    }
