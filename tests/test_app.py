"""
Suite de tests. Correr con:  pytest -v
No necesita ninguna API key ni conexión a internet: todo lo que llama a una API
externa se prueba con datos simulados (unittest.mock), igual que se validó a
mano durante el desarrollo de esta app.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from motor import predecir
from providers import detectar_value_bets, detectar_arbitraje


# --------------------------------------------------------------------------
# Motor de predicción
# --------------------------------------------------------------------------

def test_futbol_probabilidades_suman_cien():
    r = predecir("futbol", {"local_nombre": "A", "visita_nombre": "B"})
    m = r["mercados"]["1x2"]
    assert abs((m["gana_local"] + m["empate"] + m["gana_visita"]) - 100) < 0.5


def test_futbol_favorito_claro_da_mas_probabilidad_al_local():
    datos = {
        "local_nombre": "Favorito", "visita_nombre": "Débil",
        "local_goles_favor_local": 3.0, "local_goles_contra_local": 0.3,
        "visita_goles_favor_visita": 0.4, "visita_goles_contra_visita": 2.5,
    }
    r = predecir("futbol", datos)
    assert r["mercados"]["1x2"]["gana_local"] > r["mercados"]["1x2"]["gana_visita"]
    assert r["pronostico_principal"] == "Gana local"


def test_baloncesto_ventaja_localia_favorece_al_local_con_equipos_parejos():
    r = predecir("baloncesto", {"local_nombre": "A", "visita_nombre": "B"})
    assert r["mercados"]["moneyline"]["gana_local"] > 50


def test_tenis_favorito_por_ranking_gana_mas_probabilidad():
    datos = {
        "jugador_a_nombre": "Top10", "jugador_b_nombre": "Top200",
        "jugador_a_ranking_pts": 6000, "jugador_b_ranking_pts": 800,
    }
    r = predecir("tenis", datos)
    assert r["mercados"]["ganador_partido"]["Top10"] > r["mercados"]["ganador_partido"]["Top200"]


def test_confianza_siempre_entre_5_y_97():
    for deporte, datos in [
        ("futbol", {"local_nombre": "A", "visita_nombre": "B"}),
        ("baloncesto", {"local_nombre": "A", "visita_nombre": "B"}),
        ("tenis", {"jugador_a_nombre": "A", "jugador_b_nombre": "B"}),
    ]:
        r = predecir(deporte, datos)
        assert 5 <= r["confianza"] <= 97


def test_jugadores_de_futbol_se_incluyen_cuando_se_dan_datos():
    datos = {
        "local_nombre": "A", "visita_nombre": "B",
        "local_j1_nombre": "Killer", "local_j1_goles": 0.6, "local_j1_tiros": 2.0,
        "local_j1_asistencias": 0.1, "local_j1_minutos": 90,
    }
    r = predecir("futbol", datos)
    assert len(r["jugadores"]["local"]) == 1
    assert r["jugadores"]["local"][0]["nombre"] == "Killer"
    assert 0 <= r["jugadores"]["local"][0]["prob_anota_1_mas"] <= 100


# --------------------------------------------------------------------------
# Value bets y arbitraje (con cuotas simuladas, forma real de The Odds API)
# --------------------------------------------------------------------------

def test_value_bet_se_marca_solo_si_el_edge_supera_el_umbral():
    cuotas = {("h2h", "A", None): {"precio": 2.5, "casa": "X"}}
    # Modelo cree 55% -> implícita de 2.5 es 40% -> edge de 15 pts, debe marcar value
    comp = detectar_value_bets({"A": 0.55}, cuotas)
    assert comp[0]["es_value_bet"] is True

    # Modelo cree 41% -> edge de solo 1pt, NO debe marcar value (umbral 3pts)
    comp2 = detectar_value_bets({"A": 0.41}, cuotas)
    assert comp2[0]["es_value_bet"] is False


def test_arbitraje_detecta_cuando_suma_implicita_menor_a_uno():
    cuotas = {
        ("h2h", "A", None): {"precio": 2.2, "casa": "X"},
        ("h2h", "B", None): {"precio": 2.2, "casa": "Y"},
    }
    arb = detectar_arbitraje(cuotas)
    assert len(arb) == 1
    assert arb[0]["ganancia_garantizada_pct"] > 0


def test_sin_arbitraje_cuando_la_casa_tiene_margen_normal():
    cuotas = {
        ("h2h", "A", None): {"precio": 1.8, "casa": "X"},
        ("h2h", "B", None): {"precio": 1.8, "casa": "Y"},
    }
    assert detectar_arbitraje(cuotas) == []


# --------------------------------------------------------------------------
# Rutas Flask (sin tocar red real — providers sin API key deben fallar limpio)
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("providers.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("calibracion.DB_PATH", tmp_path / "test.db")
    import app as appmodule
    appmodule.init_db()
    appmodule.app.config["TESTING"] = True
    with appmodule.app.test_client() as c:
        yield c


def test_home_y_paginas_secundarias_responden_200(client):
    for ruta in ["/", "/historial", "/calibracion", "/apuestas"]:
        assert client.get(ruta).status_code == 200


def test_analizar_futbol_end_to_end(client):
    r = client.post("/analizar", json={"deporte": "futbol", "datos": {"local_nombre": "A", "visita_nombre": "B"}})
    assert r.status_code == 200
    assert "calibracion" in r.get_json()


def test_analizar_deporte_invalido_da_400(client):
    r = client.post("/analizar", json={"deporte": "ajedrez", "datos": {}})
    assert r.status_code == 400


def test_registrar_y_resolver_apuesta(client):
    r = client.post("/api/apuestas", json={
        "deporte": "futbol", "partido": "A vs B", "mercado": "h2h",
        "seleccion": "A", "cuota": 2.0, "monto": 10,
    })
    assert r.status_code == 200

    r = client.post("/api/apuestas/1/resolver", json={"estado": "ganada"})
    assert r.status_code == 200
    assert r.get_json()["ganancia"] == 10.0


def test_buscar_equipo_sin_api_key_da_error_controlado(client, monkeypatch):
    monkeypatch.setattr("providers.API_SPORTS_KEY", "")
    r = client.get("/api/buscar_equipo?deporte=futbol&q=river")
    assert r.status_code == 400
    assert "API_SPORTS_KEY" in r.get_json()["error"]


def test_rate_limit_devuelve_429_al_pasarse(client, monkeypatch):
    import app as appmodule
    monkeypatch.setattr(appmodule, "LIMITE_PETICIONES", 3)
    codigos = [client.get("/api/historial").status_code for _ in range(6)]
    assert 429 in codigos
