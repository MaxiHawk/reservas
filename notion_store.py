"""
Capa de datos: cliente de la API de Notion + lógica de reservas thread-safe.

Streamlit atiende cada sesión en un hilo dentro de UN solo proceso, por lo que
un threading.Lock global (compartido vía @st.cache_resource en app.py) basta
para serializar las escrituras y garantizar que un bloque no se reserve dos veces.

Modalidades (propiedad "Modalidad" en Notion):
  - Pareja: requiere 2 nombres.
  - Individual: requiere 1 nombre.

Liberación programada (fila de control ⚙️ en la misma base):
  - Una fila cuyo título comienza con "⚙️" define en su propiedad Horario la
    fecha/hora de APERTURA de las reservas. El docente la edita desde Notion.
  - La hora se interpreta LITERALMENTE como hora de Chile (America/Santiago),
    tal como se ve en Notion, sin conversiones de zona horaria.
  - Antes de esa hora, reserve() lanza ReservationsLockedError (bloqueo real
    en el servidor, no solo visual).
  - Si no existe fila ⚙️, las reservas están abiertas.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"

# Nombres EXACTOS de las propiedades en la base de Notion
PROP_TITULO = "Bloque"
PROP_HORARIO = "Horario"
PROP_ESTADO = "Estado"
PROP_EST1 = "Estudiante 1"
PROP_EST2 = "Estudiante 2"
PROP_FECHA_RESERVA = "Fecha de reserva"
PROP_MODALIDAD = "Modalidad"
PROP_NOTAS = "Notas"

ESTADO_DISPONIBLE = "Disponible"
ESTADO_RESERVADO = "Reservado"
ESTADO_BLOQUEADO = "Bloqueado"

MODALIDAD_PAREJA = "Pareja"
MODALIDAD_INDIVIDUAL = "Individual"

# Título de la fila de control de liberación (debe COMENZAR con este prefijo)
CONFIG_PREFIX = "⚙️"

# Zona horaria por defecto para interpretar las fechas escritas en Notion
TZ_DEFAULT = "America/Santiago"


class BlockNotFoundError(Exception):
    """El bloque no existe en la base de datos."""


class BlockUnavailableError(Exception):
    """El bloque ya no está disponible (reservado o bloqueado)."""


class ReservationsLockedError(Exception):
    """Las reservas aún no se liberan (fecha de apertura en el futuro)."""

    def __init__(self, release_dt: datetime):
        self.release_dt = release_dt
        super().__init__(
            f"Las reservas se abren el {release_dt.strftime('%d/%m/%Y a las %H:%M')} "
            f"(hora de Chile)."
        )


@dataclass
class Block:
    id: str
    titulo: str
    estado: str
    inicio: Optional[str]
    fin: Optional[str]
    estudiante_1: str
    estudiante_2: str
    modalidad: str  # "Pareja", "Individual" o "" (no reservable)
    notas: str = ""

    @property
    def es_config(self) -> bool:
        """True si es la fila de control ⚙️ (no se muestra como bloque)."""
        return self.titulo.strip().startswith(CONFIG_PREFIX)

    @property
    def reservable(self) -> bool:
        return (
            not self.es_config
            and self.estado == ESTADO_DISPONIBLE
            and self.modalidad in (MODALIDAD_PAREJA, MODALIDAD_INDIVIDUAL)
        )

    @property
    def es_individual(self) -> bool:
        return self.modalidad == MODALIDAD_INDIVIDUAL


def _plain_text(rich: list) -> str:
    return "".join(part.get("plain_text", "") for part in rich or [])


def _parse_block(page: dict) -> Block:
    props = page.get("properties", {})

    titulo = _plain_text(props.get(PROP_TITULO, {}).get("title", []))

    estado_sel = props.get(PROP_ESTADO, {}).get("select") or {}
    estado = estado_sel.get("name", "")

    modalidad_sel = props.get(PROP_MODALIDAD, {}).get("select") or {}
    modalidad = modalidad_sel.get("name", "")

    horario = props.get(PROP_HORARIO, {}).get("date") or {}

    return Block(
        id=page["id"],
        titulo=titulo,
        estado=estado,
        inicio=horario.get("start"),
        fin=horario.get("end"),
        estudiante_1=_plain_text(props.get(PROP_EST1, {}).get("rich_text", [])),
        estudiante_2=_plain_text(props.get(PROP_EST2, {}).get("rich_text", [])),
        modalidad=modalidad,
        notas=_plain_text(props.get(PROP_NOTAS, {}).get("rich_text", [])),
    )


def parse_local_dt(iso: Optional[str], tz_name: str = TZ_DEFAULT) -> Optional[datetime]:
    """Interpreta un datetime de Notion de forma LITERAL en la zona tz_name.

    Se ignora cualquier sufijo de zona ('Z' o '+hh:mm'): la hora que el docente
    ve escrita en Notion es la hora que vale, en hora de Chile. Así el
    comportamiento es predecible sin importar cómo serialice la API.
    """
    if not iso or len(iso) < 16:
        return None
    base = iso[:19] if len(iso) >= 19 else iso[:16] + ":00"
    try:
        naive = datetime.fromisoformat(base)
    except ValueError:
        return None
    return naive.replace(tzinfo=ZoneInfo(tz_name))


class NotionClient:
    """Cliente síncrono mínimo de la API de Notion (usa httpx)."""

    def __init__(self, token: str, database_id: str, timeout: float = 15.0):
        import httpx  # import local: los tests usan un cliente falso

        self.database_id = database_id
        self._http = httpx.Client(
            base_url=NOTION_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def query_database(self) -> list[dict]:
        """Devuelve todas las páginas de la base, ordenadas por Horario."""
        results: list[dict] = []
        payload: dict = {
            "sorts": [{"property": PROP_HORARIO, "direction": "ascending"}],
            "page_size": 100,
        }
        while True:
            resp = self._http.post(f"/databases/{self.database_id}/query", json=payload)
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            payload["start_cursor"] = data["next_cursor"]

    def get_page(self, page_id: str) -> dict:
        resp = self._http.get(f"/pages/{page_id}")
        resp.raise_for_status()
        return resp.json()

    def update_page(self, page_id: str, properties: dict) -> dict:
        resp = self._http.patch(f"/pages/{page_id}", json={"properties": properties})
        resp.raise_for_status()
        return resp.json()


class ReservationService:
    """Lógica de reservas: caché de lecturas, lock global de escrituras y
    liberación programada controlada desde Notion."""

    def __init__(
        self,
        client,
        cache_ttl: float = 5.0,
        tz_name: str = TZ_DEFAULT,
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self.client = client
        self.cache_ttl = cache_ttl
        self.tz_name = tz_name
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._write_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache: Optional[list[Block]] = None
        self._cache_time: float = 0.0

    # ---------- Tiempo ----------

    def now(self) -> datetime:
        return self._now_fn()

    # ---------- Lecturas ----------

    def list_blocks(self, force_refresh: bool = False) -> list[Block]:
        """Todas las filas (incluida la de control ⚙️), ordenadas por Horario."""
        with self._cache_lock:
            fresh = (
                self._cache is not None
                and (time.monotonic() - self._cache_time) < self.cache_ttl
            )
            if fresh and not force_refresh:
                return self._cache
            pages = self.client.query_database()
            blocks = [_parse_block(p) for p in pages]
            blocks.sort(key=lambda b: b.inicio or "")
            self._cache = blocks
            self._cache_time = time.monotonic()
            return blocks

    def visible_blocks(self) -> list[Block]:
        """Bloques que se muestran en la app (sin la fila de control)."""
        return [b for b in self.list_blocks() if not b.es_config]

    def _invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_time = 0.0

    # ---------- Liberación programada ----------

    def get_release_time(self) -> Optional[datetime]:
        """Fecha/hora de apertura definida en la fila ⚙️, o None si no existe."""
        for b in self.list_blocks():
            if b.es_config:
                return parse_local_dt(b.inicio, self.tz_name)
        return None

    def reservations_open(self) -> bool:
        release = self.get_release_time()
        return release is None or self.now() >= release

    def seconds_until_release(self) -> float:
        """Segundos que faltan para la apertura (<= 0 si ya abrió)."""
        release = self.get_release_time()
        if release is None:
            return 0.0
        return (release - self.now()).total_seconds()

    # ---------- Escrituras ----------

    def reserve(self, block_id: str, estudiante_1: str, estudiante_2: str = "") -> Block:
        """Reserva un bloque. Lanza:
        - ReservationsLockedError si la apertura aún no llega (control en Notion).
        - ValueError si faltan nombres según la modalidad.
        - BlockNotFoundError si el bloque no existe.
        - BlockUnavailableError si ya no está disponible (carrera perdida).
        """
        e1 = (estudiante_1 or "").strip()
        e2 = (estudiante_2 or "").strip()
        if not e1:
            raise ValueError("Debes ingresar al menos el primer nombre.")

        # Bloqueo real del lado del servidor (no solo visual)
        release = self.get_release_time()
        if release is not None and self.now() < release:
            raise ReservationsLockedError(release)

        with self._write_lock:  # serializa TODAS las escrituras
            try:
                page = self.client.get_page(block_id)
            except Exception as exc:
                raise BlockNotFoundError(f"Bloque no encontrado: {block_id}") from exc

            block = _parse_block(page)

            # Doble verificación contra Notion justo antes de escribir
            if block.es_config or not block.modalidad:
                raise BlockUnavailableError(
                    f"El bloque '{block.titulo}' no es reservable."
                )
            if block.estado != ESTADO_DISPONIBLE:
                raise BlockUnavailableError(
                    f"El bloque '{block.titulo}' ya no está disponible "
                    f"(estado actual: {block.estado})."
                )
            if block.modalidad == MODALIDAD_PAREJA and not e2:
                raise ValueError("Este bloque es en pareja: ingresa ambos nombres.")
            if block.modalidad == MODALIDAD_INDIVIDUAL:
                e2 = ""  # defensa individual: solo un nombre

            now_iso = datetime.now(timezone.utc).isoformat()
            properties = {
                PROP_ESTADO: {"select": {"name": ESTADO_RESERVADO}},
                PROP_EST1: {"rich_text": [{"text": {"content": e1}}]},
                PROP_EST2: {"rich_text": ([{"text": {"content": e2}}] if e2 else [])},
                PROP_FECHA_RESERVA: {"date": {"start": now_iso}},
            }
            updated = self.client.update_page(block_id, properties)
            self._invalidate_cache()
            return _parse_block(updated)
