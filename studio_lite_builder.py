"""
studio_lite_builder.py
Arma un archivo de juego (.rbxlx) con todos los scripts de Studio Lite ya colocados,
para subirlo con la seccion "Instalar Studio Lite" de Asset Hub (sin usar Roblox Studio).

Lee los scripts de studio_lite_scripts.json (debe estar en la misma carpeta).

Estructura que crea:
  ReplicatedStorage > StudioLite > Modules > (13 ModuleScripts)
  ServerScriptService > StudioLiteServer > ProjectService (Script)
  StarterPlayer > StarterPlayerScripts > StudioLiteClient > Main, InterfaceGenerator (LocalScripts)
"""
import itertools
import json
import os
import re

RUTA_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "studio_lite_scripts.json")
_ILEGALES = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _esc(texto):
    return (
        str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _cdata(texto):
    texto = _ILEGALES.sub("", texto)
    return "<![CDATA[" + texto.replace("]]>", "]]]]><![CDATA[>") + "]]>"


class _Constructor:
    def __init__(self):
        self.n = itertools.count()
        self.partes = []

    def item(self, clase, nombre, fuente=None, hijos=None):
        ref = f"RBX{next(self.n):05d}"
        self.partes.append(f'<Item class="{clase}" referent="{ref}"><Properties>')
        self.partes.append(f'<string name="Name">{_esc(nombre)}</string>')
        if fuente is not None:
            self.partes.append(f'<ProtectedString name="Source">{_cdata(fuente)}</ProtectedString>')
        self.partes.append("</Properties>")
        if hijos:
            hijos()
        self.partes.append("</Item>")


def cargar_fuentes():
    with open(RUTA_JSON, "r", encoding="utf-8") as f:
        datos = json.load(f)
    for grupo in ("modules", "client", "server"):
        if not datos.get(grupo):
            raise ValueError(f"Falta el grupo '{grupo}' en studio_lite_scripts.json")
    return datos


def construir_rbxlx():
    """Devuelve (bytes_del_rbxlx, lista_de_scripts_incluidos)."""
    d = cargar_fuentes()
    b = _Constructor()
    incluidos = []

    def modulos():
        for nombre, codigo in d["modules"].items():
            b.item("ModuleScript", nombre, codigo)
            incluidos.append(f"ReplicatedStorage/StudioLite/Modules/{nombre}")

    def servidor():
        for nombre, codigo in d["server"].items():
            b.item("Script", nombre, codigo)
            incluidos.append(f"ServerScriptService/StudioLiteServer/{nombre}")

    def cliente():
        for nombre, codigo in d["client"].items():
            b.item("LocalScript", nombre, codigo)
            incluidos.append(f"StarterPlayer/StarterPlayerScripts/StudioLiteClient/{nombre}")

    b.partes.append(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="http://www.roblox.com/roblox.xsd" version="4">'
        '<Meta name="ExplicitAutoJoints">true</Meta>'
    )
    b.item("Workspace", "Workspace")
    b.item(
        "ReplicatedStorage",
        "ReplicatedStorage",
        hijos=lambda: b.item("Folder", "StudioLite", hijos=lambda: b.item("Folder", "Modules", hijos=modulos)),
    )
    b.item(
        "ServerScriptService",
        "ServerScriptService",
        hijos=lambda: b.item("Folder", "StudioLiteServer", hijos=servidor),
    )
    b.item(
        "StarterPlayer",
        "StarterPlayer",
        hijos=lambda: b.item(
            "StarterPlayerScripts",
            "StarterPlayerScripts",
            hijos=lambda: b.item("Folder", "StudioLiteClient", hijos=cliente),
        ),
    )
    b.partes.append("</roblox>")
    return "".join(b.partes).encode("utf-8"), incluidos


if __name__ == "__main__":
    datos, lista = construir_rbxlx()
    with open("StudioLite.rbxlx", "wb") as f:
        f.write(datos)
    print(f"StudioLite.rbxlx: {len(datos) / 1024:.0f} KB, {len(lista)} scripts")
