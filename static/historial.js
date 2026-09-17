document.querySelectorAll(".marcar-resultado").forEach((contenedor) => {
  const id = contenedor.dataset.marcar;
  contenedor.querySelectorAll(".btn-marcar").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const valorActual = btn.classList.contains("activo");
      const nuevoValor = valorActual ? null : btn.dataset.valor;

      try {
        const resp = await fetch(`/historial/${id}/marcar`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ resultado_real: nuevoValor }),
        });
        if (!resp.ok) throw new Error("No se pudo guardar");

        contenedor.querySelectorAll(".btn-marcar").forEach((b) => b.classList.remove("activo"));
        if (nuevoValor) btn.classList.add("activo");
      } catch (e) {
        alert("Error al guardar: " + e.message);
      }
    });
  });
});
