# Roblox Asset Hub v4 (Streamlit):
#  - Pestaña 1: modelos (.fbx/.gltf/.glb) y animaciones (.rbxm)  -> usa ROBLOX_API_KEY
#  - Pestaña 2: publicar juegos (.rbxl/.rbxlx) en un place existente -> usa ROBLOX_PLACE_API_KEY
#
# Secrets necesarios: ROBLOX_API_KEY, ROBLOX_USER_ID, APP_USER, APP_PASS
# Secrets opcionales:
#   ROBLOX_PLACE_API_KEY  (API Key aparte para publicar juegos; si falta, usa ROBLOX_API_KEY)
#   UNIVERSE_ID, PLACE_ID (para que aparezcan ya escritos)

import hmac
import json
import os
import time

import requests
import streamlit as st

st.set_page_config(page_title="Roblox Asset Hub", page_icon="📦", layout="centered")


def secreto(nombre):
    try:
        return str(st.secrets[nombre]).strip()
    except Exception:
        return ""


API_KEY = secreto("ROBLOX_API_KEY")
PLACE_API_KEY = secreto("ROBLOX_PLACE_API_KEY") or API_KEY
USA_KEY_SEPARADA = bool(secreto("ROBLOX_PLACE_API_KEY"))
USER_ID = secreto("ROBLOX_USER_ID")
APP_USER = secreto("APP_USER")
APP_PASS = secreto("APP_PASS")
UNIVERSE_ID_DEF = secreto("UNIVERSE_ID")
PLACE_ID_DEF = secreto("PLACE_ID")

if not (API_KEY and USER_ID.isdigit() and APP_USER and APP_PASS):
    st.error("Faltan secrets: ROBLOX_API_KEY, ROBLOX_USER_ID (numerico), APP_USER, APP_PASS.")
    st.stop()

# ---------- Login (con sesion recordada por 30 dias) ----------
DURACION_SESION = 30 * 24 * 3600  # 30 dias


def _firma(exp):
    clave = (APP_PASS + API_KEY).encode("utf-8")
    return hmac.new(clave, f"{APP_USER}|{exp}".encode("utf-8"), "sha256").hexdigest()


def crear_token():
    exp = int(time.time()) + DURACION_SESION
    return f"{exp}.{_firma(exp)}"


def token_valido(token):
    try:
        exp_txt, firma = token.split(".", 1)
        exp = int(exp_txt)
    except Exception:
        return False
    return exp > time.time() and hmac.compare_digest(
        firma.encode("utf-8"), _firma(exp).encode("utf-8")
    )


def credenciales_ok(u, p):
    return hmac.compare_digest(u.encode("utf-8"), APP_USER.encode("utf-8")) and hmac.compare_digest(
        p.encode("utf-8"), APP_PASS.encode("utf-8")
    )


# Si la URL trae un token valido, entra directo sin pedir usuario y contraseña
if not st.session_state.get("autenticado"):
    t = st.query_params.get("t", "")
    if t and token_valido(t):
        st.session_state["autenticado"] = True

if not st.session_state.get("autenticado"):
    st.title("📦 Roblox Asset Hub")
    st.caption("Inicia sesión para continuar.")
    u = st.text_input("Usuario", key="login_u")
    mostrar = st.checkbox("Mostrar contraseña", value=False)
    p = st.text_input(
        "Contraseña",
        type="default" if mostrar else "password",
        key="login_p",
    )
    if st.button("Entrar", type="primary", use_container_width=True):
        if credenciales_ok(u, p):
            st.session_state["autenticado"] = True
            st.query_params["t"] = crear_token()
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")
    st.stop()

# ---------- Subida de assets a Roblox ----------
URL = "https://apis.roblox.com/assets/v1/assets"
CABECERAS = {"x-api-key": API_KEY}
MAX_BYTES = 20 * 1024 * 1024
MAX_BYTES_PLACE = 100 * 1024 * 1024

FORMATOS = {
    ".fbx": ("Model", "model/fbx"),
    ".gltf": ("Model", "model/gltf+json"),
    ".glb": ("Model", "model/gltf-binary"),
    ".rbxm": ("Animation", "model/x-rbxm"),
}


def subir_archivo(nombre_archivo, contenido, tipo_asset, mime, nombre, descripcion):
    solicitud = {
        "assetType": tipo_asset,
        "displayName": nombre,
        "description": descripcion,
        "creationContext": {"creator": {"userId": int(USER_ID)}},
    }
    try:
        r = requests.post(
            URL,
            headers=CABECERAS,
            data={"request": json.dumps(solicitud)},
            files={"fileContent": (nombre_archivo, contenido, mime)},
            timeout=180,
        )
        if not r.ok:
            return False, "", f"HTTP {r.status_code}: {r.text[:300]}"

        ruta = r.json().get("path", "")
        if not ruta.startswith("operations/"):
            return False, "", "No se recibio la operacion esperada."

        url_estado = "https://apis.roblox.com/assets/v1/" + ruta
        inicio = time.time()
        while time.time() - inicio < 240:
            e = requests.get(url_estado, headers=CABECERAS, timeout=30)
            if not e.ok:
                return False, "", f"Error consultando estado: HTTP {e.status_code}"
            res = e.json()
            if res.get("done"):
                if res.get("error"):
                    return False, "", "Roblox no pudo procesarlo: " + json.dumps(
                        res["error"], ensure_ascii=False
                    )
                asset = res.get("response", {})
                asset_id = asset.get("assetId")
                if not asset_id:
                    return False, "", "Termino sin Asset ID."
                mod = asset.get("moderationResult", {}).get("moderationState", "No informado")
                return True, str(asset_id), f"moderacion: {mod}"
            time.sleep(5)
        return False, "", f"Tiempo agotado. Operacion: {ruta}"
    except requests.exceptions.RequestException as err:
        return False, "", "Error de conexion: " + str(err)


# ---------- Publicar juego (place) ----------
def publicar_place(universe_id, place_id, contenido, ext, tipo_version):
    url = f"https://apis.roblox.com/universes/v1/{universe_id}/places/{place_id}/versions"
    tipo_contenido = "application/xml" if ext == ".rbxlx" else "application/octet-stream"
    try:
        r = requests.post(
            url,
            headers={"x-api-key": PLACE_API_KEY, "Content-Type": tipo_contenido},
            params={"versionType": tipo_version},
            data=contenido,
            timeout=300,
        )
        if not r.ok:
            pistas = {
                401: "API Key invalida o sin permiso universe-places:write.",
                403: "La API Key no tiene acceso a este universo o la IP esta restringida.",
                404: "Universe ID o Place ID incorrecto.",
                409: "Conflicto: el place esta ocupado, intenta de nuevo.",
                429: "Demasiadas solicitudes, espera un momento.",
            }
            pista = pistas.get(r.status_code, "")
            return False, f"HTTP {r.status_code}. {pista} {r.text[:300]}".strip()
        version = r.json().get("versionNumber", "?")
        return True, str(version)
    except requests.exceptions.RequestException as err:
        return False, "Error de conexion: " + str(err)


# ---------- Barra lateral ----------
with st.sidebar:
    st.header("📦 Asset Hub")
    st.caption(
        "Modelos: .fbx .gltf .glb\n\n"
        "Animaciones: .rbxm (máx. 20 MB)\n\n"
        "Juegos: .rbxl .rbxlx (máx. 100 MB)"
    )
    if st.button("Cerrar sesión", use_container_width=True):
        st.query_params.clear()
        st.session_state.clear()
        st.rerun()

st.title("📦 Roblox Asset Hub")

tab_assets, tab_juego = st.tabs(["📦 Assets", "🎮 Publicar juego"])

# =====================================================================
# PESTAÑA 1: ASSETS
# =====================================================================
with tab_assets:
    st.subheader("1. Elige tus archivos")
    archivos = st.file_uploader(
        "Archivos",
        type=["fbx", "gltf", "glb", "rbxm"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key="uploader_assets",
    )
    tipo_rbxm = st.radio(
        "Los archivos .rbxm son:", ["Animación", "Modelo"], horizontal=True
    )

    nombres = []
    descripcion = "Subido desde Asset Hub."
    if archivos:
        st.subheader("2. Nombres y descripción")
        prefijo = st.text_input(
            "Prefijo opcional (se añade al inicio de todos los nombres)",
            placeholder="Ej: Katana_",
        )
        descripcion = st.text_input("Descripción (para todos)", value=descripcion)

        for i, f in enumerate(archivos):
            base = os.path.splitext(f.name)[0]
            n = st.text_input(
                f"📄 {f.name}",
                value=base,
                max_chars=50,
                key=f"nombre_{i}_{f.name}_{f.size}",
            )
            final = (prefijo.strip() + n.strip())[:50] or "MiAsset"
            nombres.append(final)
        st.caption("Roblox permite nombres de hasta 50 caracteres.")

    if archivos:
        st.subheader("3. Subir")
    if st.button(
        "🚀 Subir a Roblox",
        type="primary",
        disabled=not archivos,
        use_container_width=True,
    ):
        filas = []
        barra = st.progress(0.0)
        estado = st.empty()

        for i, f in enumerate(archivos):
            nombre_final = nombres[i]
            estado.info(f"Subiendo {i + 1}/{len(archivos)}: {nombre_final} ...")
            ext = os.path.splitext(f.name)[1].lower()
            contenido = f.getvalue()

            if ext not in FORMATOS:
                ok, aid, msg = False, "", "Formato no aceptado."
            elif not contenido:
                ok, aid, msg = False, "", "El archivo esta vacio."
            elif len(contenido) > MAX_BYTES:
                ok, aid, msg = False, "", "Supera los 20 MB."
            else:
                tipo_asset, mime = FORMATOS[ext]
                if ext == ".rbxm":
                    tipo_asset = "Animation" if tipo_rbxm == "Animación" else "Model"
                ok, aid, msg = subir_archivo(
                    f.name, contenido, tipo_asset, mime, nombre_final, descripcion
                )

            filas.append(
                {
                    "Nombre": nombre_final,
                    "Archivo": f.name,
                    "Resultado": "✅ OK" if ok else "❌ ERROR",
                    "Asset ID": aid,
                    "rbxassetid": f"rbxassetid://{aid}" if aid else "",
                    "Detalle": msg,
                }
            )
            barra.progress((i + 1) / len(archivos))

        estado.empty()
        st.session_state["resultados"] = filas

    if st.session_state.get("resultados"):
        filas = st.session_state["resultados"]
        ok_n = sum(1 for x in filas if x["Resultado"].startswith("✅"))
        st.divider()
        st.subheader("Resultados")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total", len(filas))
        c2.metric("Correctos", ok_n)
        c3.metric("Con error", len(filas) - ok_n)

        st.dataframe(filas, hide_index=True)

        ids = [f"{x['Nombre']}: {x['rbxassetid']}" for x in filas if x["rbxassetid"]]
        if ids:
            st.text_area("Copiar IDs", "\n".join(ids), height=120)

        for x in filas:
            if x["Resultado"].startswith("❌"):
                st.error(f"{x['Nombre']}: {x['Detalle']}")

        st.link_button(
            "Mis creaciones en Roblox", "https://create.roblox.com/dashboard/creations"
        )

# =====================================================================
# PESTAÑA 2: PUBLICAR JUEGO
# =====================================================================
with tab_juego:
    st.subheader("Publicar un juego (.rbxl / .rbxlx)")
    st.info(
        "Esto **reemplaza el contenido de un place que ya existe**. "
        "No crea juegos nuevos. Crea antes un place vacío en Roblox y copia sus IDs."
    )
    if USA_KEY_SEPARADA:
        st.caption("🔑 Usando la API Key de juegos (ROBLOX_PLACE_API_KEY).")
    else:
        st.caption("🔑 Usando la API Key general (ROBLOX_API_KEY). Puedes añadir ROBLOX_PLACE_API_KEY en los secrets.")

    universe_id = st.text_input("Universe ID", value=UNIVERSE_ID_DEF, key="universe_id")
    place_id = st.text_input("Place ID", value=PLACE_ID_DEF, key="place_id")

    archivo_juego = st.file_uploader(
        "Archivo del juego",
        type=["rbxl", "rbxlx"],
        accept_multiple_files=False,
        key="uploader_juego",
    )

    modo = st.radio(
        "Modo",
        ["Publicar (visible para todos)", "Guardar (sin publicar)"],
        help="Guardar sube la versión pero no la hace pública. Podrás publicarla desde Studio.",
    )
    tipo_version = "Published" if modo.startswith("Publicar") else "Saved"

    confirmar = st.checkbox("Entiendo que se reemplazará el contenido actual del place.")

    listo = bool(
        archivo_juego and universe_id.strip().isdigit() and place_id.strip().isdigit() and confirmar
    )

    if st.button(
        "🎮 Subir juego",
        type="primary",
        disabled=not listo,
        use_container_width=True,
    ):
        contenido = archivo_juego.getvalue()
        ext = os.path.splitext(archivo_juego.name)[1].lower()
        if not contenido:
            st.error("El archivo está vacío.")
        elif len(contenido) > MAX_BYTES_PLACE:
            st.error("El archivo supera los 100 MB.")
        else:
            with st.spinner("Subiendo juego a Roblox..."):
                ok, detalle = publicar_place(
                    universe_id.strip(), place_id.strip(), contenido, ext, tipo_version
                )
            if ok:
                st.success(f"✅ Listo. Versión {detalle} ({'publicada' if tipo_version == 'Published' else 'guardada'}).")
                st.link_button(
                    "Abrir en Roblox",
                    f"https://www.roblox.com/games/{place_id.strip()}",
                )
            else:
                st.error(f"❌ {detalle}")

    if not listo:
        st.caption("Completa los dos IDs (solo números), sube el archivo y marca la casilla.")
