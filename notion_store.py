"""
Capa de datos: cliente de la API de Notion + servicio de reservas (versión sync
para Streamlit).

Diseño anti-condición-de-carrera:
- Streamlit ejecuta cada sesión de usuario en un HILO dentro de UN proceso.
- Todas las escrituras pasan por un threading.Lock global (cola única).
- Antes de escribir se re-verifica el estado real del bloque en Notion.
- Las lecturas usan un caché en memoria con TTL para no saturar la API de
  Notion (~3 req/s) aunque 30+ estudiantes consulten a la vez.

IMPORTANTE: el servicio debe instanciarse UNA sola vez para todo el proceso
(en Streamlit: con @st.cache_resource), para que el lock sea compartido.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

NOTION_VERSION = "2022-06-28"

# Nombres de propiedades en la base de datos de Notion
PROP_TITULO = "Bloque"
PROP_HORARIO = "Horario"
PROP_ESTADO = "Estado"
PROP_EST1 = "Estudiante 1"
PROP_EST2 = "Estudiante 2"
PROP_FECHA_RESERVA = "Fecha de reserva"

ESTADO_DISPONIBLE = "Disponible"
ESTADO_RESERVADO = "Reservado"
ESTADO_BLOQUEADO = "Bloqueado"


class BlockNotFoundError(Exception):
    """El bloque no existe en la base de datos."""


class BlockUnavailableError(Exception):
    """El bloque ya no está disponible (reservado o bloqueado)."""

    def __init__(self, estado: str):
        self.estado = estado
        super().__init__(f"Bloque no disponible (estado actual: {estado})")


@dataclass
class Block:
    id: str
    titulo: str
    estado: str
    inicio: Optional[str] = None
    fin: Optional[str] = None
    estudiante_1: str = ""
    estudiante_2: str = ""

    @property
    def disponible(self) -> bool:
        return self.estado == ESTADO_DISPONIBLE

    @property
    def reservado_por(self) -> Optional[str]:
        if self.estado != ESTADO_RESERVADO:
            return None
        return f"{self.estudiante_1} / {self.estudiante_2}".strip(" /")


def _parse_block(page: dict[str, Any]) -> Block:
    """Convierte una página de la API de Notion en un Block."""
    props = page.get("properties", {})

    def plain_text(items: list[dict]) -> str:
        return "".join(i.get("plain_text", "") for i in items)

    titulo = plain_text(props.get(PROP_TITULO, {}).get("title", []) or [])
    estado_obj = props.get(PROP_ESTADO, {}).get("select") or {}
    estado = estado_obj.get("name", ESTADO_BLOQUEADO)
    fecha = props.get(PROP_HORARIO, {}).get("date") or {}
    est1 = plain_text(props.get(PROP_EST1, {}).get("rich_text", []) or [])
    est2 = plain_text(props.get(PROP_EST2, {}).get("rich_text", []) or [])

    return Block(
        id=page["id"],
        titulo=titulo,
        estado=estado,
        inicio=fecha.get("start"),
        fin=fecha.get("end"),
        estudiante_1=est1,
        estudiante_2=est2,
    )


class NotionClient:
    """Cliente HTTP real (síncrono) contra la API pública de Notion."""

    def __init__(self, token: str, database_id: str):
        import httpx  # import local: los tests no requieren httpx

        self._database_id = database_id
        self._http = httpx.Client(
            base_url="https://api.notion.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def query_database(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        payload: dict[str, Any] = {"page_size": 100}
        while True:
            r = self._http.post(
                f"/v1/databases/{self._database_id}/query", json=payload
            )
            r.raise_for_status()
            data = r.json()
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            payload["start_cursor"] = data["next_cursor"]
        return results

    def get_page(self, page_id: str) -> dict[str, Any]:
        r = self._http.get(f"/v1/pages/{page_id}")
        r.raise_for_status()
        return r.json()

    def update_page(self, page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        r = self._http.patch(f"/v1/pages/{page_id}", json={"properties": properties})
        r.raise_for_status()
        return r.json()


@dataclass
class _Cache:
    blocks: list[Block] = field(default_factory=list)
    fetched_at: float = 0.0


class ReservationService:
    """Lógica de negocio: listado con caché + reserva serializada (thread-safe)."""

    def __init__(self, client, cache_ttl: float = 5.0):
        self._client = client
        self._cache_ttl = cache_ttl
        self._cache = _Cache()
        self._cache_lock = threading.Lock()    # evita estampida de refrescos
        self._reserve_lock = threading.Lock()  # serializa TODAS las escrituras

    # ---------- Lectura ----------

    def list_blocks(self, force_refresh: bool = False) -> list[Block]:
        now = time.monotonic()
        if not force_refresh and (now - self._cache.fetched_at) < self._cache_ttl:
            return self._cache.blocks
        with self._cache_lock:
            # double-check: otro hilo pudo haber refrescado mientras esperábamos
            now = time.monotonic()
            if not force_refresh and (now - self._cache.fetched_at) < self._cache_ttl:
                return self._cache.blocks
            pages = self._client.query_database()
            blocks = [_parse_block(p) for p in pages]
            blocks.sort(key=lambda b: (b.inicio or "", b.titulo))
            self._cache = _Cache(blocks=blocks, fetched_at=time.monotonic())
            return self._cache.blocks

    # ---------- Escritura ----------

    def reserve(self, block_id: str, estudiante_1: str, estudiante_2: str) -> Block:
        """Reserva un bloque. Lanza BlockUnavailableError si ya no está disponible.

        Toda la operación (verificar + escribir) ocurre dentro de un lock global:
        dos reservas simultáneas sobre el mismo bloque se procesan en serie.
        La primera gana; la segunda recibe el error.
        """
        estudiante_1 = estudiante_1.strip()
        estudiante_2 = estudiante_2.strip()
        if not estudiante_1 or not estudiante_2:
            raise ValueError("Se requieren los nombres de ambos integrantes.")

        with self._reserve_lock:
            # 1) Releer el estado REAL desde Notion (no desde el caché)
            try:
                page = self._client.get_page(block_id)
            except Exception as exc:  # 404 u otros
                raise BlockNotFoundError(str(exc)) from exc

            block = _parse_block(page)
            if not block.disponible:
                raise BlockUnavailableError(block.estado)

            # 2) Escribir la reserva
            now_iso = datetime.now(timezone.utc).isoformat()
            updated = self._client.update_page(
                block_id,
                {
                    PROP_ESTADO: {"select": {"name": ESTADO_RESERVADO}},
                    PROP_EST1: {"rich_text": [{"text": {"content": estudiante_1}}]},
                    PROP_EST2: {"rich_text": [{"text": {"content": estudiante_2}}]},
                    PROP_FECHA_RESERVA: {"date": {"start": now_iso}},
                },
            )

            # 3) Invalidar caché para que el resto vea el cambio de inmediato
            self._cache.fetched_at = 0.0
            return _parse_block(updated)
