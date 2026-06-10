"""
Reservas Tribunal Endovascular — TMED732 UNAB 2026
Estética: Universo AngioMasters // Archivo de la Orden (uam.maxihawk.com)

Liberación programada: controlada desde Notion (fila ⚙️ en la base).
Antes de la apertura se muestra una cuenta regresiva y las reservas quedan
bloqueadas también del lado del servidor.

Ejecutar:  streamlit run app.py
"""

import os

import streamlit as st

from notion_store import (
    ESTADO_BLOQUEADO,
    ESTADO_DISPONIBLE,
    ESTADO_RESERVADO,
    BlockUnavailableError,
    NotionClient,
    ReservationService,
    ReservationsLockedError,
)

# ─────────────────────── Configuración ───────────────────────

st.set_page_config(
    page_title="Reservas Tribunal Endovascular",
    page_icon="🏛️",
    layout="centered",
    initial_sidebar_state="collapsed",
)


def _secret(name: str, default: str = "") -> str:
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.environ.get(name, default)


@st.cache_resource
def get_service() -> ReservationService:
    """Instancia ÚNICA compartida por todas las sesiones (hilos) → el
    threading.Lock interno garantiza cero dobles reservas."""
    token = _secret("NOTION_TOKEN")
    database_id = _secret("NOTION_DATABASE_ID")
    if not token or not database_id:
        st.error("⚠️ Falta configurar NOTION_TOKEN y NOTION_DATABASE_ID en Secrets.")
        st.stop()
    ttl = float(_secret("CACHE_TTL_SECONDS", "5"))
    tz = _secret("RESERVAS_TZ", "America/Santiago")
    return ReservationService(NotionClient(token, database_id), cache_ttl=ttl, tz_name=tz)


# ─────────────────────── Estilo // Archivo de la Orden ────────────────

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@500;600;700&display=swap');

.stApp {
  background:
    radial-gradient(ellipse at 20% -10%, rgba(0,229,255,.10), transparent 50%),
    radial-gradient(ellipse at 90% 110%, rgba(255,209,102,.06), transparent 55%),
    #070b14;
}
h1, h2, h3 { font-family: 'Rajdhani', sans-serif !important; letter-spacing: .04em; }

.uam-kicker {
  font-family: 'Share Tech Mono', monospace;
  color: #00e5ff; font-size: .8rem; letter-spacing: .25em;
  text-transform: uppercase; opacity: .9; margin-bottom: .2rem;
}
.uam-title {
  font-family: 'Rajdhani', sans-serif; font-weight: 700;
  font-size: clamp(1.7rem, 6vw, 2.6rem); line-height: 1.05;
  color: #e6f1ff; text-shadow: 0 0 24px rgba(0,229,255,.35);
  margin: 0 0 .3rem 0;
}
.uam-sub {
  font-family: 'Share Tech Mono', monospace;
  color: #8fa8c7; font-size: .85rem;
}
.uam-card {
  border: 1px solid rgba(0,229,255,.22);
  border-left: 4px solid var(--edge, #00e5ff);
  border-radius: 12px;
  background: linear-gradient(160deg, rgba(14,22,38,.92), rgba(9,14,26,.92));
  padding: .8rem 1rem; margin: .45rem 0;
  box-shadow: 0 0 18px rgba(0,229,255,.05);
}
.uam-card.libre   { --edge: #00e5ff; }
.uam-card.tomado  { --edge: #ff5d73; opacity: .92; }
.uam-card.pausa   { --edge: #44506a; opacity: .75; padding: .45rem 1rem; }
.uam-card.sellado { --edge: #ffd166; }
.uam-time {
  font-family: 'Share Tech Mono', monospace;
  font-size: 1.05rem; color: #00e5ff; white-space: nowrap;
}
.uam-card.tomado .uam-time { color: #ff5d73; }
.uam-card.pausa  .uam-time { color: #8fa8c7; }
.uam-name { font-family: 'Rajdhani', sans-serif; font-weight: 600; font-size: 1.1rem; color: #e6f1ff; }
.uam-badge {
  display: inline-block; font-family: 'Share Tech Mono', monospace;
  font-size: .68rem; letter-spacing: .12em; text-transform: uppercase;
  padding: .12rem .55rem; border-radius: 999px; border: 1px solid;
}
.b-libre  { color: #00e5ff; border-color: rgba(0,229,255,.5);  background: rgba(0,229,255,.08); }
.b-tomado { color: #ff5d73; border-color: rgba(255,93,115,.5); background: rgba(255,93,115,.08); }
.b-pausa  { color: #8fa8c7; border-color: rgba(143,168,199,.4); background: rgba(143,168,199,.07); }
.b-indiv  { color: #ffd166; border-color: rgba(255,209,102,.5); background: rgba(255,209,102,.08); }
.b-lock   { color: #ffd166; border-color: rgba(255,209,102,.5); background: rgba(255,209,102,.08); }
.uam-squad { font-family: 'Share Tech Mono', monospace; font-size: .82rem; color: #ffd166; margin-top: .15rem; }

/* Cuenta regresiva — portal sellado */
.uam-portal {
  border: 1px solid rgba(255,209,102,.4);
  border-radius: 16px; text-align: center;
  background: linear-gradient(160deg, rgba(30,24,8,.6), rgba(9,14,26,.95));
  box-shadow: 0 0 30px rgba(255,209,102,.12), inset 0 0 40px rgba(255,209,102,.04);
  padding: 1.4rem 1rem; margin: 1rem 0;
}
.uam-portal .lbl {
  font-family: 'Share Tech Mono', monospace; color: #ffd166;
  letter-spacing: .3em; font-size: .75rem; text-transform: uppercase;
}
.uam-portal .timer {
  font-family: 'Share Tech Mono', monospace; font-weight: 400;
  font-size: clamp(2rem, 9vw, 3.4rem); color: #ffd166;
  text-shadow: 0 0 24px rgba(255,209,102,.55); margin: .3rem 0;
}
.uam-portal .units {
  font-family: 'Share Tech Mono', monospace; color: #8fa8c7;
  font-size: .7rem; letter-spacing: .35em; text-transform: uppercase;
}
.uam-portal .fecha {
  font-family: 'Rajdhani', sans-serif; color: #e6f1ff; font-size: 1rem; margin-top: .5rem;
}

.stButton > button {
  font-family: 'Share Tech Mono', monospace !important;
  letter-spacing: .08em; text-transform: uppercase;
  border: 1px solid rgba(0,229,255,.6) !important;
  background: rgba(0,229,255,.10) !important; color: #00e5ff !important;
  border-radius: 10px !important; width: 100%;
  min-height: 2.6rem; /* objetivo táctil cómodo en móvil */
}
.stButton > button:hover { background: rgba(0,229,255,.22) !important; box-shadow: 0 0 14px rgba(0,229,255,.35); }
</style>
""",
    unsafe_allow_html=True,
)

# ─────────────────────── Utilidades ───────────────────────


def hora(iso: str | None) -> str:
    """'2026-06-16T14:06:00.000Z' → '14:06' (hora literal, sin convertir TZ)."""
    return iso[11:16] if iso and len(iso) >= 16 else "--:--"


def es_pausa(b) -> bool:
    return b.estado == ESTADO_BLOQUEADO and not b.modalidad


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def fecha_es(dt) -> str:
    return (
        f"{DIAS[dt.weekday()]} {dt.day} de {MESES[dt.month - 1]} "
        f"· {dt.strftime('%H:%M')} hrs (hora Chile)"
    )


# ─────────────────────── Diálogo de reserva (2 pasos) ───────────────


@st.dialog("🛡️ Registro ante el Tribunal")
def dialogo_reserva(block):
    st.markdown(
        f"**{block.titulo}**  \n"
        f"⏱️ {hora(block.inicio)} – {hora(block.fin)} · martes 16 de junio 2026"
    )
    if block.es_individual:
        st.caption("// MODALIDAD: DEFENSA INDIVIDUAL — un solo aspirante //")
        e1 = st.text_input("🩸 Nombre completo del aspirante", key="d_e1")
        e2 = ""
    else:
        st.caption("// MODALIDAD: ESCUADRÓN DE A DOS — ambos nombres requeridos //")
        e1 = st.text_input("🩸 Aspirante 1 — nombre completo", key="d_e1")
        e2 = st.text_input("🩸 Aspirante 2 — nombre completo", key="d_e2")

    confirmo = st.checkbox(
        "Declaro ante el Sumo Cartógrafo que este registro es definitivo "
        "y sólo él puede liberarlo."
    )

    if st.button("⚔️ SELLAR LA RESERVA", disabled=not confirmo, type="primary"):
        try:
            resultado = get_service().reserve(block.id, e1, e2)
        except ValueError as e:
            st.warning(str(e))
        except ReservationsLockedError as e:
            st.error(f"🔒 El portal aún está sellado. {e}")
        except BlockUnavailableError:
            st.error(
                "⚡ Otro escuadrón selló este bloque hace instantes. "
                "Elige otro horario disponible."
            )
        except Exception:
            st.error("Error de conexión con el Archivo. Intenta de nuevo.")
        else:
            st.session_state["exito_msg"] = (
                f"🏆 **{resultado.titulo}** sellado para "
                f"**{resultado.estudiante_1}**"
                + (f" y **{resultado.estudiante_2}**" if resultado.estudiante_2 else "")
                + f" · {hora(resultado.inicio)}–{hora(resultado.fin)}"
            )
            st.rerun()  # cierra el diálogo y refresca la línea de tiempo


# ─────────────────────── Encabezado ───────────────────────

st.markdown(
    """
<div class="uam-kicker">// UNIVERSO ANGIOMASTERS // ARCHIVO DE LA ORDEN //</div>
<div class="uam-title">🏛️ Reservas Tribunal Endovascular</div>
<div class="uam-sub">DEFENSA FINAL ORAL · TMED732 · UNAB 2026 · MARTES 16 JUN · 14:00 → 16:46</div>
""",
    unsafe_allow_html=True,
)

st.markdown(
    "> 🧭 **Aspirantes:** elijan su bloque ante el Tribunal. "
    "Solo los bloques **DISPONIBLES** pueden sellarse; la decisión es definitiva "
    "y únicamente el **Sumo Cartógrafo** puede liberarla. Uniforme de TM obligatorio."
)

if "exito_msg" in st.session_state:
    st.success(st.session_state.pop("exito_msg"))

# ─────────────────────── Cuenta regresiva (portal sellado) ────────────


@st.fragment(run_every=1)
def portal_countdown():
    """Cronómetro en vivo hasta la apertura. Al llegar a cero, recarga la app."""
    svc = get_service()
    try:
        release = svc.get_release_time()
        restante = svc.seconds_until_release()
    except Exception:
        return
    if release is None or restante <= 0:
        st.rerun(scope="app")  # ¡portal abierto! → recargar todo
        return

    total = int(restante)
    d, resto = divmod(total, 86400)
    h, resto = divmod(resto, 3600)
    m, s = divmod(resto, 60)
    timer = f"{d:02d} : {h:02d} : {m:02d} : {s:02d}"

    st.markdown(
        f"""
<div class="uam-portal">
  <div class="lbl">// PORTAL SELLADO // APERTURA DE RESERVAS EN //</div>
  <div class="timer">{timer}</div>
  <div class="units">días&nbsp;&nbsp;&nbsp;horas&nbsp;&nbsp;&nbsp;min&nbsp;&nbsp;&nbsp;seg</div>
  <div class="fecha">🗝️ El Sumo Cartógrafo abrirá el portal el <b>{fecha_es(release)}</b></div>
</div>
""",
        unsafe_allow_html=True,
    )


# ─────────────────────── Línea de tiempo (auto-refresh 5 s) ────────────


@st.fragment(run_every=5)
def timeline():
    svc = get_service()
    try:
        blocks = svc.visible_blocks()
        abierto = svc.reservations_open()
    except Exception:
        st.error("No se pudo consultar el Archivo de la Orden. Reintentando…")
        return

    reservables = [b for b in blocks if b.modalidad]
    tomados = [b for b in reservables if b.estado == ESTADO_RESERVADO]
    libres = len(reservables) - len(tomados)

    c1, c2 = st.columns(2)
    c1.metric("🟢 Bloques libres", libres)
    c2.metric("🔴 Sellados", len(tomados))
    if reservables:
        st.progress(len(tomados) / len(reservables))

    for b in blocks:
        ini, fin = hora(b.inicio), hora(b.fin)

        if es_pausa(b):
            st.markdown(
                f'<div class="uam-card pausa">'
                f'<span class="uam-time">{ini}–{fin}</span> &nbsp; '
                f'<span class="uam-badge b-pausa">⚫ {b.titulo}</span></div>',
                unsafe_allow_html=True,
            )
            continue

        indiv = (
            ' <span class="uam-badge b-indiv">★ individual</span>'
            if b.es_individual
            else ""
        )

        if b.estado == ESTADO_DISPONIBLE:
            if abierto:
                col_info, col_btn = st.columns([3, 1.2], vertical_alignment="center")
                with col_info:
                    st.markdown(
                        f'<div class="uam-card libre">'
                        f'<span class="uam-time">{ini}–{fin}</span> &nbsp; '
                        f'<span class="uam-badge b-libre">🟢 disponible</span>{indiv}'
                        f'<div class="uam-name">{b.titulo}</div></div>',
                        unsafe_allow_html=True,
                    )
                with col_btn:
                    if st.button("Reservar", key=f"btn_{b.id}"):
                        st.session_state["bloque_elegido"] = b.id
                        st.rerun(scope="app")
            else:
                # Cronograma visible pero bloqueado hasta la apertura
                st.markdown(
                    f'<div class="uam-card sellado">'
                    f'<span class="uam-time">{ini}–{fin}</span> &nbsp; '
                    f'<span class="uam-badge b-lock">🔒 portal sellado</span>{indiv}'
                    f'<div class="uam-name">{b.titulo}</div></div>',
                    unsafe_allow_html=True,
                )
        else:
            squad = b.estudiante_1 + (f" & {b.estudiante_2}" if b.estudiante_2 else "")
            squad_html = (
                f'<div class="uam-squad">⚔️ {squad}</div>' if squad else ""
            )
            st.markdown(
                f'<div class="uam-card tomado">'
                f'<span class="uam-time">{ini}–{fin}</span> &nbsp; '
                f'<span class="uam-badge b-tomado">🔴 sellado</span>{indiv}'
                f'<div class="uam-name">{b.titulo}</div>{squad_html}</div>',
                unsafe_allow_html=True,
            )

    st.caption(
        "// El Archivo se sincroniza automáticamente cada 5 segundos · "
        "administrado por el Sumo Cartógrafo //"
    )


# ─────────────────────── Render principal ─────────────────────

_portal_abierto = True
try:
    _portal_abierto = get_service().reservations_open()
except Exception:
    pass

if not _portal_abierto:
    portal_countdown()

timeline()

# Abrir el diálogo fuera del fragmento (rerun completo de la app)
_elegido = st.session_state.pop("bloque_elegido", None)
if _elegido:
    _b = next(
        (x for x in get_service().visible_blocks() if x.id == _elegido), None
    )
    if _b is not None and _b.reservable:
        dialogo_reserva(_b)
    else:
        st.error("⚡ Ese bloque acaba de ser sellado por otro escuadrón.")
