# Mi dashboard de salud — guía de instalación

Esto convierte tu dashboard en una web con URL propia (acceso rápido desde el
iPhone) con dos piezas de automatización opcionales: una que actualiza tus
datos de Garmin cada noche sola, y otra que actualiza tu peso cada vez que
Apple Health recibe una medición nueva de RENPHO.

**Nada de esto corre "solo" por arte de magia — corre en infraestructura
gratuita tuya (GitHub, y opcionalmente Vercel) que tú controlas.** Yo no
puedo ejecutar nada de forma continuada por ti.

---

## Parte 1 — Publicar el dashboard (obligatorio, ~10 min)

1. Crea una cuenta en [github.com](https://github.com) si no la tienes.
2. Crea un repositorio **nuevo** (botón verde "New"). Ponle un nombre que no
   delate de qué se trata (ej. `panel-personal-x7k2`, no
   `javier-salud-dashboard`) — recuerda que la web resultante será pública
   aunque nadie la enlace.
3. Sube **todo** el contenido de esta carpeta al repo (arrastra los archivos
   en la web de GitHub, o usa `git push` si te manejas con la terminal).
4. Ve a **Settings → Pages** en el repo. En "Source" elige
   `Deploy from a branch`, rama `main`, carpeta `/ (root)`. Guarda.
5. En 1-2 minutos tu dashboard estará en:
   `https://TU-USUARIO.github.io/NOMBRE-DEL-REPO/`
6. Abre esa URL en Safari del iPhone → botón compartir (□↑) →
   **"Añadir a pantalla de inicio"**. Ya tienes tu icono como una app.

En este punto el dashboard ya funciona con los datos de hoy (1/8/2026), y el
bot funciona metiendo tu clave de API la primera vez que lo uses (se guarda
en el propio iPhone, no hace falta repetirlo). Consigue una clave en
[console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys).

**Nada se actualiza todavía solo** — para eso, sigue las partes 2 y 3.

---

## Parte 2 — Actualización automática de Garmin (~15 min)

1. En tu repo de GitHub: **Settings → Secrets and variables → Actions →
   New repository secret**. Crea dos:
   - `GARMIN_EMAIL` = el email de tu cuenta Garmin Connect
   - `GARMIN_PASSWORD` = tu contraseña de Garmin Connect
2. Ya está — el workflow en `.github/workflows/sync-garmin.yml` corre solo
   cada noche a las 05:00 UTC y actualiza `data/activities.json`,
   `data/sleep.json` y `data/yearly.json` automáticamente.
3. Para probarlo ya mismo sin esperar: pestaña **Actions** del repo →
   "Sync Garmin data" → **Run workflow**.

⚠️ Usa el paquete no oficial `garminconnect` (no hay API oficial abierta
para particulares). Garmin podría cambiar algo internamente y romperlo en
cualquier momento — si un día deja de funcionar, es la primera sospechosa.

---

## Parte 3 — Actualización automática de peso (RENPHO → Apple Health) (~20 min, opcional)

Como RENPHO no tiene API propia, usamos el camino oficial: RENPHO ya
sincroniza con Apple Health → una app (**Health Auto Export**, ~5€ en el
App Store) reenvía esos datos automáticamente.

1. Crea una cuenta gratuita en [vercel.com](https://vercel.com) — puedes
   entrar directamente "with GitHub" usando la cuenta que ya creaste, sin
   contraseña nueva.
2. "Add New Project" → importa el mismo repositorio de GitHub. Vercel
   detecta solo el archivo `api/weight.js` y lo despliega como función.
3. En el proyecto de Vercel → **Settings → Environment Variables**, añade:
   - `GH_TOKEN` — un token de GitHub (Settings → Developer settings →
     Personal access tokens → Fine-grained, con permiso "Contents:
     Read and write" solo sobre este repo)
   - `GH_OWNER` — tu usuario de GitHub
   - `GH_REPO` — el nombre del repositorio
   - `SHARED_SECRET` — cualquier palabra secreta que te inventes
4. En el iPhone, abre **Health Auto Export** → Automatizaciones → Nueva →
   tipo **REST API**:
   - URL: `https://TU-PROYECTO.vercel.app/api/weight`
   - Formato: JSON
   - Cabecera personalizada: `X-Secret` = el mismo valor que pusiste en
     `SHARED_SECRET`
   - Métricas: `Weight Body Mass` (y `Body Fat Percentage` si quieres)
   - Frecuencia: la más frecuente que te deje (iOS decide cuándo corre
     realmente, no es instantáneo garantizado)

A partir de aquí, cada vez que Health Auto Export dispare la automatización,
tu peso se actualiza solo en el dashboard.

---

## Qué NO se actualiza solo

- El **desnivel/ubicación de actividades anteriores a junio 2016** — eso
  solo vino del export completo de Garmin que subiste una vez, no cambia.
- Los **eventos conocidos** (triatlones, tours, Sommerfest, Conil) — son
  fijos, los añadimos a mano. Si haces un cuarto tour a Gardasee algún año,
  toca añadirlo tú a `data/known_events.json` (o pídeme que te ayude y te
  paso el JSON actualizado para que lo subas).
- Si cambias de báscula o de reloj, hay que revisar los scripts.
