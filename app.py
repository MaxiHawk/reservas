"""
Reserva de Bloques — TMED732 (versión Streamlit)

Ejecutar en desarrollo:
    streamlit run app.py

Configuración: completar .streamlit/secrets.toml (ver secrets.toml.example).
"""

from datetime import datetime

import streamlit as st

from notion_store import (
    ESTADO_DISPONIBLE,
    BlockNotFoundError,
    BlockUnavailableError,
    NotionClient,
    ReservationService,
)

st.set_page_config(
    page_title="Reserva de Bloques — TMED732",
    page_icon="🗓️",
    layout="wide",
)

BADGE = {
    "Disponible": "🟢 Disponible",
    "Reservado": "🔴 Reservado",
    "Bloqueado": "⚫ Bloqueado",
}

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


@st.cache_resource
def get_service() -> ReservationService:
    """Instancia Única para TODO el proceso: el lock anti-doble-reserva es global."""
    token = st.secrets["NOTION_TOKEN"]
    database_id = st.secrets["NOTION_DATABASE_ID"]
    ttl = float(st.secrets.get("CACHE_TTL_SECONDS", 5))
    return ReservationService(NotionClient(token, database_id), cache_ttl=ttl)


def fmt_horario(inicio: str | None, fin: str | None) -> str:
    if not inicio:
        return ""
    try:
        dt_i = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
        txt = f"{DIAS[dt_i.weekday()].capitalize()} {dt_i.day} {MESES[dt_i.month - 1]} · {dt_i:%H:%M}"
        if fin:
            dt_f = datetime.fromisoformat(fin.replace("Z", "+00:00"))
            txt += f" – {dt_f:%H:%M}"
        return txt
    except (ValueError, IndexError):
        return inicio


@st.dialog("Reservar bloque")
def dialogo_reserva(block_id: str, titulo: str, horario: str):
    """Flujo en 2 pasos: ingreso de nombres → confirmación explícita."""
    service = get_service()
    paso = st.session_state.get("paso_reserva", 1)

    if paso == 1:
        st.markdown(f"**{titulo}**  \n{horario}")
        e1 = st.text_input(
            "Estudiante 1 (nombre completo)",
            value=st.session_state.get("est1", ""),
            placeholder="Ej: Camila Pérez Soto",
            max_chars=120,
        )
        e2 = st.text_input(
            "Estudiante 2 (nombre completo)",
            value=st.session_state.get("est2", ""),
            placeholder="Ej: Diego Rojas Muñoz",
            max_chars=120,
        )
        col1, col2 = st.columns(2)
        if col1.button("Cancelar", use_container_width=True):
            _cerrar_dialogo()
            st.rerun()
        if col2.button("Continuar →", type="primary", use_container_width=True):
            if len(e1.strip()) < 2 or len(e2.strip()) < 2:
                st.error("Debes ingresar los nombres completos de ambos integrantes.")
            else:
                st.session_state["est1"] = e1.strip()
                st.session_state["est2"] = e2.strip()
                st.session_state["paso_reserva"] = 2
                st.rerun(scope="fragment")

    else:  # paso 2: confirmación
        st.warning(
            f"⚠️ **Confirma tu reserva**\n\n"
            f"**Bloque:** {titulo}  \n"
            f"**Horario:** {horario}  \n"
            f"**Pareja:** {st.session_state['est1']} y {st.session_state['est2']}\n\n"
            "Esta acción no se puede deshacer desde la app."
        )
        col1, col2 = st.columns(2)
        if col1.button("← Volver", use_container_width=True):
            st.session_state["paso_reserva"] = 1
            st.rerun(scope="fragment")
        if col2.button("✅ Confirmar reserva", type="primary", use_container_width=True):
            try:
                block = service.reserve(
                    block_id,
                    st.session_state["est1"],
                    st.session_state["est2"],
                )
                st.session_state["flash"] = (
                    "success",
                    f"¡Reserva exitosa! **{block.titulo}** quedó a nombre de "
                    f"{block.estudiante_1} y {block.estudiante_2}.",
                )
            except BlockUnavailableError as e:
                st.session_state["flash"] = (
                    "error",
                    f"Lo sentimos, **{titulo}** ya no está disponible "
                    f"(estado actual: {e.estado}). Elige otro bloque.",
                )
            except BlockNotFoundError:
                st.session_state["flash"] = ("error", "El bloque ya no existe.")
            except ValueError as e:
                st.session_state["flash"] = ("error", str(e))
            except Exception:
                st.session_state["flash"] = (
                    "error",
                    "Error de conexión con Notion. Intenta nuevamente.",
                )
            _cerrar_dialogo()
            st.rerun()


def _cerrar_dialogo():
    for key in ("bloque_seleccionado", "paso_reserva", "est1", "est2"):
        st.session_state.pop(key, None)


@st.fragment(run_every="5s")
def grilla_bloques():
    """Grilla auto-actualizable de bloques (cada 5 s, sin recargar toda la app)."""
    service = get_service()
    try:
        blocks = service.list_blocks()
    except Exception:
        st.error("No se pudo cargar la disponibilidad desde Notion. Reintentando…")
        return

    if not blocks:
        st.info("Aún no hay bloques definidos.")
        return

    disponibles = sum(1 for b in blocks if b.disponible)
    st.caption(
        f"🟢 {disponibles} disponibles de {len(blocks)} bloques · "
        "La disponibilidad se actualiza automáticamente cada 5 segundos."
    )

    N_COLS = 3
    for fila_inicio in range(0, len(blocks), N_COLS):
        cols = st.columns(N_COLS)
        for col, b in zip(cols, blocks[fila_inicio : fila_inicio + N_COLS]):
            with col, st.container(border=True):
                st.markdown(f"**{b.titulo}**")
                st.markdown(BADGE.get(b.estado, b.estado))
                horario = fmt_horario(b.inicio, b.fin)
                if horario:
                    st.caption(horario)
                if b.reservado_por:
                    st.caption(f"Tomado por: *{b.reservado_por}*")
                if b.disponible:
                    if st.button(
                        "Reservar este bloque",
                        key=f"btn_{b.id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        st.session_state["bloque_seleccionado"] = {
                            "id": b.id,
                            "titulo": b.titulo,
                            "horario": horario,
                        }
                        st.session_state["paso_reserva"] = 1
                        st.rerun(scope="app")


def main():
    st.title("🗓️ Reserva de Bloques — TMED732")
    st.markdown(
        "Selecciona un bloque **disponible**, ingresa los nombres de la pareja "
        "y confirma tu reserva."
    )

    # Mensajes flash (resultado de la última acción)
    flash = st.session_state.pop("flash", None)
    if flash:
        tipo, msg = flash
        (st.success if tipo == "success" else st.error)(msg)

    grilla_bloques()

    # Abrir diálogo si hay un bloque seleccionado
    sel = st.session_state.get("bloque_seleccionado")
    if sel:
        dialogo_reserva(sel["id"], sel["titulo"], sel["horario"])


if __name__ == "__main__":
    main()
else:
    main()
