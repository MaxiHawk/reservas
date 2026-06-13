# 🏛️ Reservas Tribunal Endovascular — TMED732 UNAB 2026

Aplicación Streamlit con la estética del **Universo AngioMasters // Archivo de la
Orden** (uam.maxihawk.com) para que los aspirantes reserven su bloque de la
**Defensa Final Oral** (martes 16 de junio 2026, 14:00–16:46), usando una base
de datos de Notion como backend administrado por el Sumo Cartógrafo.

## Diseño

- **Tema oscuro sci-fi**: fondo azul profundo, acentos cián neón (#00E5FF) y
  dorado (#FFD166), tipografías Rajdhani + Share Tech Mono, etiquetas tipo
  terminal (`// ARCHIVO DE LA ORDEN //`).
- **Optimizado para 1 día y móviles**: línea de tiempo vertical (no grilla),
  tarjetas a ancho completo con hora prominente, botones grandes táctiles,
  pausas/apertura/cierre como filas compactas no reservables.
- **Narrativa gamificada**: bloques “sellados”, escuadrones, aspirantes,
  modalidad ★ individual destacada en dorado.
- **Auto-sincronización** cada 5 s (`st.fragment`), métricas de bloques libres
  vs. sellados y barra de progreso de la jornada.

## Bloques (desde Notion)

La base ya contiene el cronograma oficial: Apertura, **B1–B12** (parejas),
**B13** (individual), 3 pausas docentes y cierre. La propiedad **Modalidad**
distingue `Pareja` (pide 2 nombres) de `Individual` (pide 1). Las filas sin
modalidad (pausas, apertura, cierre) nunca son reservables.

| Estado | Significado | ¿Reservable? |
|---|---|---|
| 🟢 Disponible | Libre | Sí |
| 🔴 Reservado | Sellado por un escuadrón | No |
| ⚫ Bloqueado | Pausa / apertura / cierre o bloqueado por el docente | No |

## ⏳ Liberación programada (estilo venta de entradas)

La apertura de reservas se controla 100% desde Notion con la fila de control
**“⚙️ Liberación de reservas — editar solo la fecha”**:

- Su propiedad **Horario** define la fecha/hora de apertura (se interpreta
  LITERALMENTE como hora de Chile, tal como se ve en Notion).
- **Antes de esa hora**: la app muestra el cronograma bloqueado (🔒) y una
  **cuenta regresiva en vivo** (días : horas : min : seg). El bloqueo también
  es real en el servidor: nadie puede reservar aunque manipule la interfaz.
- **Al llegar a cero**: la app se desbloquea automáticamente y aparecen los
  botones “Reservar”. Sin intervención del docente.
- **Para cambiar la fecha**: edita solo el Horario de esa fila (se refleja en
  ≤ 5 s). Si borras la fila, las reservas quedan abiertas siempre.
- No cambies el título: la app la reconoce porque comienza con ⚙️. Esta fila
  nunca se muestra como bloque ni puede reservarse.
- Zona horaria configurable vía secret opcional `RESERVAS_TZ`
  (default: `America/Santiago`).

> 💡 Consejo: como Streamlit Cloud “duerme” las apps sin tráfico, abre la URL
> unos 10 minutos antes de la liberación para que esté despierta en el peak.

## Garantías técnicas

- **Anti doble-reserva**: instancia única (`@st.cache_resource`) +
  `threading.Lock` global + re-verificación contra Notion antes de escribir.
- **Alta demanda**: caché en memoria (TTL 5 s) → 30+ estudiantes simultáneos
  generan muy pocas llamadas reales a la API de Notion (~3 req/s de límite).
- **Confirmación en 2 pasos**: nombres → declaración explícita → “Sellar la
  reserva”.
- **Liberación programada**: bloqueo real en servidor antes de la fecha,
  apertura automática al llegar a cero.

## Configuración (una sola vez)

1. Crea una integración en **notion.so/profile/integrations** (Read + Update
   content) y copia el token.
2. Abre la base **“Reserva de Bloques — TMED732 UNAB”** → menú `•••` →
   **Connections** → agrega tu integración.
3. Copia el ID de la base (32 caracteres hex de la URL, antes de `?v=`).

## Desarrollo local

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # edita token e ID
streamlit run app.py
```

## Tests (sin red, sin Notion, sin Streamlit)

```bash
python3 tests/test_core.py
```

Incluye: 30 hilos sellando el mismo bloque → exactamente 1 gana, 29 conflictos,
1 sola escritura; defensa individual con 1 nombre; pareja exige 2 nombres;
pausas no reservables; caché e invalidación; liberación programada (bloqueo
antes de la fecha, apertura después, carrera en el segundo exacto de apertura).

## Despliegue — Streamlit Community Cloud (gratis)

1. Sube la carpeta a GitHub (¡sin `secrets.toml`!).
2. **share.streamlit.io** → *New app* → repo + `app.py`.
3. En **Secrets** pega:
   ```toml
   NOTION_TOKEN = "tu_token"
   NOTION_DATABASE_ID = "tu_database_id"
   CACHE_TTL_SECONDS = 5
   ```
4. Comparte la URL con el curso.

> ⚠️ Tras actualizar archivos en GitHub, si ves errores raros usa
> **Manage app → Reboot app** (limpia la caché de recursos).

## Flujo del Sumo Cartógrafo (docente, desde Notion)

- **Cambiar la fecha de liberación**: editar el `Horario` de la fila ⚙️.
- **Liberar una reserva**: `Estado → Disponible` y borrar nombres.
- **Bloquear un bloque**: `Estado → Bloqueado`.
- **Cambiar horarios o agregar bloques**: editar `Horario` / nueva fila con
  `Modalidad` y `Estado = Disponible`.

## Estructura

```
reserva-bloques-streamlit/
├── app.py                    # UI gamificada (countdown + timeline + diálogo 2 pasos)
├── notion_store.py           # Cliente Notion + reservas thread-safe + liberación
├── requirements.txt
├── .streamlit/
│   ├── config.toml           # Tema oscuro AngioMasters
│   └── secrets.toml.example
└── tests/
    └── test_core.py          # 18 tests (concurrencia, modalidades, liberación)
```
