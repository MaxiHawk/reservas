"""
Tests de la lógica crítica (sin red, sin Streamlit): caché, validaciones,
modalidades (pareja/individual) y —lo más importante— que dos reservas
simultáneas sobre el mismo bloque NUNCA resulten en doble reserva.

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

from notion_store import (
    ESTADO_BLOQUEADO,
    ESTADO_DISPONIBLE,
    ESTADO_RESERVADO,
    MODALIDAD_INDIVIDUAL,
    MODALIDAD_PAREJA,
    BlockUnavailableError,
    ReservationService,
)


def make_page(
    page_id: str,
    titulo: str,
    estado: str,
    modalidad: str = MODALIDAD_PAREJA,
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
            "b1": make_page("b1", "B1 — Defensa en pareja", ESTADO_DISPONIBLE),
            "pausa": make_page("pausa", "Pausa docente ☕ (1)", ESTADO_BLOQUEADO, modalidad=""),
            "b2": make_page("b2", "B2 — Defensa en pareja", ESTADO_RESERVADO, est1="Ana", est2="Luis"),
            "b13": make_page("b13", "B13 — Defensa individual", ESTADO_DISPONIBLE, modalidad=MODALIDAD_INDIVIDUAL),
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

    def test_parseo_modalidades(self):
        blocks = {b.id: b for b in self.service.list_blocks()}
        self.assertTrue(blocks["b1"].reservable)
        self.assertFalse(blocks["b1"].es_individual)
        self.assertTrue(blocks["b13"].es_individual)
        self.assertFalse(blocks["pausa"].reservable, "Las pausas nunca son reservables")
        self.assertFalse(blocks["b2"].reservable, "Un bloque sellado no es reservable")

    # ---------- Reservas ----------

    def test_reserva_pareja_exitosa(self):
        block = self.service.reserve("b1", "Camila Pérez", "Diego Rojas")
        self.assertEqual(block.estado, ESTADO_RESERVADO)
        self.assertEqual(block.estudiante_1, "Camila Pérez")
        self.assertEqual(block.estudiante_2, "Diego Rojas")

    def test_reserva_individual_con_un_nombre(self):
        block = self.service.reserve("b13", "Valentina Soto")
        self.assertEqual(block.estado, ESTADO_RESERVADO)
        self.assertEqual(block.estudiante_1, "Valentina Soto")
        self.assertEqual(block.estudiante_2, "", "La defensa individual no lleva segundo nombre")

    def test_pareja_requiere_ambos_nombres(self):
        with self.assertRaises(ValueError):
            self.service.reserve("b1", "Camila Pérez", "")
        self.assertEqual(self.client.update_count, 0, "No debe escribir nada")

    def test_nombre_1_siempre_obligatorio(self):
        with self.assertRaises(ValueError):
            self.service.reserve("b1", "   ", "Diego")

    def test_no_reserva_pausa(self):
        with self.assertRaises(BlockUnavailableError):
            self.service.reserve("pausa", "Camila", "Diego")
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
        """Reservas simultáneas sobre bloques DISTINTOS deben funcionar todas
        (incluida la individual)."""
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(self.service.reserve, "b1", "P1A", "P1B")
            f2 = ex.submit(self.service.reserve, "b13", "Solo1")
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
