"""
Tests de la lógica crítica (sin red, sin Streamlit): caché, validaciones y
—lo más importante— que dos reservas simultáneas sobre el mismo bloque
NUNCA resulten en doble reserva.

Streamlit ejecuta cada sesión de usuario en un hilo, por lo que las carreras se
simulan aquí con ThreadPoolExecutor (30 hilos reservando a la vez).

Ejecutar:  python3 tests/test_core.py
"""

import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notion_store import (
    ESTADO_BLOQUEADO,
    ESTADO_DISPONIBLE,
    ESTADO_RESERVADO,
    BlockUnavailableError,
    ReservationService,
)


def make_page(page_id: str, titulo: str, estado: str, est1: str = "", est2: str = ""):
    """Construye una página con el mismo shape que devuelve la API de Notion."""
    return {
        "id": page_id,
        "properties": {
            "Bloque": {"title": [{"plain_text": titulo}]},
            "Estado": {"select": {"name": estado}},
            "Horario": {"date": {"start": "2026-06-15T10:00:00", "end": "2026-06-15T10:30:00"}},
            "Estudiante 1": {"rich_text": [{"plain_text": est1}] if est1 else []},
            "Estudiante 2": {"rich_text": [{"plain_text": est2}] if est2 else []},
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
        for key in ("Estudiante 1", "Estudiante 2"):
            if key in properties:
                texts = properties[key]["rich_text"]
                props[key] = {"rich_text": [{"plain_text": t["text"]["content"]} for t in texts]}
        with self._counter_lock:
            self.update_count += 1
        return page


class TestReservas(unittest.TestCase):
    def setUp(self):
        self.pages = {
            "b1": make_page("b1", "Bloque 01", ESTADO_DISPONIBLE),
            "b2": make_page("b2", "Bloque 02", ESTADO_BLOQUEADO),
            "b3": make_page("b3", "Bloque 03", ESTADO_RESERVADO, "Ana", "Luis"),
        }
        self.client = FakeNotionClient(self.pages)
        self.service = ReservationService(self.client, cache_ttl=5.0)

    def test_listado_y_cache(self):
        blocks1 = self.service.list_blocks()
        blocks2 = self.service.list_blocks()  # debe venir del caché
        self.assertEqual(len(blocks1), 3)
        self.assertEqual(self.client.query_count, 1, "La segunda lectura debe usar caché")
        self.assertIs(blocks1, blocks2)

    def test_lecturas_concurrentes_no_estampida(self):
        """30 hilos pidiendo el listado a la vez → mínimas llamadas reales a Notion."""
        with ThreadPoolExecutor(max_workers=30) as ex:
            results = list(ex.map(lambda _: self.service.list_blocks(), range(30)))
        self.assertTrue(all(len(r) == 3 for r in results))
        self.assertLessEqual(
            self.client.query_count, 2,
            "El caché + lock debe evitar la estampida de consultas",
        )

    def test_reserva_exitosa(self):
        block = self.service.reserve("b1", "Camila Pérez", "Diego Rojas")
        self.assertEqual(block.estado, ESTADO_RESERVADO)
        self.assertEqual(block.estudiante_1, "Camila Pérez")
        self.assertEqual(block.estudiante_2, "Diego Rojas")

    def test_no_reserva_bloqueado(self):
        with self.assertRaises(BlockUnavailableError):
            self.service.reserve("b2", "Camila", "Diego")
        self.assertEqual(self.client.update_count, 0, "No debe escribir nada")

    def test_no_reserva_ya_reservado(self):
        with self.assertRaises(BlockUnavailableError):
            self.service.reserve("b3", "Camila", "Diego")
        self.assertEqual(self.client.update_count, 0)

    def test_nombres_obligatorios(self):
        with self.assertRaises(ValueError):
            self.service.reserve("b1", "  ", "Diego")

    def test_carrera_30_reservas_simultaneas_mismo_bloque(self):
        """EL TEST CLAVE: 30 parejas (hilos) intentan reservar el mismo bloque a la
        vez. Exactamente UNA debe ganar; las otras 29 reciben BlockUnavailableError."""

        def intentar(i):
            try:
                return self.service.reserve("b1", f"Estudiante A{i}", f"Estudiante B{i}")
            except Exception as e:
                return e

        with ThreadPoolExecutor(max_workers=30) as ex:
            results = list(ex.map(intentar, range(30)))

        exitos = [r for r in results if not isinstance(r, Exception)]
        conflictos = [r for r in results if isinstance(r, BlockUnavailableError)]
        self.assertEqual(len(exitos), 1, "Exactamente una pareja debe ganar el bloque")
        self.assertEqual(len(conflictos), 29, "Las demás deben recibir conflicto")
        self.assertEqual(self.client.update_count, 1, "Solo debe haber UNA escritura en Notion")

    def test_carrera_bloques_distintos_todas_ganan(self):
        """Reservas simultáneas sobre bloques DISTINTOS deben funcionar todas."""
        self.pages["b4"] = make_page("b4", "Bloque 04", ESTADO_DISPONIBLE)

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(self.service.reserve, "b1", "P1A", "P1B")
            f2 = ex.submit(self.service.reserve, "b4", "P2A", "P2B")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
