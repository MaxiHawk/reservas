# 🏛️ Reservas Tribunal Endovascular — TMED732 UNAB 2026

Aplicación Streamlit con la estética del **Universo AngioMasters // Archivo de la
Orden** (uam.maxihawk.com) para que los aspirantes reserven su bloque de la
**Defensa Final Oral** (martes 16 de junio 2026, 14:00–17:30), usando una base
de datos de Notion como backend administrado por el Sumo Cartógrafo.

## Cronograma holgado (14:00 → 17:30)

25 estudiantes = 12 parejas + 1 individual → **13 bloques genéricos de 12 min**
(≈6 de defensa + 2 de preguntas + 4 de holgura/transición):

| Hora | Bloque |
|---|---|
| 14:00–14:10 | 🎬 Apertura e instrucciones |
| 14:10–14:58 | B1 · B2 · B3 · B4 |
| 14:58–15:08 | ⏸️ Pausa docente 1 |
| 15:08–15:56 | B5 · B6 · B7 · B8 |
| 15:56–16:06 | ⏸️ Pausa docente 2 |
| 16:06–16:54 | B9 · B10 · B11 · B12 |
| 16:54–17:04 | ⏸️ Pausa docente 3 |
| 17:04–17:16 | B13 |
| 17:16–17:30 | 🏁 Cierre + **14 min de buffer** de contingencia |

## Modalidad elegida al inscribirse

Ya **no hay bloque exclusivo individual**: todos los bloques dan la misma
oportunidad. En el formulario de reserva hay un interruptor
**“⭐ Me presento de forma INDIVIDUAL (sin pareja)”**:

- Apagado (default): pareja → exige **2 nombres** → Notion registra
  `Modalidad = Pareja`.
- Encendido: individual → exige **1 nombre** → Notion registra
  `Modalidad = Individual` y el bloque se muestra con insignia ⭐.

La columna **Modalidad** de Notion queda vacía en bloques disponibles y la
llena la app al reservar.

## Estados (desde Notion)

| Estado | Significado | ¿Reservable? |
|---|---|---|
| 🟢 Disponible | Libre | Sí |
| 🔴 Reservado | Sellado por un escuadrón o aspirante | No |
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
2. Abre la base **“Reserva de Bloques - Tribunal Endovascular 2026”** → menú
   `•••` → **Connections** → agrega tu integración.
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
1 sola escritura; modalidad individual con 1 nombre en cualquier bloque; pareja
exige 2 nombres; pausas no reservables; caché e invalidación; liberación
programada (bloqueo antes de la fecha, apertura después, carrera en el segundo
exacto de apertura).

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
- **Liberar una reserva**: `Estado → Disponible`, borrar nombres y `Modalidad`.
- **Bloquear un bloque**: `Estado → Bloqueado`.
- **Cambiar horarios o agregar bloques**: editar `Horario` / nueva fila con
  `Estado = Disponible` (sin Modalidad: la elige el estudiante).

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
