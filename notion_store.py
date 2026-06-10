"""
Capa de datos: cliente de la API de Notion + lógica de reservas thread-safe.

Streamlit atiende cada sesión en un hilo dentro de UN solo proceso, por lo que
un threading.Lock global (compartido vía @st.cache_resource en app.py) basta
para serializar las escrituras y garantizar que un bloque no se reserve dos veces.

Soporta dos modalidades de bloque (propiedad "Modalidad" en Notion):
  - Pareja: requiere 2 nombres.
  - Individual: requiere 1 nombre (defensa individual, curso impar).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

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


class BlockNotFoundError(Exception):
    """El bloque no existe en la base de datos."""


class BlockUnavailableError(Exception):
    """El bloque ya no está disponible (reservado o bloqueado)."""


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
    def reservable(self) -> bool:
        return self.estado == ESTADO_DISPONIBLE and self.modalidad in (
            MODALIDAD_PAREJA,
            MODALIDAD_INDIVIDUAL,
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
    """Lógica de reservas con caché de lecturas y lock global de escrituras."""

    def __init__(self, client, cache_ttl: float = 5.0):
        self.client = client
        self.cache_ttl = cache_ttl
        self._write_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache: Optional[list[Block]] = None
        self._cache_time: float = 0.0

    # ---------- Lecturas ----------

    def list_blocks(self, force_refresh: bool = False) -> list[Block]:
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

    def _invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache = None
            self._cache_time = 0.0

    # ---------- Escrituras ----------

    def reserve(self, block_id: str, estudiante_1: str, estudiante_2: str = "") -> Block:
        """Reserva un bloque. Lanza:
        - ValueError si faltan nombres según la modalidad.
        - BlockNotFoundError si el bloque no existe.
        - BlockUnavailableError si ya no está disponible (carrera perdida).
        """
        e1 = (estudiante_1 or "").strip()
        e2 = (estudiante_2 or "").strip()
        if not e1:
            raise ValueError("Debes ingresar al menos el primer nombre.")

        with self._write_lock:  # serializa TODAS las escrituras
            try:
                page = self.client.get_page(block_id)
            except Exception as exc:
                raise BlockNotFoundError(f"Bloque no encontrado: {block_id}") from exc

            block = _parse_block(page)

            # Doble verificación contra Notion justo antes de escribir
            if block.estado != ESTADO_DISPONIBLE:
                raise BlockUnavailableError(
                    f"El bloque '{block.titulo}' ya no está disponible "
                    f"(estado actual: {block.estado})."
                )
            if not block.modalidad:
                raise BlockUnavailableError(
                    f"El bloque '{block.titulo}' no es reservable."
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
