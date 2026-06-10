"""
Tests del núcleo de reservas (sin red, sin Notion, sin Streamlit).

Ejecutar:  python3 tests/test_core.py
"""

import copy
import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from notion_store import (
    ESTADO_BLOQUEADO,
    ESTADO_DISPONIBLE,
    ESTADO_RESERVADO,
    MODALIDAD_INDIVIDUAL,
    MODALIDAD_PAREJA,
    BlockNotFoundError,
    BlockUnavailableError,
    ReservationService,
    ReservationsLockedError,
    parse_local_dt,
)

TZ_CL = ZoneInfo("America/Santiago")


def make_page(
    pid,
    titulo,
    estado,
    modalidad=None,
    e1="",
    e2="",
    inicio="2026-06-16T14:10:00.000Z",
    fin="2026-06-16T14:22:00.000Z",
):
    return {
        "id": pid,
        "properties": {
            "Bloque": {"title": [{"plain_text": titulo}]},
            "Estado": {"select": ({"name": estado} if estado else None)},
            "Modalidad": {"select": ({"name": modalidad} if modalidad else None)},
            "Horario": {"date": {"start": inicio, "end": fin}},
            "Estudiante 1": {"rich_text": ([{"plain_text": e1}] if e1 else [])},
            "Estudiante 2": {"rich_text": ([{"plain_text": e2}] if e2 else [])},
            "Notas": {"rich_text": []},
        },
    }


def make_config_page(release_iso):
    """Fila de control ⚙️ que define la fecha de liberación."""
    return make_page(
        "config",
        "⚙️ Liberación de reservas — editar solo la fecha",
        ESTADO_BLOQUEADO,
        inicio=release_iso,
        fin=None,
    )


class FakeNotionClient:
    """Simula la API de Notion en memoria (thread-safe)."""

    def __init__(self, pages):
        self.pages = {p["id"]: copy.deepcopy(p) for p in pages}
        self.query_count = 0
        self.update_count = 0
        self._lock = threading.Lock()

    def query_all_pages(self):
        with self._lock:
            self.query_count += 1
            return copy.deepcopy(list(self.pages.values()))

    def update_page(self, page_id, properties):
        with self._lock:
            self.update_count += 1
            page = self.pages[page_id]
            for name, value in properties.items():
                page["properties"][name] = copy.deepcopy(value)
            return copy.deepcopy(page)


def default_pages():
    return [
        make_page("b1", "B1 — Defensa Oral", ESTADO_DISPONIBLE),
        make_page(
            "b2",
            "B2 — Defensa Oral",
            ESTADO_RESERVADO,
            modalidad=MODALIDAD_PAREJA,
            e1="Ana Soto",
            e2="Luis Rojas",
            inicio="2026-06-16T14:22:00.000Z",
            fin="2026-06-16T14:34:00.000Z",
        ),
        make_page(
            "pausa",
            "⏸️ Pausa docente 1",
            ESTADO_BLOQUEADO,
            inicio="2026-06-16T14:58:00.000Z",
            fin="2026-06-16T15:08:00.000Z",
        ),
    ]


def make_service(pages=None, cache_ttl=60.0, now_fn=None):
    client = FakeNotionClient(pages if pages is not None else default_pages())
    service = ReservationService(client, cache_ttl=cache_ttl, now_fn=now_fn)
    return service, client


class TestReservasTribunal(unittest.TestCase):
    def test_listado_y_cache(self):
        service, client = make_service()
        a = service.visible_blocks()
        b = service.visible_blocks()
        self.assertEqual(len(a), 3)
        self.assertEqual(len(b), 3)
        self.assertEqual(client.query_count, 1, "La caché debe evitar la 2da consulta")

    def test_lecturas_concurrentes_no_estampida(self):
        """30 hilos pidiendo el listado a la vez → mínimas llamadas reales a Notion."""
        service, client = make_service()
        with ThreadPoolExecutor(max_workers=30) as ex:
            list(ex.map(lambda _: service.visible_blocks(), range(30)))
        self.assertLessEqual(client.query_count, 2)

    def test_reserva_pareja_exitosa(self):
        service, client = make_service()
        block = service.reserve("b1", "Camila Pérez", "Diego Muñoz")
        self.assertEqual(block.estado, ESTADO_RESERVADO)
        self.assertEqual(block.modalidad, MODALIDAD_PAREJA)
        self.assertEqual(block.estudiante_1, "Camila Pérez")
        self.assertEqual(block.estudiante_2, "Diego Muñoz")
        self.assertEqual(client.update_count, 1)

    def test_reserva_individual_con_un_nombre(self):
        """Cualquier bloque acepta modalidad individual (elegida al inscribirse)."""
        service, client = make_service()
        block = service.reserve("b1", "Valentina Ruiz", individual=True)
        self.assertEqual(block.estado, ESTADO_RESERVADO)
        self.assertEqual(block.modalidad, MODALIDAD_INDIVIDUAL)
        self.assertEqual(block.estudiante_1, "Valentina Ruiz")
        self.assertEqual(block.estudiante_2, "")
        self.assertEqual(client.update_count, 1)

    def test_individual_ignora_segundo_nombre(self):
        service, _ = make_service()
        block = service.reserve("b1", "Valentina Ruiz", "Texto basura", individual=True)
        self.assertEqual(block.estudiante_2, "")
        self.assertEqual(block.modalidad, MODALIDAD_INDIVIDUAL)

    def test_pareja_requiere_ambos_nombres(self):
        service, client = make_service()
        with self.assertRaises(ValueError):
            service.reserve("b1", "Camila Pérez", "")
        self.assertEqual(client.update_count, 0)

    def test_nombre_1_siempre_obligatorio(self):
        service, client = make_service()
        with self.assertRaises(ValueError):
            service.reserve("b1", "   ", "Diego Muñoz")
        with self.assertRaises(ValueError):
            service.reserve("b1", "", individual=True)
        self.assertEqual(client.update_count, 0)

    def test_no_reserva_pausa(self):
        service, client = make_service()
        with self.assertRaises(BlockUnavailableError):
            service.reserve("pausa", "Camila Pérez", "Diego Muñoz")
        self.assertEqual(client.update_count, 0)

    def test_no_reserva_ya_sellado(self):
        service, client = make_service()
        with self.assertRaises(BlockUnavailableError):
            service.reserve("b2", "Camila Pérez", "Diego Muñoz")
        self.assertEqual(client.update_count, 0)

    def test_bloque_inexistente(self):
        service, _ = make_service()
        with self.assertRaises(BlockNotFoundError):
            service.reserve("no-existe", "Camila Pérez", "Diego Muñoz")

    def test_carrera_30_escuadrones_un_ganador(self):
        """30 hilos contra el MISMO bloque → exactamente 1 gana, 1 escritura."""
        service, client = make_service()

        def intentar(i):
            try:
                return service.reserve("b1", f"Aspirante A{i}", f"Aspirante B{i}")
            except Exception as e:
                return e

        with ThreadPoolExecutor(max_workers=30) as ex:
            results = list(ex.map(intentar, range(30)))

        exitos = [r for r in results if not isinstance(r, Exception)]
        conflictos = [r for r in results if isinstance(r, BlockUnavailableError)]
        self.assertEqual(len(exitos), 1)
        self.assertEqual(len(conflictos), 29)
        self.assertEqual(client.update_count, 1)

    def test_invalida_cache_tras_reservar(self):
        service, client = make_service()
        service.visible_blocks()
        service.reserve("b1", "Camila Pérez", "Diego Muñoz")
        blocks = service.visible_blocks()
        b1 = next(b for b in blocks if b.id == "b1")
        self.assertEqual(b1.estado, ESTADO_RESERVADO)


class TestLiberacionProgramada(unittest.TestCase):
    """La fila ⚙️ en Notion controla cuándo se abren las reservas."""

    RELEASE_ISO = "2026-06-14T20:00:00.000Z"  # se interpreta LITERAL: 20:00 Chile

    def _service(self, now, with_config=True):
        pages = default_pages()
        if with_config:
            pages.append(make_config_page(self.RELEASE_ISO))
        return make_service(pages, now_fn=lambda: now)

    def test_parseo_literal_ignora_sufijo_tz(self):
        """'...20:00:00.000Z' debe interpretarse como 20:00 hora de Chile."""
        dt = parse_local_dt(self.RELEASE_ISO)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 6, 14))
        self.assertEqual((dt.hour, dt.minute), (20, 0))
        self.assertEqual(str(dt.tzinfo), "America/Santiago")

    def test_antes_de_la_apertura_bloqueado(self):
        now = datetime(2026, 6, 14, 19, 59, 0, tzinfo=TZ_CL)
        service, client = self._service(now)
        self.assertFalse(service.reservations_open())
        self.assertGreater(service.seconds_until_release(), 0)
        with self.assertRaises(ReservationsLockedError):
            service.reserve("b1", "Camila Pérez", "Diego Muñoz")
        self.assertEqual(client.update_count, 0, "No debe escribir antes de la apertura")

    def test_despues_de_la_apertura_funciona(self):
        now = datetime(2026, 6, 14, 20, 0, 1, tzinfo=TZ_CL)
        service, _ = self._service(now)
        self.assertTrue(service.reservations_open())
        block = service.reserve("b1", "Camila Pérez", "Diego Muñoz")
        self.assertEqual(block.estado, ESTADO_RESERVADO)

    def test_sin_fila_config_siempre_abierto(self):
        now = datetime(2026, 6, 1, 0, 0, 0, tzinfo=TZ_CL)
        service, _ = self._service(now, with_config=False)
        self.assertIsNone(service.get_release_time())
        self.assertTrue(service.reservations_open())
        block = service.reserve("b1", "Camila Pérez", "Diego Muñoz")
        self.assertEqual(block.estado, ESTADO_RESERVADO)

    def test_fila_config_no_es_visible_ni_reservable(self):
        now = datetime(2026, 6, 15, 0, 0, 0, tzinfo=TZ_CL)
        service, client = self._service(now)
        visibles = service.visible_blocks()
        self.assertTrue(all(not b.es_config for b in visibles))
        self.assertEqual(len(visibles), 3)
        with self.assertRaises(BlockUnavailableError):
            service.reserve("config", "Camila Pérez", "Diego Muñoz")
        self.assertEqual(client.update_count, 0)

    def test_carrera_en_el_segundo_exacto_de_apertura(self):
        """Justo a las 20:00:00, 30 escuadrones → igual solo 1 gana."""
        now = datetime(2026, 6, 14, 20, 0, 0, tzinfo=TZ_CL)
        service, client = self._service(now)

        def intentar(i):
            try:
                return service.reserve("b1", f"A{i}", f"B{i}")
            except Exception as e:
                return e

        with ThreadPoolExecutor(max_workers=30) as ex:
            results = list(ex.map(intentar, range(30)))

        exitos = [r for r in results if not isinstance(r, Exception)]
        self.assertEqual(len(exitos), 1)
        self.assertEqual(client.update_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
