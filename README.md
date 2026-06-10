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

## Garantías técnicas

- **Anti doble-reserva**: instancia única (`@st.cache_resource`) +
  `threading.Lock` global + re-verificación contra Notion antes de escribir.
- **Alta demanda**: caché en memoria (TTL 5 s) → 30+ estudiantes simultáneos
  generan muy pocas llamadas reales a la API de Notion (~3 req/s de límite).
- **Confirmación en 2 pasos**: nombres → declaración explícita → “Sellar la
  reserva”.

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
pausas no reservables; caché e invalidación.

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

> ⚠️ Streamlit Cloud “duerme” apps sin visitas: ábrela unos minutos antes de
> anunciar la hora de reservas para que despierte antes del peak.

## Flujo del Sumo Cartógrafo (docente, desde Notion)

- **Liberar una reserva**: `Estado → Disponible` y borrar nombres.
- **Bloquear un bloque**: `Estado → Bloqueado`.
- **Cambiar horarios o agregar bloques**: editar `Horario` / nueva fila con
  `Modalidad` y `Estado = Disponible`.

## Estructura

```
reserva-bloques-streamlit/
├── app.py                    # UI gamificada (timeline + diálogo 2 pasos)
├── notion_store.py           # Cliente Notion + reservas thread-safe + modalidades
├── requirements.txt
├── .streamlit/
│   ├── config.toml           # Tema oscuro AngioMasters
│   └── secrets.toml.example
└── tests/
    └── test_core.py
```
