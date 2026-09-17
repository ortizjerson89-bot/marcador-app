document.querySelectorAll(".btn-resolver").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const id = btn.dataset.id;
    const estado = btn.dataset.estado;
    try {
      const resp = await fetch(`/api/apuestas/${id}/resolver`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ estado }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || "Error");
      location.reload();
    } catch (e) {
      alert("No se pudo resolver: " + e.message);
    }
  });
});
