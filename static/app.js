const tabs = document.querySelectorAll(".sport-tab");
const fieldGroups = document.querySelectorAll("[data-sport-fields]");
const form = document.getElementById("form-analisis");
const panel = document.getElementById("panel-resultado");

let deporteActual = "futbol";

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    deporteActual = tab.dataset.sport;
    fieldGroups.forEach((g) => {
      g.hidden = g.dataset.sportFields !== deporteActual;
    });
  });
});

// ---------------------------------------------------------------------------
// Autocompletado desde APIs reales (equipos, jugadores de fútbol, tenistas)
// ---------------------------------------------------------------------------

function mostrarMensajeBusqueda(contenedor, texto, tipo = "info") {
  contenedor.innerHTML = `<div class="resultado-mensaje ${tipo}">${texto}</div>`;
}

function rellenarCampos(grupo, campos) {
  Object.entries(campos).forEach(([nombre, valor]) => {
    if (nombre.startsWith("_") || valor === null || valor === undefined) return;
    const input = grupo.querySelector(`input[name="${nombre}"]`);
    if (input) input.value = valor;
  });
}

// --- Buscar y autocompletar EQUIPO (fútbol / baloncesto) ---
document.querySelectorAll("[data-buscar-equipo]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const deporte = btn.dataset.deporte;
    const rol = btn.dataset.rol;
    const grupo = btn.closest(".sport-fields");
    const inputNombre = grupo.querySelector(`input[data-buscable="equipo"][data-rol="${rol}"]`);
    const contenedorResultados = grupo.querySelector(`[data-resultados-equipo="${deporte}-${rol}"]`);
    const nombre = inputNombre.value.trim();
    if (!nombre) return;

    btn.disabled = true;
    mostrarMensajeBusqueda(contenedorResultados, "Buscando…");
    try {
      const resp = await fetch(`/api/buscar_equipo?deporte=${deporte}&q=${encodeURIComponent(nombre)}`);
      const data = await resp.json();
      if (data.error) { mostrarMensajeBusqueda(contenedorResultados, data.error, "error"); return; }
      if (!data.resultados.length) { mostrarMensajeBusqueda(contenedorResultados, "Sin resultados.", "info"); return; }

      contenedorResultados.innerHTML = "";
      data.resultados.slice(0, 6).forEach((eq) => {
        const item = document.createElement("div");
        item.className = "resultado-item";
        item.innerHTML = `<span>${eq.nombre}</span><span class="pais">${eq.pais || ""}</span>`;
        item.addEventListener("click", async () => {
          inputNombre.value = eq.nombre;
          mostrarMensajeBusqueda(contenedorResultados, "Trayendo estadísticas…");
          try {
            const r2 = await fetch("/api/autocompletar_equipo", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ deporte, team_id: eq.id, rol, incluir_corners_tiros: deporte === "futbol" }),
            });
            const d2 = await r2.json();
            if (d2.error) { mostrarMensajeBusqueda(contenedorResultados, d2.error, "error"); return; }
            rellenarCampos(grupo, d2.campos);
            // Guardar team_id / liga / season en los campos ocultos (para autocompletar jugadores luego)
            const hTeam = grupo.querySelector(`[data-hidden="${deporte}-${rol}-team_id"]`);
            const hLiga = grupo.querySelector(`[data-hidden="${deporte}-${rol}-liga_id"]`);
            const hSeason = grupo.querySelector(`[data-hidden="${deporte}-${rol}-season"]`);
            if (hTeam) hTeam.value = eq.id;
            if (hLiga && d2.campos._liga_id) hLiga.value = d2.campos._liga_id;
            if (hSeason && d2.campos._season) hSeason.value = d2.campos._season;
            mostrarMensajeBusqueda(contenedorResultados, `Datos de ${eq.nombre} cargados (API-${deporte === "futbol" ? "Football" : "Basketball"}).`, "ok");

            if (deporte === "futbol" && d2.campos._season) {
              try {
                const rl = await fetch(`/api/lesionados?team_id=${eq.id}&season=${d2.campos._season}`);
                const dl = await rl.json();
                if (dl.lesionados && dl.lesionados.length) {
                  const lista = dl.lesionados.map((l) => `${l.nombre}${l.motivo ? " (" + l.motivo + ")" : ""}`).join(", ");
                  contenedorResultados.insertAdjacentHTML("beforeend", `<div class="aviso-lesionados">⚠ Bajas actuales: ${lista}</div>`);
                }
              } catch (e) { /* informativo, no crítico */ }
            }
          } catch (e) {
            mostrarMensajeBusqueda(contenedorResultados, "Error al traer estadísticas: " + e.message, "error");
          }
        });
        contenedorResultados.appendChild(item);
      });
    } catch (e) {
      mostrarMensajeBusqueda(contenedorResultados, "Error de red: " + e.message, "error");
    } finally {
      btn.disabled = false;
    }
  });
});

// --- Buscar y autocompletar JUGADOR de fútbol ---
document.querySelectorAll("[data-buscar-jugador]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const rol = btn.dataset.rol;
    const idx = btn.dataset.idx;
    const grupo = btn.closest(".sport-fields");
    const inputNombre = grupo.querySelector(`input[data-buscable="jugador"][data-rol="${rol}"][data-idx="${idx}"]`);
    const contenedorResultados = grupo.querySelector(`[data-resultados-jugador="${rol}-${idx}"]`);
    const nombre = inputNombre.value.trim();

    const teamId = grupo.querySelector(`[data-hidden="futbol-${rol}-team_id"]`)?.value;
    const season = grupo.querySelector(`[data-hidden="futbol-${rol}-season"]`)?.value;
    if (!teamId || !season) {
      mostrarMensajeBusqueda(contenedorResultados, `Primero busca y selecciona el equipo ${rol === "local" ? "local" : "visitante"} arriba.`, "error");
      return;
    }
    if (!nombre) return;

    btn.disabled = true;
    mostrarMensajeBusqueda(contenedorResultados, "Buscando…");
    try {
      const resp = await fetch(`/api/buscar_jugador?q=${encodeURIComponent(nombre)}&team_id=${teamId}&season=${season}`);
      const data = await resp.json();
      if (data.error) { mostrarMensajeBusqueda(contenedorResultados, data.error, "error"); return; }
      if (!data.resultados.length) { mostrarMensajeBusqueda(contenedorResultados, "Sin resultados en ese equipo/temporada.", "info"); return; }

      contenedorResultados.innerHTML = "";
      data.resultados.slice(0, 6).forEach((j) => {
        const item = document.createElement("div");
        item.className = "resultado-item";
        item.innerHTML = `<span>${j.nombre}</span>`;
        item.addEventListener("click", async () => {
          mostrarMensajeBusqueda(contenedorResultados, "Trayendo estadísticas…");
          try {
            const r2 = await fetch("/api/autocompletar_jugador", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ player_id: j.id, team_id: parseInt(teamId), season: parseInt(season) }),
            });
            const d2 = await r2.json();
            if (d2.error) { mostrarMensajeBusqueda(contenedorResultados, d2.error, "error"); return; }
            const c = d2.campos;
            inputNombre.value = c.nombre;
            grupo.querySelector(`input[name="${rol}_j${idx}_goles"]`).value = c.goles;
            grupo.querySelector(`input[name="${rol}_j${idx}_tiros"]`).value = c.tiros;
            grupo.querySelector(`input[name="${rol}_j${idx}_asistencias"]`).value = c.asistencias;
            grupo.querySelector(`input[name="${rol}_j${idx}_minutos"]`).value = c.minutos;
            mostrarMensajeBusqueda(contenedorResultados, `Datos de ${c.nombre} cargados (API-Football).`, "ok");
          } catch (e) {
            mostrarMensajeBusqueda(contenedorResultados, "Error al traer estadísticas: " + e.message, "error");
          }
        });
        contenedorResultados.appendChild(item);
      });
    } catch (e) {
      mostrarMensajeBusqueda(contenedorResultados, "Error de red: " + e.message, "error");
    } finally {
      btn.disabled = false;
    }
  });
});

// --- Autocompletar TENISTA ---
document.querySelectorAll("[data-buscar-tenis]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const lado = btn.dataset.lado;
    const grupo = btn.closest(".sport-fields");
    const inputNombre = grupo.querySelector(`input[data-buscable="tenis"][data-lado="${lado}"]`);
    const nombre = inputNombre.value.trim();
    if (!nombre) return;

    btn.disabled = true;
    btn.textContent = "Buscando…";
    try {
      const resp = await fetch("/api/autocompletar_tenis", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nombre }),
      });
      const data = await resp.json();
      if (data.error) { alert(data.error); return; }
      const c = data.campos;
      inputNombre.value = c.nombre;
      grupo.querySelector(`input[name="jugador_${lado}_ranking_pts"]`).value = c.ranking_pts;
      if (c.winrate !== null) grupo.querySelector(`input[name="jugador_${lado}_winrate"]`).value = c.winrate;
    } catch (e) {
      alert("Error al traer estadísticas: " + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Buscar";
    }
  });
});

function recolectarDatos() {
  const grupoActivo = document.querySelector(`[data-sport-fields="${deporteActual}"]`);
  const inputs = grupoActivo.querySelectorAll("input");
  const datos = {};
  inputs.forEach((input) => {
    if (input.value !== "") datos[input.name] = input.value;
  });
  return datos;
}

function barra(etiqueta, valor, extra = "") {
  const v = Math.max(0, Math.min(100, valor));
  return `
    <div class="barra-item">
      <div class="barra-etiquetas">
        <span>${etiqueta}${extra ? ` <span style="color:var(--hueso-tenue)">(${extra})</span>` : ""}</span>
        <span class="barra-valor">${valor}%</span>
      </div>
      <div class="barra-track"><div class="barra-fill" style="width:${v}%"></div></div>
    </div>`;
}

function renderFutbol(r) {
  const m = r.mercados;
  let html = "";

  html += `<div class="bloque-mercado"><h4>Resultado (1X2)</h4>
    ${barra("Gana " + "local", m["1x2"].gana_local)}
    ${barra("Empate", m["1x2"].empate)}
    ${barra("Gana visitante", m["1x2"].gana_visita)}
  </div>`;

  html += `<div class="bloque-mercado"><h4>Goles</h4>
    <div class="datos-clave">
      <div class="dato-clave">Marcador más probable: <b>${m.goles.marcador_mas_probable}</b> (${m.goles.prob_marcador_exacto}%)</div>
      <div class="dato-clave">Goles esperados totales: <b>${m.goles.total_goles_esperados}</b></div>
      <div class="dato-clave">xG local: <b>${m.goles.goles_esperados_local}</b></div>
      <div class="dato-clave">xG visita: <b>${m.goles.goles_esperados_visita}</b></div>
    </div>
    <div style="margin-top:12px">
      ${barra("Over 2.5 goles", m.goles.over_2_5)}
      ${barra("Ambos anotan — Sí", m.goles.ambos_anotan_si)}
    </div>
  </div>`;

  html += `<div class="bloque-mercado"><h4>Corners</h4>
    <div class="datos-clave">
      <div class="dato-clave">Corners esperados local: <b>${m.corners.corners_esperados_local}</b></div>
      <div class="dato-clave">Corners esperados visita: <b>${m.corners.corners_esperados_visita}</b></div>
      <div class="dato-clave">Total esperado: <b>${m.corners.total_corners_esperados}</b></div>
      <div class="dato-clave">Tiros totales esperados: <b>${m.tiros.total_tiros_esperados}</b></div>
    </div>
    <div style="margin-top:12px">${barra("Over 9.5 corners", m.corners.over_9_5)}</div>
  </div>`;

  const hayJugadores = (r.jugadores.local.length + r.jugadores.visita.length) > 0;
  if (hayJugadores) {
    html += `<div class="bloque-mercado"><h4>Jugadores clave</h4>`;
    [["Local", r.jugadores.local], ["Visitante", r.jugadores.visita]].forEach(([lado, lista]) => {
      lista.forEach((j) => {
        html += `<div class="jugador-card">
          <div class="jugador-nombre">${j.nombre} <span style="color:var(--hueso-tenue);font-weight:400">— ${lado}</span></div>
          <div class="jugador-stats-row">
            <span>Anota 1+: <b>${j.prob_anota_1_mas}%</b></span>
            <span>Anota 2+: <b>${j.prob_anota_2_mas}%</b></span>
            <span>Goles esp.: <b>${j.goles_esperados}</b></span>
            <span>Tiros esp.: <b>${j.tiros_esperados}</b></span>
            <span>Asiste 1+: <b>${j.prob_asiste_1_mas}%</b></span>
          </div>
        </div>`;
      });
    });
    html += `</div>`;
  }

  return html;
}

function renderBaloncesto(r) {
  const m = r.mercados;
  return `
  <div class="bloque-mercado"><h4>Ganador (Moneyline)</h4>
    ${barra("Gana local", m.moneyline.gana_local)}
    ${barra("Gana visitante", m.moneyline.gana_visita)}
  </div>
  <div class="bloque-mercado"><h4>Puntos</h4>
    <div class="datos-clave">
      <div class="dato-clave">Puntos esperados local: <b>${m.puntos.puntos_esperados_local}</b></div>
      <div class="dato-clave">Puntos esperados visita: <b>${m.puntos.puntos_esperados_visita}</b></div>
      <div class="dato-clave">Total esperado: <b>${m.puntos.total_esperado}</b></div>
      <div class="dato-clave">Diferencial esperado: <b>${m.puntos.diferencial_esperado}</b></div>
    </div>
    <div style="margin-top:12px">
      ${barra(`Over ${m.puntos.linea_total_evaluada}`, m.puntos.over)}
      ${barra(`Under ${m.puntos.linea_total_evaluada}`, m.puntos.under)}
    </div>
  </div>`;
}

function renderTenis(r) {
  const m = r.mercados;
  const nombres = Object.keys(m.ganador_partido);
  let html = `<div class="bloque-mercado"><h4>Ganador del partido</h4>`;
  nombres.forEach((n) => { html += barra(n, m.ganador_partido[n]); });
  html += `</div>`;
  html += `<div class="bloque-mercado"><h4>Desglose del modelo</h4>
    <div class="datos-clave">
      <div class="dato-clave">Por ranking (Elo): <b>${m.desglose_modelo.prob_por_ranking_elo}%</b></div>
      <div class="dato-clave">Por % de victorias: <b>${m.desglose_modelo.prob_por_winrate}%</b></div>
      <div class="dato-clave">Por forma reciente: <b>${m.desglose_modelo.prob_por_forma}%</b></div>
    </div>
  </div>`;
  return html;
}

function renderResultado(r) {
  let cuerpoMercados = "";
  if (r.deporte === "futbol") cuerpoMercados = renderFutbol(r);
  else if (r.deporte === "baloncesto") cuerpoMercados = renderBaloncesto(r);
  else if (r.deporte === "tenis") cuerpoMercados = renderTenis(r);

  const factoresHtml = r.factores.map((f) => `<li>${f}</li>`).join("");

  let calibracionHtml = "";
  if (r.calibracion && r.calibracion.disponible) {
    const dif = r.calibracion.diferencia;
    calibracionHtml = `<p class="calibracion-linea">Según tus ${r.calibracion.muestras} partidos marcados en un rango de confianza parecido, el acierto real ronda <b>${r.calibracion.confianza_ajustada}%</b> (${dif >= 0 ? "+" : ""}${dif} pts vs. lo que dice el modelo).</p>`;
  } else if (r.calibracion) {
    calibracionHtml = `<p class="calibracion-linea sutil">Aún no hay suficientes partidos marcados en tu <a href="/historial">historial</a> (${r.calibracion.muestras}/${r.calibracion.muestras_necesarias}) para ajustar esta confianza con datos reales.</p>`;
  }

  panel.innerHTML = `
    <div class="res-header">
      <div class="res-pronostico-label">Pronóstico principal</div>
      <div class="res-pronostico">${r.pronostico_principal}</div>
      <div class="res-confianza-row">
        <div class="res-confianza-track"><div class="res-confianza-fill" style="width:${r.confianza}%"></div></div>
        <div class="res-confianza-num">${r.confianza}%</div>
      </div>
      ${calibracionHtml}
    </div>
    ${cuerpoMercados}
    <div id="bloque-prediccion-oficial"></div>
    <div id="bloque-cuotas"></div>
    <div class="bloque-mercado"><h4>Factores considerados</h4>
      <ul class="factores-lista">${factoresHtml}</ul>
    </div>
  `;
}

async function mostrarPrediccionOficial(r, grupo) {
  const destino = document.getElementById("bloque-prediccion-oficial");
  if (!destino || r.deporte !== "futbol") return;
  const teamA = grupo.querySelector('[data-hidden="futbol-local-team_id"]')?.value;
  const teamB = grupo.querySelector('[data-hidden="futbol-visita-team_id"]')?.value;
  if (!teamA || !teamB) return;

  try {
    const resp = await fetch("/api/prediccion_oficial", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ team_a_id: parseInt(teamA), team_b_id: parseInt(teamB) }),
    });
    const data = await resp.json();
    if (data.error) return; // no molestamos con un error visible por esto, es un extra informativo
    const p = data.prediccion;
    destino.innerHTML = `
      <div class="bloque-mercado">
        <h4>Comparación con el pronóstico oficial de API-Football</h4>
        <div class="datos-clave">
          <div class="dato-clave">Tu modelo: <b>${r.pronostico_principal}</b> (${r.probabilidad_pronostico}%)</div>
          <div class="dato-clave">API-Football sugiere: <b>${p.ganador_sugerido || "sin ganador claro"}</b></div>
          ${p.porcentaje_local ? `<div class="dato-clave">Su % local/empate/visita: <b>${p.porcentaje_local} / ${p.porcentaje_empate} / ${p.porcentaje_visita}</b></div>` : ""}
          ${p.consejo ? `<div class="dato-clave">Su consejo: <b>${p.consejo}</b></div>` : ""}
        </div>
        <p class="nota-value-bets">Dos modelos independientes viendo el mismo partido — si coinciden, es una señal más; si no, vale la pena mirar por qué antes de confiar en cualquiera de los dos a ciegas.</p>
      </div>`;
  } catch (e) { /* informativo, no interrumpe el flujo principal si falla */ }
}

// ---------------------------------------------------------------------------
// Cuotas reales y value bets — automático tras cada análisis de fútbol/baloncesto
// ---------------------------------------------------------------------------

const CUOTAS_OK = document.body.dataset.cuotasOk === "true";
const LIGAS_CACHE = {};

function probabilidadesGenericas(r) {
  if (r.deporte === "futbol") {
    const m = r.mercados;
    return {
      local: m["1x2"].gana_local / 100,
      empate: m["1x2"].empate / 100,
      visita: m["1x2"].gana_visita / 100,
      over: m.goles.over_2_5 / 100,
      under: m.goles.under_2_5 / 100,
    };
  }
  if (r.deporte === "baloncesto") {
    const m = r.mercados;
    return {
      local: m.moneyline.gana_local / 100,
      visita: m.moneyline.gana_visita / 100,
      over: m.puntos.over / 100,
      under: m.puntos.under / 100,
    };
  }
  if (r.deporte === "tenis") {
    const nombres = Object.keys(r.mercados.ganador_partido);
    return {
      local: r.mercados.ganador_partido[nombres[0]] / 100,
      visita: r.mercados.ganador_partido[nombres[1]] / 100,
    };
  }
  return null;
}

function renderArbitraje(arbitraje) {
  if (!arbitraje || !arbitraje.length) return "";
  const bloques = arbitraje.map((a) => {
    const filas = a.reparto.map((p) => `<li>${p.resultado}: cuota ${p.cuota} en <b>${p.casa}</b> — ${p.pct_del_monto_total}% del monto total</li>`).join("");
    return `
      <div class="aviso-arbitraje">
        <strong>⚡ Posible arbitraje detectado (${a.mercado}${a.punto ? " " + a.punto : ""})</strong>
        <p>Ganancia garantizada teórica: <b>${a.ganancia_garantizada_pct}%</b>, repartiendo así entre casas distintas:</p>
        <ul>${filas}</ul>
        <p class="nota-value-bets">Estas ventanas se cierran en minutos y algunas casas limitan cuentas que arbitran seguido — verifica las cuotas en vivo antes de mover dinero real.</p>
      </div>`;
  }).join("");
  return bloques;
}

function renderTablaValueBets(comparacion, evento, contexto) {
  if (!comparacion.length) {
    return `<p class="resultado-mensaje">Se encontró el partido pero ninguna casa tenía cuotas abiertas todavía para estos mercados.</p>`;
  }
  const filas = comparacion.map((c, idx) => `
    <tr class="${c.es_value_bet ? "fila-value" : ""}">
      <td>${c.resultado}${c.punto !== null && c.punto !== undefined ? " " + c.punto : ""}</td>
      <td>${c.cuota} <span class="casa-cuota">(${c.casa})</span></td>
      <td>${c.prob_modelo}%</td>
      <td>${c.prob_implicita_cuota}%</td>
      <td class="${c.valor_pct > 0 ? "edge-positivo" : "edge-negativo"}">${c.valor_pct > 0 ? "+" : ""}${c.valor_pct} pts</td>
      <td>${c.es_value_bet ? '<span class="badge-value">VALUE</span>' : "—"}</td>
      <td><button type="button" class="btn-registrar-apuesta" data-idx="${idx}">Registrar</button></td>
    </tr>`).join("");

  const dataset = JSON.stringify(comparacion).replace(/"/g, "&quot;");
  const contextoStr = JSON.stringify(contexto).replace(/"/g, "&quot;");

  return `
    <p class="resultado-mensaje ok">Partido encontrado: ${evento.home_team} vs ${evento.away_team} · cuotas de ${evento.num_casas} casas de apuestas.</p>
    <div class="tabla-scroll">
    <table class="tabla-value-bets" data-comparacion="${dataset}" data-contexto="${contextoStr}">
      <thead><tr><th>Resultado</th><th>Mejor cuota</th><th>Tu prob.</th><th>Prob. del mercado</th><th>Diferencia</th><th></th><th></th></tr></thead>
      <tbody>${filas}</tbody>
    </table>
    </div>
    <p class="nota-value-bets">"VALUE" = tu modelo cree que ese resultado es más probable de lo que paga la cuota, con más de 3 puntos porcentuales de margen. No es garantía de ganar la apuesta individual — es una ventaja estadística a favor, a largo plazo. Nunca apuestes más de lo que puedes perder.</p>
    <button type="button" class="btn-secundario" id="btn-ver-movimiento">Ver movimiento de cuotas de este partido</button>
    <div id="bloque-movimiento"></div>
  `;
}

function activarBotonesDeApuesta(bloque) {
  const tabla = bloque.querySelector(".tabla-value-bets");
  if (!tabla) return;
  const comparacion = JSON.parse(tabla.dataset.comparacion.replace(/&quot;/g, '"'));
  const contexto = JSON.parse(tabla.dataset.contexto.replace(/&quot;/g, '"'));

  tabla.querySelectorAll(".btn-registrar-apuesta").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const c = comparacion[parseInt(btn.dataset.idx)];
      const monto = prompt(`¿Cuánto vas a apostar a "${c.resultado}" a cuota ${c.cuota}?`, "10");
      if (!monto || isNaN(parseFloat(monto))) return;
      try {
        const resp = await fetch("/api/apuestas", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            deporte: contexto.deporte, partido: contexto.partido,
            mercado: c.mercado, seleccion: c.resultado, cuota: c.cuota, monto: parseFloat(monto),
          }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || "Error");
        btn.textContent = "Registrada ✓";
        btn.disabled = true;
      } catch (e) {
        alert("No se pudo registrar: " + e.message);
      }
    });
  });

  const btnMov = bloque.querySelector("#btn-ver-movimiento");
  if (btnMov) {
    btnMov.addEventListener("click", async () => {
      const destino = bloque.querySelector("#bloque-movimiento");
      destino.innerHTML = `<p class="resultado-mensaje">Cargando…</p>`;
      try {
        const resp = await fetch(`/api/movimiento_cuotas?home=${encodeURIComponent(contexto.home)}&away=${encodeURIComponent(contexto.away)}`);
        const data = await resp.json();
        if (!data.movimiento.length) {
          destino.innerHTML = `<p class="resultado-mensaje">Todavía no hay historial guardado para este partido — esta app solo registra la cuota cada vez que tú consultas este partido, así que vuelve a mirar en unas horas/días.</p>`;
          return;
        }
        const filas = data.movimiento.map((m) => `<tr><td>${m.fecha}</td><td>${m.mercado}</td><td>${m.resultado}${m.punto ? " " + m.punto : ""}</td><td>${m.cuota}</td><td>${m.casa}</td></tr>`).join("");
        destino.innerHTML = `<div class="tabla-scroll"><table class="tabla-value-bets"><thead><tr><th>Momento consultado</th><th>Mercado</th><th>Resultado</th><th>Cuota</th><th>Casa</th></tr></thead><tbody>${filas}</tbody></table></div>`;
      } catch (e) {
        destino.innerHTML = `<p class="resultado-mensaje error">${e.message}</p>`;
      }
    });
  }
}

async function cargarLigas(deporte) {
  if (LIGAS_CACHE[deporte]) return LIGAS_CACHE[deporte];
  const resp = await fetch(`/api/listar_ligas_cuotas?deporte=${deporte}`);
  const data = await resp.json();
  if (data.error) throw new Error(data.error);
  LIGAS_CACHE[deporte] = data.ligas;
  return data.ligas;
}

async function intentarValueBets(r, datos) {
  const bloque = document.getElementById("bloque-cuotas");
  if (!CUOTAS_OK || !["futbol", "baloncesto", "tenis"].includes(r.deporte)) return;

  const equipoLocal = datos.local_nombre || datos.jugador_a_nombre || "Local";
  const equipoVisita = datos.visita_nombre || datos.jugador_b_nombre || "Visitante";
  const probabilidades = probabilidadesGenericas(r);

  bloque.innerHTML = `<div class="bloque-mercado"><h4>Cuotas reales y value bets</h4><p class="resultado-mensaje">Buscando el partido en las casas de apuestas…</p></div>`;

  let ligas;

  try {
    ligas = await cargarLigas(r.deporte);
  } catch (e) {
    bloque.innerHTML = `<div class="bloque-mercado"><h4>Cuotas reales y value bets</h4><p class="resultado-mensaje error">${e.message}</p></div>`;
    return;
  }

  let encontrado = null;
  const intentos = ligas.slice(0, 6);
  for (const liga of intentos) {
    try {
      const resp = await fetch("/api/comparar_cuotas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sport_key: liga.key, equipo_local: equipoLocal, equipo_visita: equipoVisita, probabilidades }),
      });
      const data = await resp.json();
      if (!data.error) { encontrado = data; break; }
    } catch (e) { /* seguir probando con la siguiente liga */ }
  }

  if (encontrado) {
    const contexto = { deporte: r.deporte, partido: `${encontrado.evento.home_team} vs ${encontrado.evento.away_team}`, home: encontrado.evento.home_team, away: encontrado.evento.away_team };
    bloque.innerHTML = `<div class="bloque-mercado"><h4>Cuotas reales y value bets</h4>${renderArbitraje(encontrado.arbitraje)}${renderTablaValueBets(encontrado.comparacion, encontrado.evento, contexto)}</div>`;
    activarBotonesDeApuesta(bloque);
    return;
  }

  // No se encontró automáticamente: dejamos un selector manual de liga/torneo
  const opciones = ligas.map((l) => `<option value="${l.key}">${l.titulo}</option>`).join("");
  bloque.innerHTML = `
    <div class="bloque-mercado">
      <h4>Cuotas reales y value bets</h4>
      <p class="resultado-mensaje">No encontramos este partido automáticamente en las ligas más comunes. Elige la liga/torneo correcto:</p>
      <div class="input-buscable">
        <select id="select-liga-cuotas">${opciones}</select>
        <button type="button" class="btn-buscar" id="btn-reintentar-cuotas">Buscar cuotas</button>
      </div>
      <div id="resultado-cuotas-manual"></div>
    </div>`;

  document.getElementById("btn-reintentar-cuotas").addEventListener("click", async () => {
    const key = document.getElementById("select-liga-cuotas").value;
    const destino = document.getElementById("resultado-cuotas-manual");
    destino.innerHTML = `<p class="resultado-mensaje">Buscando…</p>`;
    try {
      const resp = await fetch("/api/comparar_cuotas", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sport_key: key, equipo_local: equipoLocal, equipo_visita: equipoVisita, probabilidades }),
      });
      const data = await resp.json();
      if (data.error) { destino.innerHTML = `<p class="resultado-mensaje error">${data.error}</p>`; return; }
      const contexto = { deporte: r.deporte, partido: `${data.evento.home_team} vs ${data.evento.away_team}`, home: data.evento.home_team, away: data.evento.away_team };
      destino.innerHTML = `${renderArbitraje(data.arbitraje)}${renderTablaValueBets(data.comparacion, data.evento, contexto)}`;
      activarBotonesDeApuesta(destino);
    } catch (e) {
      destino.innerHTML = `<p class="resultado-mensaje error">${e.message}</p>`;
    }
  });
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = form.querySelector(".btn-analizar");
  btn.disabled = true;
  btn.textContent = "Analizando…";

  try {
    const datos = recolectarDatos();
    const resp = await fetch("/analizar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deporte: deporteActual, datos }),
    });
    const r = await resp.json();
    if (!resp.ok) throw new Error(r.error || "Error desconocido");
    renderResultado(r);
    intentarValueBets(r, datos);
    const grupoActivo = document.querySelector(`[data-sport-fields="${deporteActual}"]`);
    mostrarPrediccionOficial(r, grupoActivo);
  } catch (err) {
    panel.innerHTML = `<div class="resultado-vacio"><p>No se pudo analizar: ${err.message}</p></div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Analizar partido";
  }
});
