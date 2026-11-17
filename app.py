"""app.py — Recortador IA (Etapa 1): subir imagen, recorte automático, vista previa, descarga y ZIP."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile

import gradio as gr
from PIL import Image, ImageOps

import engine
from engine import PARTS

N = len(PARTS)
LABELS = [p.label for p in PARTS]
LABEL2KEY = {p.label: p.key for p in PARTS}
EXPORT_FULL, EXPORT_CROP = "full", "crop"

HEAD = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'

CSS = """
:root { --tap: 48px; --gap: 12px; }
html, body { overflow-x: hidden; }
.gradio-container {
  max-width: 640px !important; margin: 0 auto !important;
  padding: 12px 12px calc(110px + env(safe-area-inset-bottom)) 12px !important;
  font-size: 16px;
}
button { min-height: var(--tap); font-size: 16px; }
input[type=text], textarea { min-height: var(--tap); font-size: 16px; }
#parts .wrap { display: flex; flex-direction: column; gap: var(--gap); }
#parts label {
  min-height: var(--tap); display: flex; align-items: center; gap: 12px;
  padding: 10px 14px; border-radius: 14px;
}
#parts input[type=checkbox] { width: 26px; height: 26px; flex: 0 0 26px; }
#go-btn {
  position: fixed; left: 0; right: 0; margin: 0 auto;
  bottom: calc(12px + env(safe-area-inset-bottom));
  width: calc(100% - 24px); max-width: 616px; min-height: 56px;
  font-size: 18px; z-index: 1000; border-radius: 16px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, .28);
}
.card { border-radius: 16px; }
"""

THEME = gr.themes.Soft(
    primary_hue="violet", neutral_hue="slate",
    radius_size="lg", text_size="lg", spacing_size="lg",
    font=[gr.themes.GoogleFont("Inter"), "system-ui", "sans-serif"],
)

CPU_NOTE = (
    "⚠️ **Modo CPU (sin GPU):** se usan modelos pequeños y el recorte será **más lento** "
    "(puede tardar varios minutos). Con una GPU en el Space va mucho más rápido."
)
SIDES_NOTE = (
    "**Izquierdo / derecho = del PERSONAJE**, no del espectador. "
    "Si el personaje te mira de frente, su brazo izquierdo aparece en el lado **derecho** de la pantalla."
)


# ---------------------------------------------------------------- utilidades
def _safe_name(name: str | None, fallback: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", (name or "").strip(), flags=re.UNICODE).strip("_")
    return s[:60] or fallback


def _unique(names_by_key: dict) -> dict:
    used, out = set(), {}
    for k, n in names_by_key.items():
        base = _safe_name(n, k)
        cand, i = base, 2
        while cand.lower() in used:
            cand, i = f"{base}_{i}", i + 1
        used.add(cand.lower())
        out[k] = cand
    return out


def _write_files(state: dict, mode: str, names_by_key: dict):
    """Escribe un PNG por parte detectada + un ZIP. Devuelve ({clave: ruta}, ruta_zip)."""
    crop = mode == EXPORT_CROP
    tmp = tempfile.mkdtemp(prefix="recortador_")
    ok_keys = [p.key for p in PARTS if state["results"].get(p.key) and state["results"][p.key].alpha is not None]
    names = _unique({k: names_by_key.get(k) for k in ok_keys})
    paths = {}
    for k in ok_keys:
        path = os.path.join(tmp, f"{names[k]}.png")
        engine.export_png(state["orig"], state["results"][k].alpha, crop, path)
        paths[k] = path
    zip_path = None
    if paths:
        zip_path = os.path.join(tmp, "recortes.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
            for p in paths.values():
                z.write(p, arcname=os.path.basename(p))
    old = state.get("tmp")
    state["tmp"] = tmp
    if old:
        shutil.rmtree(old, ignore_errors=True)
    return paths, zip_path


# ---------------------------------------------------------------- acciones
def process(image, selected, facing, mode, state, progress=gr.Progress()):
    if image is None:
        raise gr.Error("Primero sube una imagen (de la galería o con la cámara).")
    if not selected:
        raise gr.Error("Marca al menos una parte de la lista y vuelve a tocar «Recortar».")
    wanted = [LABEL2KEY[l] for l in selected]

    img = ImageOps.exif_transpose(image).convert("RGB")
    work, scale = engine.downscale(img)

    progress(0.02, desc="Cargando modelos (la primera vez tarda)…")
    try:
        eng = engine.get_engine()
    except Exception as e:  # noqa: BLE001
        raise gr.Error(
            f"No pude cargar los modelos ({type(e).__name__}: {e}). "
            "Revisa los logs del Space: suele ser falta de memoria, un error de instalación "
            "o que no hay conexión para descargar los pesos."
        )

    try:
        with engine.RUN_LOCK:
            results = eng.segment(work, wanted, facing, lambda f, t: progress(f, desc=t))
    except Exception as e:  # noqa: BLE001
        raise gr.Error(
            f"El recorte falló ({type(e).__name__}: {e}). Prueba con una imagen más pequeña "
            "o con el personaje más grande y centrado."
        )

    if state and state.get("tmp"):
        shutil.rmtree(state["tmp"], ignore_errors=True)
    state = {"orig": img, "work": work, "scale": scale, "results": results, "tmp": None}
    names = {p.key: p.key for p in PARTS}
    paths, zip_path = _write_files(state, mode, names)

    n_ok = sum(1 for r in results.values() if r.alpha is not None)
    summary = f"**Listo:** {n_ok} de {len(results)} partes recortadas."
    if scale < 1:
        summary += f" (Se trabajó a {work.size[0]}×{work.size[1]}; la descarga sale a {img.size[0]}×{img.size[1]}.)"

    outs = [
        state,
        gr.update(visible=False),                       # mensaje de estado vacío
        gr.update(value=summary, visible=True),
        gr.update(value=zip_path, visible=bool(zip_path)),
    ]
    for p in PARTS:
        r = results.get(p.key)
        if r is None:
            outs += [gr.update(visible=False), gr.update(), gr.update(visible=False),
                     gr.update(), gr.update(visible=False)]
            continue
        has = r.alpha is not None
        if r.status == "ok":
            title = f"**{p.label}** · ✅ Detectada"
        elif r.status == "approx":
            title = f"**{p.label}** · ⚠️ Aproximada — {r.note}"
        elif r.status == "error":
            title = f"**{p.label}** · ❌ Error — {r.note}"
        else:
            title = (f"**{p.label}** · ❌ No detectada — {r.note}. "
                     "Prueba con una imagen más clara o con el personaje más grande.")
        preview = engine.render_preview(work, r.alpha) if has else None
        outs += [
            gr.update(visible=True),
            gr.update(value=title),
            gr.update(value=preview, visible=has),
            gr.update(value=p.key, visible=has),
            gr.update(value=paths.get(p.key), visible=has),
        ]
    return outs


def refresh(state, mode, *names):
    """Cambió el modo de exportación o un nombre: regenera archivos y ZIP."""
    if not state:
        return [state, gr.update()] + [gr.update() for _ in range(N)]
    paths, zip_path = _write_files(state, mode, {p.key: n for p, n in zip(PARTS, names)})
    return [state, gr.update(value=zip_path, visible=bool(zip_path))] + [
        gr.update(value=paths.get(p.key)) for p in PARTS
    ]


def recortar_api(ruta, partes_csv, facing):
    """Endpoint /recortar para otras apps (p. ej. el Asset Hub en Streamlit).

    Recibe la imagen, las claves de las partes separadas por coma y 'front'/'back'.
    Devuelve (ZIP con un PNG por parte a tamaño original del lienzo, JSON con el estado de cada parte).
    """
    if not ruta:
        raise gr.Error("Falta la imagen.")
    claves = [k.strip() for k in (partes_csv or "").split(",") if k.strip() in engine.PART_BY_KEY]
    if not claves:
        raise gr.Error("No llegó ninguna parte válida.")
    facing = facing if facing in ("front", "back") else "front"
    img = ImageOps.exif_transpose(Image.open(ruta)).convert("RGB")
    work, _scale = engine.downscale(img)
    eng = engine.get_engine()
    with engine.RUN_LOCK:
        results = eng.segment(work, claves, facing)
    state = {"orig": img, "results": results, "tmp": None}
    _paths, zip_path = _write_files(state, EXPORT_FULL, {})
    info = {k: {"estado": r.status, "nota": r.note} for k, r in results.items()}
    return zip_path, json.dumps(info, ensure_ascii=False)


# ---------------------------------------------------------------- interfaz
with gr.Blocks(theme=THEME, css=CSS, head=HEAD, title="Recortador IA", fill_width=False) as demo:
    state = gr.State(None)

    gr.Markdown("# ✂️ Recortador IA\nSube un personaje y recorta sus partes en PNG transparente.")
    if not engine.HAS_GPU:
        gr.Markdown(CPU_NOTE)

    image = gr.Image(
        label="1 · Imagen del personaje", type="pil", image_mode="RGB",
        sources=["upload", "webcam"], height=380,
    )

    gr.Markdown("### 2 · ¿Qué partes quieres?")
    gr.Markdown(SIDES_NOTE)
    facing = gr.Radio(
        label="¿Cómo está el personaje en la imagen?",
        choices=[("De frente (me mira)", "front"), ("De espaldas", "back")],
        value="front",
    )
    parts = gr.CheckboxGroup(
        label="Partes", choices=LABELS, value=["Pelo", "Torso", "Personaje completo"], elem_id="parts"
    )
    with gr.Row():
        btn_all = gr.Button("Seleccionar todas", min_width=140)
        btn_none = gr.Button("Quitar todas", min_width=140)

    export_mode = gr.Radio(
        label="Tamaño de la descarga",
        choices=[("Tamaño original del lienzo (las piezas encajan)", EXPORT_FULL),
                 ("Recortado a su contorno", EXPORT_CROP)],
        value=EXPORT_FULL,
    )

    gr.Markdown("### 3 · Resultados")
    empty_md = gr.Markdown("Aún no hay recortes. Sube una imagen, elige partes y toca **Recortar**.")
    summary = gr.Markdown(visible=False)
    zip_btn = gr.DownloadButton("⬇️ Descargar todo (ZIP)", visible=False, variant="primary")

    cards, titles, previews, names, dls = [], [], [], [], []
    for p in PARTS:
        with gr.Group(visible=False, elem_classes=["card"]) as card:
            titles.append(gr.Markdown())
            previews.append(gr.Image(show_label=False, interactive=False, show_download_button=False))
            names.append(gr.Textbox(label="Nombre del archivo (sin .png)", value=p.key, max_lines=1))
            dls.append(gr.DownloadButton("Descargar PNG"))
        cards.append(card)

    run_btn = gr.Button("✂️ Recortar", variant="primary", elem_id="go-btn")

    # --- eventos
    btn_all.click(lambda: LABELS, None, parts)
    btn_none.click(lambda: [], None, parts)

    all_outputs = [state, empty_md, summary, zip_btn]
    for i in range(N):
        all_outputs += [cards[i], titles[i], previews[i], names[i], dls[i]]
    run_btn.click(process, [image, parts, facing, export_mode, state], all_outputs)

    refresh_out = [state, zip_btn] + dls
    refresh_in = [state, export_mode] + names
    export_mode.change(refresh, refresh_in, refresh_out, show_progress="minimal", trigger_mode="always_last")
    for t in names:
        t.blur(refresh, refresh_in, refresh_out, show_progress="minimal", trigger_mode="always_last")
        t.submit(refresh, refresh_in, refresh_out, show_progress="minimal", trigger_mode="always_last")

    # --- API oculta para otras apps: api_name="recortar" (no se ve en la pantalla)
    with gr.Group(visible=False):
        api_img = gr.File(type="filepath")
        api_partes = gr.Textbox()
        api_facing = gr.Textbox()
        api_zip = gr.File()
        api_info = gr.Textbox()
        api_btn = gr.Button()
    api_btn.click(recortar_api, [api_img, api_partes, api_facing], [api_zip, api_info], api_name="recortar")

demo.queue(default_concurrency_limit=1)

if __name__ == "__main__":
    demo.launch()
