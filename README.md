# 🗓️ Reserva de Bloques — TMED732 UNAB (versión Streamlit)

Aplicación Streamlit para que estudiantes reserven bloques horarios en parejas,
usando una base de datos de Notion como backend que el docente administra
directamente desde Notion.

## Cómo funciona

- **Estudiantes**: ven la grilla de bloques (🟢 Disponible / 🔴 Reservado /
  ⚫ Bloqueado), eligen uno disponible, ingresan ambos nombres y **confirman en
  2 pasos**. La grilla se actualiza sola cada 5 segundos.
- **Docente**: administra todo desde Notion (crear bloques, bloquearlos,
  liberar reservas, ver quién tomó cada uno).
- **Anti doble-reserva**: Streamlit atiende cada sesión en un hilo dentro de un
  solo proceso. Todas las escrituras pasan por un `threading.Lock` global
  (instancia única vía `@st.cache_resource`) y el estado se re-verifica contra
  Notion justo antes de escribir. Si dos parejas intentan el mismo bloque a la
  vez, solo la primera gana.
- **Alta demanda**: el listado se sirve desde un caché en memoria con TTL de
  5 s, así 30+ estudiantes consultando a la vez generan muy pocas llamadas
  reales a la API de Notion (límite ~3 req/s).

## Estados del bloque (propiedad `Estado` en Notion)

| Estado | Significado | ¿Reservable? |
|---|---|---|
| 🟢 Disponible | Libre | Sí |
| 🔴 Reservado | Tomado por una pareja | No |
| ⚫ Bloqueado | Bloqueado por el docente | No |

## Configuración (una sola vez)

### 1. Crear la integración en Notion

1. Ve a **notion.so/profile/integrations** → *New integration*.
2. Nombre: `Reserva Bloques TMED732`. Workspace: tu workspace personal.
3. Capacidades: **Read content** y **Update content**.
4. Copia el **Internal Integration Secret** (token).

### 2. Conectar la base de datos a la integración

1. Abre la base **“Reserva de Bloques — TMED732 UNAB”** en Notion.
2. Menú `•••` (arriba a la derecha) → **Connections** → agrega tu integración.

### 3. Obtener el ID de la base de datos

Abre la base en el navegador y copia los **32 caracteres hexadecimales** de la
URL (antes de `?v=`). Ese es el `NOTION_DATABASE_ID`.

## Ejecutar en desarrollo (tu computador)

```bash
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# Edita .streamlit/secrets.toml con tu token e ID
streamlit run app.py
```

## Ejecutar tests (no requieren red, Notion ni Streamlit)

```bash
python3 tests/test_core.py
```

Incluye el test clave: **30 hilos reservando el mismo bloque a la vez →
exactamente 1 gana, 29 reciben conflicto, 1 sola escritura en Notion**.

## Desplegar en Streamlit Community Cloud (gratis, recomendado)

1. Sube esta carpeta a un repositorio de **GitHub** (¡sin `secrets.toml`!).
2. Entra a **share.streamlit.io** con tu cuenta de GitHub → *New app*.
3. Selecciona el repo, rama `main`, archivo `app.py`.
4. En **Advanced settings → Secrets**, pega:
   ```toml
   NOTION_TOKEN = "tu_token"
   NOTION_DATABASE_ID = "tu_database_id"
   CACHE_TTL_SECONDS = 5
   ```
5. *Deploy* y comparte la URL pública con tus estudiantes.

> ℹ️ Streamlit Community Cloud ejecuta la app en **una sola instancia/proceso**,
> que es exactamente lo que el diseño anti-doble-reserva necesita. No requiere
> configuración adicional.

> ⚠️ Si la app pasa un tiempo sin visitas, Streamlit Cloud la “duerme” y el
> primer acceso tarda ~30–60 s en despertar. Para tu evento de reservas,
> ábrela tú mismo unos minutos antes de la hora anunciada.

## Flujo del docente

- **Agregar bloques**: nueva fila en Notion con `Estado = Disponible` y `Horario`.
- **Bloquear un bloque**: cambia `Estado` a `Bloqueado`.
- **Liberar una reserva**: cambia `Estado` a `Disponible` y borra los nombres.
- **Ver reservas**: vista *Todos los bloques* o *Por estado* en Notion.

## Estructura del proyecto

```
reserva-bloques-streamlit/
├── app.py                  # Interfaz Streamlit (grilla + diálogo 2 pasos)
├── notion_store.py         # Cliente Notion + lógica de reservas thread-safe
├── requirements.txt
├── .streamlit/
│   └── secrets.toml.example
└── tests/
    └── test_core.py        # Tests de concurrencia (sin red ni Streamlit)
```
