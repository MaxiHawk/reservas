"""
Tests de la lógica crítica (sin red, sin Streamlit): caché, validaciones y
—lo más importante— que dos reservas simultáneas sobre el mismo bloque NUNCA
resulten en doble reserva.

Reglas vigentes:
  - TODAS las reservas son en PAREJA: se exigen 2 nombres.
  - Un bloque es reservable solo si está Disponible (no config, no Bloqueado).
  - Los bloques Bloqueados (apertura, pausas, cierre, o reservados a mano por el
    docente — p.ej. la defensa individual) NO se pueden reservar.

Streamlit ejecuta cada sesión de usuario en un hilo, por lo que las carreras se
simulan con ThreadPoolExecutor (30 hilos reservando a la vez).

Ejecutar:  python3 tests/test_core.py
"""

import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime
from zoneinfo import ZoneInfo

from notion_store import (
    ESTADO_BLOQUEADO,
    ESTADO_DISPONIBLE,
    ESTADO_RESERVADO,
    MODALIDAD_PAREJA,
    BlockUnavailableError,
    ReservationService,
    ReservationsLockedError,
    parse_local_dt,
)

TZ_CL = ZoneInfo("America/Santiago")


def make_config_page(release_iso: str):
    """Fila de control ⚙️ que define la fecha de liberacion."""
    return {
        "id": "config",
        "properties": {
            "Bloque": {"title": [{"plain_text": "⚙️ Liberación de reservas"}]},
            "Estado": {"select": {"name": ESTADO_BLOQUEADO}},
            "Modalidad": {"select": None},
            "Horario": {"date": {"start": release_iso, "end": None}},
            "Estudiante 1": {"rich_text": []},
            "Estudiante 2": {"rich_text": []},
            "Notas": {"rich_text": []},
        },
    }


def make_page(
    page_id: str,
    titulo: str,
    estado: str,
    modalidad: str = "",
    est1: str = "",
    est2: str = "",
):
    """Construye una página con el mismo shape que devuelve la API de Notion."""
    return {
        "id": page_id,
        "properties": {
            "Bloque": {"title": [{"plain_text": titulo}]},
            "Estado": {"select": {"name": estado}},
            "Modalidad": {"select": {"name": modalidad} if modalidad else None},
            "Horario": {"date": {"start": "2026-06-16T14:06:00", "end": "2026-06-16T14:16:00"}},
            "Estudiante 1": {"rich_text": [{"plain_text": est1}] if est1 else []},
            "Estudiante 2": {"rich_text": [{"plain_text": est2}] if est2 else []},
            "Notas": {"rich_text": []},
        },
    }


class FakeNotionClient:
    """Simula la API de Notion con latencia artificial para forzar carreras."""

    def __init__(self, pages: dict, latency: float = 0.01):
        self.pages = pages
        self.latency = latency
        self.query_count = 0
        self.update_count = 0
        self._counter_lock = threading.Lock()

    def query_database(self):
        with self._counter_lock:
            self.query_count += 1
        time.sleep(self.latency)
        return list(self.pages.values())

    def get_page(self, page_id):
        time.sleep(self.latency)
        if page_id not in self.pages:
            raise Exception("404 not found")
        return self.pages[page_id]

    def update_page(self, page_id, properties):
        # Simula la escritura NO atómica de Notion (read-modify-write)
        time.sleep(self.latency)
        page = self.pages[page_id]
        props = page["properties"]
        if "Estado" in properties:
            props["Estado"] = {"select": {"name": properties["Estado"]["select"]["name"]}}
        if "Modalidad" in properties and properties["Modalidad"].get("select"):
            props["Modalidad"] = {"select": {"name": properties["Modalidad"]["select"]["name"]}}
        for key in ("Estudiante 1", "Estudiante 2"):
            if key in properties:
                texts = properties[key]["rich_text"]
                props[key] = {"rich_text": [{"plain_text": t["text"]["content"]} for t in texts]}
        with self._counter_lock:
            self.update_count += 1
        return page


class TestReservasTribunal(unittest.TestCase):
    def setUp(self):
        self.pages = {
            "b1": make_page("b1", "B1 — Defensa Oral", ESTADO_DISPONIBLE),
            "pausa": make_page("pausa", "Pausa docente ☕ (1)", ESTADO_BLOQUEADO),
            "b2": make_page("b2", "B2 — Defensa Oral", ESTADO_RESERVADO, est1="Ana", est2="Luis"),
            "b4": make_page("b4", "B4 — Defensa Oral", ESTADO_BLOQUEADO),  # reservado a mano (individual)
        }
        self.client = FakeNotionClient(self.pages)
        self.service = ReservationService(self.client, cache_ttl=5.0)

    # ---------- Lecturas / caché ----------

    def test_listado_y_cache(self):
        blocks1 = self.service.list_blocks()
        blocks2 = self.service.list_blocks()  # debe venir del caché
        self.assertEqual(len(blocks1), 4)
        self.assertEqual(self.client.query_count, 1, "La segunda lectura debe usar caché")
        self.assertIs(blocks1, blocks2)

    def test_lecturas_concurrentes_no_estampida(self):
        """30 hilos pidiendo el listado a la vez → mínimas llamadas reales a Notion."""
        with ThreadPoolExecutor(max_workers=30) as ex:
            results = list(ex.map(lambda _: self.service.list_blocks(), range(30)))
        self.assertTrue(all(len(r) == 4 for r in results))
        self.assertLessEqual(
            self.client.query_count, 2,
            "El caché + lock debe evitar la estampida de consultas",
        )

    def test_reservable_solo_si_disponible(self):
        blocks = {b.id: b for b in self.service.list_blocks()}
        self.assertTrue(blocks["b1"].reservable, "Un bloque Disponible es reservable")
        self.assertFalse(blocks["pausa"].reservable, "Las pausas nunca son reservables")
        self.assertFalse(blocks["b2"].reservable, "Un bloque sellado no es reservable")
        self.assertFalse(blocks["b4"].reservable, "Un bloque bloqueado por el docente no es reservable")

    # ---------- Reservas (solo parejas) ----------

    def test_reserva_pareja_exitosa(self):
        block = self.service.reserve("b1", "Camila Pérez", "Diego Rojas")
        self.assertEqual(block.estado, ESTADO_RESERVADO)
        self.assertEqual(block.estudiante_1, "Camila Pérez")
        self.assertEqual(block.estudiante_2, "Diego Rojas")
        self.assertEqual(block.modalidad, MODALIDAD_PAREJA, "Toda reserva queda como Pareja")

    def test_requiere_ambos_nombres(self):
        with self.assertRaises(ValueError):
            self.service.reserve("b1", "Camila Pérez", "")
        with self.assertRaises(ValueError):
            self.service.reserve("b1", "", "Diego Rojas")
        self.assertEqual(self.client.update_count, 0, "No debe escribir nada")

    def test_nombres_solo_espacios_invalidos(self):
        with self.assertRaises(ValueError):
            self.service.reserve("b1", "   ", "Diego")

    def test_no_reserva_pausa(self):
        with self.assertRaises(BlockUnavailableError):
            self.service.reserve("pausa", "Camila", "Diego")
        self.assertEqual(self.client.update_count, 0)

    def test_no_reserva_bloque_bloqueado_por_docente(self):
        """B4 (reservado a mano para la defensa individual) no debe poder tomarse."""
        with self.assertRaises(BlockUnavailableError):
            self.service.reserve("b4", "Camila", "Diego")
        self.assertEqual(self.client.update_count, 0)

    def test_no_reserva_ya_sellado(self):
        with self.assertRaises(BlockUnavailableError):
            self.service.reserve("b2", "Camila", "Diego")
        self.assertEqual(self.client.update_count, 0)

    # ---------- Carreras (lo crítico) ----------

    def test_carrera_30_reservas_simultaneas_mismo_bloque(self):
        """EL TEST CLAVE: 30 escuadrones (hilos) intentan sellar el mismo bloque a
        la vez. Exactamente UNO gana; los otros 29 reciben BlockUnavailableError."""

        def intentar(i):
            try:
                return self.service.reserve("b1", f"Aspirante A{i}", f"Aspirante B{i}")
            except Exception as e:
                return e

        with ThreadPoolExecutor(max_workers=30) as ex:
            results = list(ex.map(intentar, range(30)))

        exitos = [r for r in results if not isinstance(r, Exception)]
        conflictos = [r for r in results if isinstance(r, BlockUnavailableError)]
        self.assertEqual(len(exitos), 1, "Exactamente un escuadrón debe ganar el bloque")
        self.assertEqual(len(conflictos), 29, "Los demás deben recibir conflicto")
        self.assertEqual(self.client.update_count, 1, "Solo debe haber UNA escritura en Notion")

    def test_carrera_bloques_distintos_todas_ganan(self):
        """Reservas simultáneas sobre bloques DISTINTOS deben funcionar todas."""
        self.pages["b3"] = make_page("b3", "B3 — Defensa Oral", ESTADO_DISPONIBLE)
        self.service.list_blocks(force_refresh=True)
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(self.service.reserve, "b1", "P1A", "P1B")
            f2 = ex.submit(self.service.reserve, "b3", "P3A", "P3B")
            r1, r2 = f1.result(), f2.result()

        self.assertEqual(r1.estado, ESTADO_RESERVADO)
        self.assertEqual(r2.estado, ESTADO_RESERVADO)
        self.assertEqual(self.client.update_count, 2)

    def test_cache_se_invalida_tras_reserva(self):
        self.service.list_blocks()
        self.service.reserve("b1", "Camila", "Diego")
        blocks = self.service.list_blocks()  # debe refrescar
        b1 = next(b for b in blocks if b.id == "b1")
        self.assertEqual(b1.estado, ESTADO_RESERVADO)
        self.assertEqual(self.client.query_count, 2)


class TestLiberacionProgramada(unittest.TestCase):
    """La fila ⚙️ en Notion controla cuando se abren las reservas."""

    RELEASE_ISO = "2026-06-13T20:00:00.000Z"  # se interpreta LITERAL: sábado 20:00 Chile

    def _service(self, now: datetime, with_config: bool = True):
        pages = {"b1": make_page("b1", "B1 — Defensa Oral", ESTADO_DISPONIBLE)}
        if with_config:
            pages["config"] = make_config_page(self.RELEASE_ISO)
        client = FakeNotionClient(pages)
        service = ReservationService(client, cache_ttl=5.0, now_fn=lambda: now)
        return service, client

    def test_parseo_literal_ignora_sufijo_tz(self):
        """'...20:00:00.000Z' debe interpretarse como 20:00 hora de Chile."""
        dt = parse_local_dt(self.RELEASE_ISO)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 6, 13))
        self.assertEqual((dt.hour, dt.minute), (20, 0))
        self.assertEqual(str(dt.tzinfo), "America/Santiago")

    def test_antes_de_la_apertura_bloqueado(self):
        now = datetime(2026, 6, 13, 19, 59, 0, tzinfo=TZ_CL)
        service, client = self._service(now)
        self.assertFalse(service.reservations_open())
        self.assertGreater(service.seconds_until_release(), 0)
        with self.assertRaises(ReservationsLockedError):
            service.reserve("b1", "Camila", "Diego")
        self.assertEqual(client.update_count, 0, "No debe escribir antes de la apertura")

    def test_despues_de_la_apertura_funciona(self):
        now = datetime(2026, 6, 13, 20, 0, 1, tzinfo=TZ_CL)
        service, _ = self._service(now)
        self.assertTrue(service.reservations_open())
        block = service.reserve("b1", "Camila", "Diego")
        self.assertEqual(block.estado, ESTADO_RESERVADO)

    def test_sin_fila_config_siempre_abierto(self):
        now = datetime(2026, 6, 1, 0, 0, 0, tzinfo=TZ_CL)
        service, _ = self._service(now, with_config=False)
        self.assertIsNone(service.get_release_time())
        self.assertTrue(service.reservations_open())
        block = service.reserve("b1", "Camila", "Diego")
        self.assertEqual(block.estado, ESTADO_RESERVADO)

    def test_fila_config_no_es_reservable_ni_visible(self):
        now = datetime(2026, 6, 15, 0, 0, 0, tzinfo=TZ_CL)
        service, client = self._service(now)
        visibles = service.visible_blocks()
        self.assertTrue(all(not b.es_config for b in visibles))
        self.assertEqual(len(visibles), 1)
        with self.assertRaises(BlockUnavailableError):
            service.reserve("config", "Camila", "Diego")
        self.assertEqual(client.update_count, 0)

    def test_carrera_en_el_segundo_exacto_de_apertura(self):
        """Justo a las 20:00:00, 30 escuadrones → igual solo 1 gana."""
        now = datetime(2026, 6, 13, 20, 0, 0, tzinfo=TZ_CL)
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
