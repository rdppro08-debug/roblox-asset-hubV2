# Roblox Asset Hub v5 (Streamlit):
#  - Pestaña 1: modelos (.fbx/.gltf/.glb) y animaciones (.rbxm)  -> usa ROBLOX_API_KEY
#  - Pestaña 2: publicar juegos (.rbxl/.rbxlx) en un place existente -> usa ROBLOX_PLACE_API_KEY
#  - Pestaña 3: chat con IA (Gemini): texto, audio, chats guardados y código copiable
#
# Secrets necesarios: ROBLOX_API_KEY, ROBLOX_USER_ID, APP_USER, APP_PASS
# Secrets opcionales:
#   ROBLOX_PLACE_API_KEY  (API Key aparte para publicar juegos; si falta, usa ROBLOX_API_KEY)
#   UNIVERSE_ID, PLACE_ID (para que aparezcan ya escritos)
#   GEMINI_API_KEY        (para el chat con IA; gratis en https://aistudio.google.com/apikey)
#   GEMINI_MODEL          (modelo por defecto del chat; si falta usa gemini-3.8-flash)

import base64
import hmac
import json
import os
import re
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
GEMINI_API_KEY = secreto("GEMINI_API_KEY")

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


# ---------- Chat con IA (Gemini) ----------
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
GEMINI_MODELOS = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash"]
GEMINI_MODELO_DEF = secreto("GEMINI_MODEL") or GEMINI_MODELOS[0]
if GEMINI_MODELO_DEF not in GEMINI_MODELOS:
    GEMINI_MODELOS.insert(0, GEMINI_MODELO_DEF)

MAX_MENSAJES_CONTEXTO = 30  # cuantos mensajes anteriores se envian a la IA
CHATS_ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chats_guardados.json")
TITULO_NUEVO = "Chat nuevo"

SISTEMA_DEF = (
    "Eres un asistente experto en programación, especialmente en Roblox Studio y Luau "
    "(ServerScript, LocalScript, ModuleScript, RemoteEvents, personajes R6, DataStore y Open Cloud). "
    "Responde siempre en español, de forma clara y directa; el usuario trabaja desde el celular.\n"
    "Cuando pidan código: entrega el script COMPLETO, un bloque de código por cada script. "
    "Antes de cada bloque escribe en una línea el nombre exacto del archivo y su ruta dentro de Roblox "
    "(por ejemplo: `ServerScriptService/CombatServer` (Script)). "
    "Nunca uses fragmentos, ni instrucciones de buscar y reemplazar, ni puntos suspensivos que omitan código. "
    "Si falta información importante, haz una sola pregunta corta o asume lo más razonable y dilo."
)

PATRON_CODIGO = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
PATRON_ARCHIVO = re.compile(r"[\w\-]+(?:\.[\w\-]+)*\.(?:lua|luau|py|json|txt|js|html|md|toml)")
EXTENSIONES = {
    "lua": ".lua",
    "luau": ".lua",
    "python": ".py",
    "py": ".py",
    "json": ".json",
    "javascript": ".js",
    "js": ".js",
    "html": ".html",
    "toml": ".toml",
    "markdown": ".md",
}


def _cabeceras_gemini():
    return {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}


def _error_gemini(r):
    pistas = {
        400: "Solicitud inválida o API Key incorrecta.",
        403: "La API Key no tiene permiso o no es válida.",
        404: "Ese modelo no existe o no está disponible; prueba otro en la lista.",
        429: "Alcanzaste el límite gratuito; espera un momento o usa otro modelo.",
        500: "Error del servidor de Gemini; intenta de nuevo.",
        503: "Gemini está saturado; intenta de nuevo en un momento.",
    }
    try:
        detalle = r.json()["error"]["message"]
    except Exception:
        detalle = r.text[:300]
    return f"HTTP {r.status_code}. {pistas.get(r.status_code, '')} {detalle}".strip()


def gemini_stream(modelo, mensajes, sistema):
    """Genera la respuesta de la IA trozo a trozo."""
    contenidos = [
        {
            "role": "user" if m["role"] == "user" else "model",
            "parts": [{"text": m["text"]}],
        }
        for m in mensajes[-MAX_MENSAJES_CONTEXTO:]
    ]
    cuerpo = {"contents": contenidos}
    if sistema.strip():
        cuerpo["systemInstruction"] = {"parts": [{"text": sistema}]}
    try:
        r = requests.post(
            f"{GEMINI_BASE}{modelo}:streamGenerateContent",
            headers=_cabeceras_gemini(),
            params={"alt": "sse"},
            json=cuerpo,
            stream=True,
            timeout=(15, 120),
        )
    except requests.exceptions.RequestException as err:
        raise RuntimeError("Error de conexión: " + str(err))
    if not r.ok:
        raise RuntimeError(_error_gemini(r))

    r.encoding = "utf-8"
    hubo_texto = False
    for linea in r.iter_lines(decode_unicode=True):
        if not linea or not linea.startswith("data:"):
            continue
        dato = linea[5:].strip()
        if not dato or dato == "[DONE]":
            continue
        try:
            j = json.loads(dato)
        except ValueError:
            continue
        bloqueo = (j.get("promptFeedback") or {}).get("blockReason")
        if bloqueo:
            raise RuntimeError(f"Gemini bloqueó la solicitud ({bloqueo}). Intenta reformularla.")
        for cand in j.get("candidates") or []:
            for parte in (cand.get("content") or {}).get("parts") or []:
                texto = parte.get("text")
                if texto and not parte.get("thought"):
                    hubo_texto = True
                    yield texto
    if not hubo_texto:
        raise RuntimeError("Gemini no devolvió texto (puede haberse filtrado). Intenta reformular.")


def gemini_transcribir(modelo, audio_bytes, mime):
    """Convierte un audio en texto. Devuelve (ok, texto_o_error)."""
    cuerpo = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": mime or "audio/wav",
                            "data": base64.b64encode(audio_bytes).decode("ascii"),
                        }
                    },
                    {
                        "text": "Transcribe literalmente este audio en el idioma en que se habla. "
                        "Devuelve únicamente la transcripción, sin comentarios."
                    },
                ],
            }
        ]
    }
    try:
        r = requests.post(
            f"{GEMINI_BASE}{modelo}:generateContent",
            headers=_cabeceras_gemini(),
            json=cuerpo,
            timeout=120,
        )
        if not r.ok:
            return False, _error_gemini(r)
        partes = r.json()["candidates"][0]["content"]["parts"]
        texto = "".join(p.get("text", "") for p in partes if not p.get("thought")).strip()
        if not texto:
            return False, "No se entendió nada en el audio."
        return True, texto
    except requests.exceptions.RequestException as err:
        return False, "Error de conexión: " + str(err)
    except Exception:
        return False, "Respuesta inesperada al transcribir el audio."


# ----- Chats guardados -----
def limpiar_chats(obj):
    """Valida un diccionario de chats (del archivo o de una importación)."""
    limpios = {}
    if not isinstance(obj, dict):
        return limpios
    for cid, c in obj.items():
        if not isinstance(c, dict) or not isinstance(c.get("mensajes"), list):
            continue
        mensajes = [
            {"role": m["role"], "text": m["text"]}
            for m in c["mensajes"]
            if isinstance(m, dict)
            and m.get("role") in ("user", "assistant")
            and isinstance(m.get("text"), str)
        ]
        limpios[str(cid)] = {
            "titulo": str(c.get("titulo") or TITULO_NUEVO)[:60],
            "mensajes": mensajes,
            "actualizado": float(c.get("actualizado") or 0),
        }
    return limpios


def cargar_chats():
    try:
        with open(CHATS_ARCHIVO, "r", encoding="utf-8") as f:
            return limpiar_chats(json.load(f).get("chats"))
    except Exception:
        return {}


def guardar_chats():
    try:
        tmp = CHATS_ARCHIVO + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"chats": st.session_state["chats"]}, f, ensure_ascii=False)
        os.replace(tmp, CHATS_ARCHIVO)
    except Exception:
        pass  # si no se puede escribir en disco, el chat sigue funcionando en memoria


def nuevo_chat():
    cid = str(int(time.time() * 1000))
    st.session_state["chats"][cid] = {
        "titulo": TITULO_NUEVO,
        "mensajes": [],
        "actualizado": time.time(),
    }
    st.session_state["chat_actual"] = cid
    guardar_chats()


def borrar_chat():
    cid = st.session_state.get("chat_actual")
    st.session_state["chats"].pop(cid, None)
    st.session_state["chat_actual"] = None
    st.session_state["confirmar_borrado"] = False
    guardar_chats()


def restaurar_sistema():
    st.session_state["sistema"] = SISTEMA_DEF


def regenerar_respuesta():
    chat = st.session_state["chats"].get(st.session_state.get("chat_actual"))
    if chat and chat["mensajes"] and chat["mensajes"][-1]["role"] == "assistant":
        chat["mensajes"].pop()
        st.session_state["regenerar"] = True
        guardar_chats()


def importar_chats(contenido):
    try:
        nuevos = limpiar_chats(json.loads(contenido.decode("utf-8")).get("chats"))
    except Exception:
        return 0
    st.session_state["chats"].update(nuevos)
    guardar_chats()
    return len(nuevos)


# ----- Mostrar mensajes con bloques de código (copiar y descargar) -----
def partir_mensaje(texto):
    """Separa un mensaje en trozos de texto y bloques de código."""
    trozos = []
    pos = 0
    for m in PATRON_CODIGO.finditer(texto):
        if m.start() > pos:
            trozos.append({"tipo": "texto", "texto": texto[pos : m.start()]})
        trozos.append(
            {"tipo": "codigo", "lenguaje": m.group(1).strip().lower(), "texto": m.group(2).rstrip("\n")}
        )
        pos = m.end()
    if pos < len(texto):
        trozos.append({"tipo": "texto", "texto": texto[pos:]})
    return trozos


def nombre_descarga(trozos, i, n_codigo):
    """Intenta sacar el nombre del archivo de la línea previa al bloque de código."""
    lenguaje = trozos[i]["lenguaje"]
    ext = EXTENSIONES.get(lenguaje, ".txt" if lenguaje else ".lua")
    if i > 0 and trozos[i - 1]["tipo"] == "texto":
        lineas = [x for x in trozos[i - 1]["texto"].strip().splitlines() if x.strip()]
        if lineas:
            m = PATRON_ARCHIVO.search(lineas[-1])
            if m:
                return m.group(0)
    return f"codigo_{n_codigo}{ext}"


def mostrar_mensaje(texto, clave):
    trozos = partir_mensaje(texto)
    n_codigo = 0
    for i, tz in enumerate(trozos):
        if tz["tipo"] == "texto":
            if tz["texto"].strip():
                st.markdown(tz["texto"])
        else:
            n_codigo += 1
            leng = "lua" if tz["lenguaje"] == "luau" else (tz["lenguaje"] or None)
            st.code(tz["texto"], language=leng)  # el icono de copiar viene incluido
            st.download_button(
                "⬇️ Descargar archivo",
                data=tz["texto"].encode("utf-8"),
                file_name=nombre_descarga(trozos, i, n_codigo),
                mime="text/plain",
                key=f"dl_{clave}_{n_codigo}",
            )


def pestana_chat():
    st.subheader("🤖 Chat con IA")
    if not GEMINI_API_KEY:
        st.warning(
            "Falta el secret **GEMINI_API_KEY**. Crea una clave gratis en "
            "https://aistudio.google.com/apikey y agrégala en los secrets de Streamlit."
        )
        return

    if "chats" not in st.session_state:
        st.session_state["chats"] = cargar_chats()
        st.session_state["chat_actual"] = None
    st.session_state.setdefault("audio_n", 0)
    st.session_state.setdefault("sistema", SISTEMA_DEF)
    st.session_state.setdefault("modelo_chat", GEMINI_MODELO_DEF)

    chats = st.session_state["chats"]
    if st.session_state.get("chat_actual") not in chats:
        if chats:
            st.session_state["chat_actual"] = max(chats, key=lambda k: chats[k]["actualizado"])
        else:
            nuevo_chat()
            chats = st.session_state["chats"]

    # --- Selector de chat y ajustes ---
    ids = sorted(chats, key=lambda k: chats[k]["actualizado"], reverse=True)
    elegido = st.selectbox(
        "Chat",
        ids,
        index=ids.index(st.session_state["chat_actual"]),
        format_func=lambda k: chats[k]["titulo"],
        label_visibility="collapsed",
    )
    st.session_state["chat_actual"] = elegido
    chat = chats[elegido]

    c1, c2 = st.columns(2)
    c1.button("➕ Chat nuevo", on_click=nuevo_chat, use_container_width=True)
    c2.button(
        "🔁 Regenerar",
        on_click=regenerar_respuesta,
        disabled=not (chat["mensajes"] and chat["mensajes"][-1]["role"] == "assistant"),
        use_container_width=True,
    )

    with st.expander("⚙️ Ajustes y chats guardados"):
        st.selectbox("Modelo", GEMINI_MODELOS, key="modelo_chat")
        st.text_area(
            "Instrucciones para la IA",
            key="sistema",
            height=200,
            help="Define cómo responde la IA. Se aplica a todos los chats.",
        )
        st.button("Restaurar instrucciones originales", on_click=restaurar_sistema)
        st.divider()
        st.checkbox("Confirmo que quiero borrar este chat", key="confirmar_borrado")
        st.button(
            "🗑️ Borrar este chat",
            on_click=borrar_chat,
            disabled=not st.session_state.get("confirmar_borrado", False),
        )
        st.divider()
        st.download_button(
            "⬇️ Exportar todos los chats",
            data=json.dumps({"chats": chats}, ensure_ascii=False, indent=1).encode("utf-8"),
            file_name="chats_asset_hub.json",
            mime="application/json",
        )
        subido = st.file_uploader("Importar chats (.json)", type=["json"], key="importar_json")
        if subido is not None and st.button("Importar"):
            n = importar_chats(subido.getvalue())
            st.success(f"Se importaron {n} chats." if n else "El archivo no tiene chats válidos.")
        st.caption(
            "Los chats se guardan en el servidor de la app. Si la app se reinicia o se vuelve a "
            "desplegar pueden perderse: usa **Exportar** de vez en cuando para tener respaldo."
        )

    # --- Zona de mensajes (arriba) y entradas (abajo) ---
    zona_mensajes = st.container()

    prompt = None
    desde_audio = False
    if hasattr(st, "audio_input"):
        with st.expander("🎤 Hablar con la IA"):
            audio = st.audio_input("Graba tu mensaje", key=f"audio_{st.session_state['audio_n']}")
            if audio is not None and st.button("📨 Enviar audio", type="primary"):
                with st.spinner("Transcribiendo..."):
                    ok, texto = gemini_transcribir(
                        st.session_state["modelo_chat"], audio.getvalue(), audio.type
                    )
                if ok:
                    prompt = texto
                    desde_audio = True
                else:
                    st.error(texto)

    escrito = st.chat_input("Escribe tu mensaje...")
    if escrito:
        prompt = escrito

    regenerar = st.session_state.pop("regenerar", False)
    mensajes = chat["mensajes"]

    # --- Mostrar historial y responder ---
    with zona_mensajes:
        if not mensajes and not prompt and not regenerar:
            st.caption("Escribe o graba un mensaje para empezar. Pídele código y te lo da listo para copiar.")
        for i, m in enumerate(mensajes):
            with st.chat_message(m["role"]):
                if m["role"] == "assistant":
                    mostrar_mensaje(m["text"], f"{elegido}_{i}")
                else:
                    st.markdown(m["text"])

        generar = False
        if prompt:
            mensajes.append({"role": "user", "text": prompt})
            if chat["titulo"] == TITULO_NUEVO:
                chat["titulo"] = " ".join(prompt.split())[:40] or TITULO_NUEVO
            with st.chat_message("user"):
                st.markdown(prompt)
            generar = True
        elif regenerar and mensajes and mensajes[-1]["role"] == "user":
            generar = True

        if generar:
            with st.chat_message("assistant"):
                estado = st.empty()
                estado.caption("⏳ Pensando...")

                def trozos():
                    primero = True
                    for t in gemini_stream(
                        st.session_state["modelo_chat"], mensajes, st.session_state["sistema"]
                    ):
                        if primero:
                            estado.empty()
                            primero = False
                        yield t

                try:
                    respuesta = st.write_stream(trozos())
                except Exception as err:
                    estado.empty()
                    st.error(f"❌ {err}")
                    respuesta = None
            if respuesta and str(respuesta).strip():
                mensajes.append({"role": "assistant", "text": str(respuesta)})
                chat["actualizado"] = time.time()
                guardar_chats()
                if desde_audio:
                    st.session_state["audio_n"] += 1  # limpia la grabación para la próxima
                st.rerun()  # vuelve a dibujar con botones de copiar y descargar
            elif prompt:
                mensajes.pop()  # no deja un mensaje sin respuesta; vuelve a enviarlo
                if not mensajes:
                    chat["titulo"] = TITULO_NUEVO
                st.caption("Tu mensaje no se guardó porque no hubo respuesta. Vuelve a enviarlo.")


# ---------- Barra lateral ----------
with st.sidebar:
    st.header("📦 Asset Hub")
    st.caption(
        "Modelos: .fbx .gltf .glb\n\n"
        "Animaciones: .rbxm (máx. 20 MB)\n\n"
        "Juegos: .rbxl .rbxlx (máx. 100 MB)\n\n"
        "Chat IA: Gemini (texto y audio)"
    )
    if st.button("Cerrar sesión", use_container_width=True):
        st.query_params.clear()
        st.session_state.clear()
        st.rerun()

st.title("📦 Roblox Asset Hub")

tab_assets, tab_juego, tab_chat = st.tabs(["📦 Assets", "🎮 Publicar juego", "🤖 Chat IA"])

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

# =====================================================================
# PESTAÑA 3: CHAT IA
# =====================================================================
with tab_chat:
    pestana_chat()
