"""
Backend de reservas — Reservas Tribunal Endovascular · TMED732 UNAB 2026.

- Cliente mínimo de la API de Notion (httpx se importa de forma diferida para
  que los tests corran sin dependencias externas).
- ReservationService thread-safe: caché con TTL (protege a Notion de la
  estampida de 30 estudiantes), lock global anti doble-reserva y
  re-verificación contra Notion antes de escribir.
- Liberación programada: la fila cuyo título comienza con ⚙️ define la
  fecha/hora de apertura. Se interpreta LITERALMENTE como hora local
  (America/Santiago por defecto), tal como se ve escrita en Notion.
- La modalidad (Pareja / Individual) la elige el estudiante AL RESERVAR:
  todos los bloques son genéricos y dan la misma oportunidad.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

NOTION_VERSION = "2022-06-28"

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

# Las filas cuyo título comienza así son de CONTROL (fecha de liberación):
# nunca se muestran en la app ni pueden reservarse.
CONFIG_PREFIX = "⚙️"

DEFAULT_TZ = "America/Santiago"


class BlockNotFoundError(Exception):
    """El bloque solicitado no existe en la base."""


class BlockUnavailableError(Exception):
    """El bloque ya fue reservado o está bloqueado por el docente."""


class ReservationsLockedError(Exception):
    """Aún no llega la fecha/hora de liberación definida en Notion."""

    def __init__(self, release_dt: datetime):
        self.release_dt = release_dt
        super().__init__(
            "Las reservas abren el "
            f"{release_dt.strftime('%d-%m-%Y a las %H:%M')} (hora de Chile)."
        )


def parse_local_dt(iso: Optional[str], tz_name: str = DEFAULT_TZ) -> Optional[datetime]:
    """Interpreta un datetime de Notion de forma LITERAL como hora local.

    '2026-06-14T20:00:00.000Z' -> 14-jun-2026 20:00 hora de Chile.
    Se ignoran milisegundos y cualquier sufijo Z/offset: lo que el docente ve
    escrito en Notion es exactamente lo que vale.
    """
    if not iso:
        return None
    raw = iso[:19]  # 'YYYY-MM-DDTHH:MM:SS' (o más corto si es solo fecha)
    if len(raw) == 10:
        raw += "T00:00:00"
    return datetime.fromisoformat(raw).replace(tzinfo=ZoneInfo(tz_name))


@dataclass
class Block:
    id: str
    titulo: str
    estado: str
    modalidad: str
    estudiante_1: str
    estudiante_2: str
    inicio: Optional[str]
    fin: Optional[str]
    notas: str = ""

    @property
    def es_config(self) -> bool:
        return self.titulo.startswith(CONFIG_PREFIX)

    @property
    def reservable(self) -> bool:
        return (not self.es_config) and self.estado == ESTADO_DISPONIBLE

    @property
    def es_individual(self) -> bool:
        return self.modalidad == MODALIDAD_INDIVIDUAL


# ───────────────────── Parseo de páginas de Notion ──────────────────


def _texts(segments) -> str:
    out = []
    for seg in segments or []:
        out.append(seg.get("plain_text") or seg.get("text", {}).get("content", "") or "")
    return "".join(out).strip()


def _select(prop) -> str:
    sel = (prop or {}).get("select") or {}
    return sel.get("name", "") if isinstance(sel, dict) else ""


def _parse_page(page: dict) -> Block:
    props = page.get("properties", {})
    date = (props.get(PROP_HORARIO) or {}).get("date") or {}
    return Block(
        id=page["id"],
        titulo=_texts((props.get(PROP_TITULO) or {}).get("title")),
        estado=_select(props.get(PROP_ESTADO)),
        modalidad=_select(props.get(PROP_MODALIDAD)),
        estudiante_1=_texts((props.get(PROP_EST1) or {}).get("rich_text")),
        estudiante_2=_texts((props.get(PROP_EST2) or {}).get("rich_text")),
        inicio=date.get("start"),
        fin=date.get("end"),
        notas=_texts((props.get(PROP_NOTAS) or {}).get("rich_text")),
    )


# ───────────────────── Cliente HTTP de Notion ─────────────────────


class NotionClient:
    BASE = "https://api.notion.com"

    def __init__(self, token: str, database_id: str, timeout: float = 15.0):
        self._token = token
        self._database_id = database_id
        self._timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def query_all_pages(self) -> list:
        import httpx  # diferido: los tests no lo necesitan

        results: list = []
        payload: dict = {"page_size": 100}
        with httpx.Client(timeout=self._timeout) as client:
            while True:
                r = client.post(
                    f"{self.BASE}/v1/databases/{self._database_id}/query",
                    headers=self._headers(),
                    json=payload,
                )
                r.raise_for_status()
                data = r.json()
                results.extend(data.get("results", []))
                if not data.get("has_more"):
                    return results
                payload["start_cursor"] = data["next_cursor"]

    def update_page(self, page_id: str, properties: dict) -> dict:
        import httpx

        with httpx.Client(timeout=self._timeout) as client:
            r = client.patch(
                f"{self.BASE}/v1/pages/{page_id}",
                headers=self._headers(),
                json={"properties": properties},
            )
            r.raise_for_status()
            return r.json()


# ───────────────────── Servicio de reservas ───────────────────────


class ReservationService:
    """Instancia única compartida entre sesiones de Streamlit.

    - Caché TTL + lock de fetch: 30 lectores simultáneos generan ~1 llamada
      real a Notion.
    - Lock global de reserva + re-verificación: imposible la doble reserva.
    - now_fn inyectable para testear la liberación programada.
    """

    def __init__(
        self,
        client,
        cache_ttl: float = 5.0,
        tz_name: str = DEFAULT_TZ,
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self._client = client
        self._ttl = float(cache_ttl)
        self._tz_name = tz_name
        self._tz = ZoneInfo(tz_name)
        self._now_fn = now_fn
        self._cache: Optional[list] = None
        self._cache_expiry = 0.0
        self._cache_lock = threading.Lock()
        self._fetch_lock = threading.Lock()
        self._reserve_lock = threading.Lock()

    # ───── reloj ─────

    def now(self) -> datetime:
        base = self._now_fn() if self._now_fn else datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=self._tz)
        return base.astimezone(self._tz)

    # ───── lecturas ─────

    def list_blocks(self) -> list:
        """Todas las filas (incluida la ⚙️ de control), con caché TTL."""
        with self._cache_lock:
            if self._cache is not None and time.monotonic() < self._cache_expiry:
                return self._cache
        with self._fetch_lock:  # anti-estampida: un solo hilo consulta Notion
            with self._cache_lock:
                if self._cache is not None and time.monotonic() < self._cache_expiry:
                    return self._cache
            pages = self._client.query_all_pages()
            blocks = sorted(
                (_parse_page(p) for p in pages), key=lambda b: b.inicio or ""
            )
            with self._cache_lock:
                self._cache = blocks
                self._cache_expiry = time.monotonic() + self._ttl
            return blocks

    def visible_blocks(self) -> list:
        """Las filas que se muestran en la app (sin la fila ⚙️ de control)."""
        return [b for b in self.list_blocks() if not b.es_config]

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_expiry = 0.0

    # ───── liberación programada ─────

    def get_release_time(self) -> Optional[datetime]:
        """Fecha/hora de apertura definida en la fila ⚙️ (None = sin restricción)."""
        for b in self.list_blocks():
            if b.es_config and b.inicio:
                return parse_local_dt(b.inicio, self._tz_name)
        return None

    def reservations_open(self) -> bool:
        release = self.get_release_time()
        return release is None or self.now() >= release

    def seconds_until_release(self) -> float:
        release = self.get_release_time()
        if release is None:
            return 0.0
        return (release - self.now()).total_seconds()

    def _ensure_open(self) -> None:
        release = self.get_release_time()
        if release is not None and self.now() < release:
            raise ReservationsLockedError(release)

    # ───── reserva ─────

    def reserve(
        self,
        block_id: str,
        estudiante_1: str,
        estudiante_2: str = "",
        individual: bool = False,
    ) -> Block:
        """Reserva atómica. La modalidad la decide el estudiante:

        - individual=False (pareja): exige ambos nombres → Modalidad 'Pareja'.
        - individual=True: exige solo un nombre → Modalidad 'Individual'.
        """
        e1 = (estudiante_1 or "").strip()
        e2 = "" if individual else (estudiante_2 or "").strip()
        if not e1:
            raise ValueError("El nombre del Aspirante 1 es obligatorio.")
        if not individual and not e2:
            raise ValueError(
                "La defensa en pareja requiere ambos nombres. Si te presentas "
                "sin pareja, marca la opción “individual”."
            )

        self._ensure_open()  # bloqueo real en servidor (no solo visual)

        with self._reserve_lock:
            self._ensure_open()
            # Re-verificación con datos FRESCOS de Notion (sin caché)
            fresh = [_parse_page(p) for p in self._client.query_all_pages()]
            block = next((b for b in fresh if b.id == block_id), None)
            if block is None:
                raise BlockNotFoundError(block_id)
            if not block.reservable:
                raise BlockUnavailableError(block.titulo or block_id)

            modalidad = MODALIDAD_INDIVIDUAL if individual else MODALIDAD_PAREJA
            properties = {
                PROP_ESTADO: {"select": {"name": ESTADO_RESERVADO}},
                PROP_MODALIDAD: {"select": {"name": modalidad}},
                PROP_EST1: {"rich_text": [{"text": {"content": e1}}]},
                PROP_EST2: {"rich_text": ([{"text": {"content": e2}}] if e2 else [])},
                PROP_FECHA_RESERVA: {"date": {"start": self.now().isoformat()}},
            }
            updated = self._client.update_page(block.id, properties)
            self.invalidate_cache()
            return _parse_page(updated)
