# Marcador — Motor de análisis y pronóstico deportivo

Prototipo funcional que analiza partidos de fútbol, baloncesto y tenis a partir de
estadísticas que tú suministras, y calcula probabilidades por mercado (1X2, over/under,
corners, anotador de goles, asistencias, etc.) más un % de confianza.

## Cómo ejecutarlo

```bash
cd pronostico_app
pip install -r requirements.txt
python3 app.py
```

Abre http://localhost:5000 en el navegador. La primera ejecución crea automáticamente
`pronosticos.db` (SQLite) para guardar el historial de análisis — no necesitas configurar
nada más.

## Estructura

- `motor.py` — los 3 modelos estadísticos (fútbol: Poisson; baloncesto: normal sobre
  diferencial; tenis: logístico tipo Elo + winrate + H2H) y el análisis de jugadores.
- `app.py` — servidor Flask: rutas `/`, `/analizar` (API), `/historial`.
- `templates/`, `static/` — interfaz web.
- `pronosticos.db` — se crea solo; guarda cada análisis para consultarlo después en /historial.

## Qué extendería primero

1. **Datos reales en vivo, no manuales** — conectar una API de datos deportivos
   (p. ej. API-Football, SportRadar, Sportmonks) para autocompletar las estadísticas
   de los equipos/jugadores en vez de digitarlas a mano. Es el cambio de mayor impacto.
2. **Comparación contra cuotas de casas de apuestas** — traer cuotas reales (vía API de
   odds) y calcular "value bets": partidos donde tu probabilidad estimada supera la
   probabilidad implícita de la cuota.
3. **Modelo entrenado con histórico real** — hoy los pesos (0.4/0.4/0.2 en tenis, el
   ajuste de forma en fútbol, etc.) son razonables pero fijos a mano. Con datos
   históricos reales se podría entrenar un modelo (regresión logística o XGBoost) que
   ajuste esos pesos automáticamente y se pueda re-entrenar con cada jornada.
4. **Backtesting** — correr el modelo contra partidos pasados para medir qué tan
   calibrado está el % de confianza (¿los partidos marcados con 70% de confianza
   realmente aciertan ~70% de las veces?).
5. **Más jugadores y más mercados por jugador** (tarjetas, faltas, remates totales no
   solo a puerta) y en baloncesto/tenis, props de jugador individuales (puntos, aces, etc.).
6. **Cuentas de usuario** para que el historial sea por persona, no global.

## Autocompletado con APIs reales (nuevo)

Ahora puedes buscar un equipo o jugador y que el formulario se llene solo, en vez de
digitar todo a mano.

1. Copia `.env.example` a `.env` y completa tus claves, o expórtalas directo:
   ```bash
   export API_SPORTS_KEY="tu_clave_de_api-sports.io"
   export RAPIDAPI_KEY="tu_clave_de_rapidapi"   # solo si vas a usar tenis
   ```
2. Corre `python3 app.py` como siempre. Si una clave no está configurada, verás un
   aviso arriba del formulario, pero igual puedes llenar todo a mano.
3. En el formulario, escribe el nombre del equipo/jugador y da clic en **Buscar** —
   aparece una lista para elegir, y al hacer clic se rellenan los campos automáticamente.

**Cobertura por deporte:**
- **Fútbol** — API-Football (api-sports.io): equipos, goles a favor/en contra
  local/visitante, forma reciente, corners y tiros a puerta (promediando los últimos
  partidos en su condición de local/visita), y jugadores individuales (goles,
  asistencias, tiros, minutos). Verificado contra la documentación oficial.
- **Baloncesto** — API-Basketball (api-sports.io): puntos a favor/en contra
  local/visitante. Verificado contra la documentación oficial. El ritmo de
  juego (pace) no viene en este endpoint, así que ese campo sigue siendo manual.
- **Tenis** — usa una API de terceros en RapidAPI cuya documentación pública es más
  delgada que la de api-sports.io. **No pude probar una llamada real** en este entorno
  (sandbox sin acceso a internet), así que el mapeo de campos es un mejor esfuerzo
  defensivo: si falla, el error te va a mostrar un fragmento del JSON crudo que devolvió
  la API para que ajustes las claves en `providers.py` (función `TennisProvider.stats_jugador`).

**Cuidado con la cuota gratuita:** el plan gratis de api-sports.io da ~100 peticiones/día.
Autocompletar corners/tiros en fútbol puede gastar 5-6 peticiones por equipo (revisa
partido por partido), así que las respuestas se cachean en SQLite para no repetir
llamadas dentro de la misma ventana de tiempo.

## Cuotas reales y value bets (nuevo, automático)

Después de analizar un partido de fútbol o baloncesto, la app busca sola el partido
en las ligas más comunes usando The Odds API, trae la mejor cuota disponible entre
varias casas de apuestas, y calcula:
- probabilidad implícita de esa cuota (quitando el margen de la casa),
- la diferencia contra la probabilidad de tu modelo,
- si hay ventaja de más de 3 puntos porcentuales, lo marca como **value bet**,
- un tamaño de apuesta sugerido usando Kelly fraccionado (25% del Kelly puro, más
  prudente que el Kelly completo).

Si no encuentra el partido en las ligas más probables, aparece un selector para
elegir la liga/torneo correcto manualmente. Requiere `ODDS_API_KEY` (gratis, 500
peticiones/mes) — sin ella, el resto de la app sigue funcionando normal, solo se
omite esta sección.

## Calibración (nuevo)

En `/historial` puedes marcar cada análisis ya jugado como "Acertó" o "Falló".
La página `/calibracion` agrupa esos resultados por rango de confianza del modelo
(0-50%, 50-60%, etc.) para que puedas ver si, por ejemplo, los pronósticos marcados
con "80-90% de confianza" de verdad aciertan esa proporción de veces. Con pocos
partidos marcados el dato no es confiable todavía — la página lo avisa cuando hay
menos de 5 en un rango. También puedes exportar todo el historial a CSV desde ahí.
