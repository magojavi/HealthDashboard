// Endpoint que recibe el POST de Health Auto Export (peso desde Apple Health)
// y actualiza data/weight.json directamente en este mismo repo de GitHub.
//
// Se despliega solo con subir este archivo dentro de /api en un proyecto de Vercel
// conectado a este repo (Vercel detecta /api/*.js automáticamente).
//
// Variables de entorno a configurar en Vercel (Project Settings -> Environment Variables):
//   GH_TOKEN      - Personal Access Token de GitHub con permiso "repo" (Contents: Read/Write)
//   GH_OWNER      - tu usuario de GitHub
//   GH_REPO       - nombre de este repositorio
//   SHARED_SECRET - cualquier cadena secreta que solo tú conozcas (para evitar peticiones falsas)
//
// En Health Auto Export, configura la automatización REST API con:
//   URL: https://TU-PROYECTO.vercel.app/api/weight
//   Método: POST, formato JSON
//   Header personalizado: X-Secret: <el mismo valor que SHARED_SECRET>
//   Métrica seleccionada: Weight Body Mass (y opcionalmente Body Fat Percentage)

export default async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }
  if (req.headers["x-secret"] !== process.env.SHARED_SECRET) {
    return res.status(401).json({ error: "Unauthorized" });
  }

  try {
    const body = req.body;
    const metrics = body?.data?.metrics || [];
    const weightMetric = metrics.find((m) => m.name === "weight_body_mass");
    const fatMetric = metrics.find((m) => m.name === "body_fat_percentage");

    if (!weightMetric || !weightMetric.data?.length) {
      return res.status(200).json({ ok: true, note: "No weight data in this payload" });
    }

    const { GH_TOKEN, GH_OWNER, GH_REPO } = process.env;
    const apiBase = `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/contents/data/weight.json`;

    // 1. Leer el weight.json actual
    const getResp = await fetch(apiBase, {
      headers: { Authorization: `Bearer ${GH_TOKEN}`, Accept: "application/vnd.github+json" },
    });
    const getData = await getResp.json();
    const currentContent = JSON.parse(Buffer.from(getData.content, "base64").toString("utf-8"));
    const bySate = new Map(currentContent.map((r) => [r[0], r]));

    // 2. Fusionar las nuevas mediciones (una fila por día, quedándonos con la última del día)
    for (const point of weightMetric.data) {
      const date = point.date.slice(0, 10);
      const kg = Math.round(point.qty * 10) / 10;
      const existing = bySate.get(date) || [date, kg, null, null, null, null];
      existing[1] = kg;
      bySate.set(date, existing);
    }
    if (fatMetric) {
      for (const point of fatMetric.data) {
        const date = point.date.slice(0, 10);
        const pct = Math.round(point.qty * 1000) / 10; // suele venir como fracción 0-1
        const existing = bySate.get(date);
        if (existing) existing[3] = pct;
      }
    }

    const updated = Array.from(bySate.values()).sort((a, b) => (a[0] > b[0] ? 1 : -1));

    // 3. Escribir de vuelta en GitHub
    const newContent = Buffer.from(JSON.stringify(updated)).toString("base64");
    const putResp = await fetch(apiBase, {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: "Actualización automática de peso (Health Auto Export)",
        content: newContent,
        sha: getData.sha,
      }),
    });

    if (!putResp.ok) {
      const err = await putResp.text();
      return res.status(500).json({ error: "GitHub write failed", detail: err });
    }

    return res.status(200).json({ ok: true, updated: weightMetric.data.length });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
