# Roblox Asset Hub v2 (Streamlit): modelos (.fbx/.gltf/.glb) y animaciones (.rbxm).
# Secrets necesarios: ROBLOX_API_KEY, ROBLOX_USER_ID, APP_USER, APP_PASS

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
USER_ID = secreto("ROBLOX_USER_ID")
APP_USER = secreto("APP_USER")
APP_PASS = secreto("APP_PASS")

if not (API_KEY and USER_ID.isdigit() and APP_USER and APP_PASS):
    st.error("Faltan secrets: ROBLOX_API_KEY, ROBLOX_USER_ID (numerico), APP_USER, APP_PASS.")
    st.stop()

# ---------- Login ----------
if not st.session_state.get("autenticado"):
    st.title("📦 Roblox Asset Hub")
    st.caption("Inicia sesión para continuar.")
    with st.form("login"):
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        entrar = st.form_submit_button("Entrar", type="primary", use_container_width=True)
    if entrar:
        if hmac.compare_digest(u, APP_USER) and hmac.compare_digest(p, APP_PASS):
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")
    st.stop()

# ---------- Subida a Roblox ----------
URL = "https://apis.roblox.com/assets/v1/assets"
CABECERAS = {"x-api-key": API_KEY}
MAX_BYTES = 20 * 1024 * 1024

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


# ---------- Barra lateral ----------
with st.sidebar:
    st.header("📦 Asset Hub")
    st.caption("Modelos: .fbx .gltf .glb\n\nAnimaciones: .rbxm\n\nMáximo 20 MB por archivo.")
    if st.button("Cerrar sesión", use_container_width=True):
        st.session_state.clear()
        st.rerun()

st.title("📦 Roblox Asset Hub")

# ---------- Paso 1: archivos ----------
st.subheader("1. Elige tus archivos")
archivos = st.file_uploader(
    "Archivos",
    type=["fbx", "gltf", "glb", "rbxm"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)
tipo_rbxm = st.radio(
    "Los archivos .rbxm son:", ["Animación", "Modelo"], horizontal=True
)

# ---------- Paso 2: nombres ----------
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

# ---------- Paso 3: subir ----------
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

# ---------- Resultados ----------
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

    st.link_button("Mis creaciones en Roblox", "https://create.roblox.com/dashboard/creations")
