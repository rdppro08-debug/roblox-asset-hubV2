"""package_cleaner.py - detecta y quita PackageLink de una COPIA de un modelo (.rbxm / .rbxmx).

Equivale a "Unlink from Package" de Roblox Studio: solo se eliminan las instancias de clase
PackageLink. Todo lo demás (partes, scripts, atributos, jerarquía, nombres, referencias) queda
tal cual. El archivo original nunca se modifica: se devuelve un nuevo contenido en memoria.

Uso:
    info = analizar(datos)      -> {"formato", "paquetes": [...], "error"}
    nuevo, n = convertir(datos) -> (bytes de la copia sin PackageLink, cuántos se quitaron)
"""
import re
import struct
import xml.etree.ElementTree as ET

CLASE = "PackageLink"
MAGIC_BIN = b"<roblox!\x89\xff\r\n\x1a\n"


# ------------------------------------------------------------------ utilidades
def _formato(d):
    if d.startswith(MAGIC_BIN):
        return "bin"
    if d[:512].lstrip(b"\xef\xbb\xbf \r\n\t").startswith((b"<roblox", b"<?xml")):
        return "xml"
    return None


# ------------------------------------------------------------------ binario (.rbxm)
def _lz4(src, n):
    out = bytearray()
    i, L = 0, len(src)
    while i < L:
        t = src[i]
        i += 1
        lit = t >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i : i + lit]
        i += lit
        if i >= L:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        m = t & 15
        if m == 15:
            while True:
                b = src[i]
                i += 1
                m += b
                if b != 255:
                    break
        m += 4
        s = len(out) - off
        if off == 0 or s < 0:
            raise ValueError("Bloque LZ4 inválido")
        for k in range(m):
            out.append(out[s + k])
    return bytes(out)


def _chunks(d):
    pos, res = 32, []
    while pos + 16 <= len(d):
        name = d[pos : pos + 4]
        c, u = struct.unpack_from("<II", d, pos + 4)
        pos += 16
        if c == 0:
            p = d[pos : pos + u]
            pos += u
        else:
            p = _lz4(d[pos : pos + c], u)
            pos += c
        if len(p) != u:
            raise ValueError("Chunk corrupto en el archivo")
        res.append((name, p))
        if name == b"END\x00":
            break
    return res


def _dec_ints(buf, pos, n):
    vals = []
    for i in range(n):
        v = (buf[pos + i] << 24) | (buf[pos + n + i] << 16) | (buf[pos + 2 * n + i] << 8) | buf[pos + 3 * n + i]
        vals.append((v >> 1) ^ -(v & 1))
    return vals, pos + 4 * n


def _enc_ints(vals):
    n = len(vals)
    b = bytearray(4 * n)
    for i, x in enumerate(vals):
        v = ((x << 1) ^ (x >> 31)) & 0xFFFFFFFF
        b[i] = v >> 24
        b[n + i] = (v >> 16) & 255
        b[2 * n + i] = (v >> 8) & 255
        b[3 * n + i] = v & 255
    return bytes(b)


def _acum(deltas):
    out, acc = [], 0
    for x in deltas:
        acc += x
        out.append(acc)
    return out


def _deltas(vals):
    out, prev = [], 0
    for x in vals:
        out.append(x - prev)
        prev = x
    return out


def _leer_bin(d):
    clases, inst, nombres, padre = {}, {}, {}, {}
    chunks = _chunks(d)
    for name, p in chunks:
        if name == b"INST":
            cid, ln = struct.unpack_from("<II", p, 0)
            cn = p[8 : 8 + ln].decode("utf-8", "replace")
            pos = 8 + ln + 1
            (n,) = struct.unpack_from("<I", p, pos)
            deltas, _ = _dec_ints(p, pos + 4, n)
            clases[cid] = cn
            inst[cid] = _acum(deltas)
        elif name == b"PROP":
            cid, ln = struct.unpack_from("<II", p, 0)
            prop = p[8 : 8 + ln].decode("utf-8", "replace")
            if prop == "Name" and p[8 + ln] == 1 and cid in inst:
                pos = 9 + ln
                for r in inst[cid]:
                    (l,) = struct.unpack_from("<I", p, pos)
                    nombres[r] = p[pos + 4 : pos + 4 + l].decode("utf-8", "replace")
                    pos += 4 + l
        elif name == b"PRNT":
            (n,) = struct.unpack_from("<I", p, 1)
            h, pos = _dec_ints(p, 5, n)
            pa, _ = _dec_ints(p, pos, n)
            padre = dict(zip(_acum(h), _acum(pa)))
    return chunks, clases, inst, nombres, padre


def _analizar_bin(d):
    _, clases, inst, nombres, padre = _leer_bin(d)
    pk = []
    for cid, cn in clases.items():
        if cn != CLASE:
            continue
        for r in inst[cid]:
            p = padre.get(r, -1)
            pk.append(
                {
                    "nombre": nombres.get(p, "?"),
                    "nivel": "principal" if padre.get(p, -1) == -1 else "anidado",
                }
            )
    return pk


def _chunk_bin(name, p):
    return name + struct.pack("<III", 0, len(p), 0) + p  # sin comprimir: válido


def _convertir_bin(d):
    chunks, clases, inst, _, padre = _leer_bin(d)
    cids_q = {cid for cid, cn in clases.items() if cn == CLASE}
    refs_q = {r for cid in cids_q for r in inst[cid]}
    if not refs_q:
        return d, 0
    if any(p in refs_q for p in padre.values()):
        raise ValueError("Un PackageLink tiene hijos; no se puede quitar de forma segura.")
    mapa = {}
    for cid in sorted(clases):
        if cid not in cids_q:
            mapa[cid] = len(mapa)

    out = []
    for name, p in chunks:
        if name in (b"INST", b"PROP"):
            (cid,) = struct.unpack_from("<I", p, 0)
            if cid in cids_q:
                continue
            p = struct.pack("<I", mapa[cid]) + p[4:]
        elif name == b"PRNT":
            (n,) = struct.unpack_from("<I", p, 1)
            h, pos = _dec_ints(p, 5, n)
            pa, _ = _dec_ints(p, pos, n)
            pares = [(a, b) for a, b in zip(_acum(h), _acum(pa)) if a not in refs_q]
            hs = [a for a, _ in pares]
            ps = [b for _, b in pares]
            p = bytes([p[0]]) + struct.pack("<I", len(pares)) + _enc_ints(_deltas(hs)) + _enc_ints(_deltas(ps))
        out.append(_chunk_bin(name, p))

    n_clases, n_inst = struct.unpack_from("<ii", d, 16)
    cabecera = d[:16] + struct.pack("<ii", len(mapa), n_inst - len(refs_q)) + d[24:32]
    nuevo = cabecera + b"".join(out)
    if _analizar_bin(nuevo):  # verificación: debe quedar sin PackageLink y seguir leyéndose
        raise ValueError("La verificación final falló; no se generó la copia.")
    return nuevo, len(refs_q)


# ------------------------------------------------------------------ XML (.rbxmx)
def _nombre_xml(item):
    if item is None:
        return "?"
    props = item.find("Properties")
    if props is not None:
        for e in props:
            if e.tag == "string" and e.get("name") == "Name":
                return e.text or "?"
    return item.get("class", "?")


def _analizar_xml(d):
    raiz = ET.fromstring(d)
    padre = {h: p for p in raiz.iter() for h in p}
    pk = []
    for it in raiz.iter("Item"):
        if it.get("class") != CLASE:
            continue
        p = padre.get(it)
        prof, a = 0, padre.get(p) if p is not None else None
        while a is not None:
            if a.tag == "Item":
                prof += 1
            a = padre.get(a)
        pk.append({"nombre": _nombre_xml(p), "nivel": "principal" if prof == 0 else "anidado"})
    return pk


_PATRON_XML = re.compile(
    rb'[ \t]*<Item\s+class="PackageLink"[^>]*?(?:/>|>.*?</Item>)[ \t]*\r?\n?', re.DOTALL
)


def _contar_items(d):
    return sum(1 for _ in ET.fromstring(d).iter("Item"))


def _convertir_xml(d):
    antes = _analizar_xml(d)
    if not antes:
        return d, 0
    total = _contar_items(d)
    nuevo, n = _PATRON_XML.subn(b"", d)
    if n != len(antes) or _analizar_xml(nuevo) or _contar_items(nuevo) != total - n:
        raise ValueError("La verificación final falló; no se generó la copia.")
    return nuevo, n


# ------------------------------------------------------------------ API pública
def analizar(datos):
    """Devuelve {"formato": "bin"|"xml"|None, "paquetes": [{"nombre","nivel"}], "error": str|None}."""
    f = _formato(datos)
    if f is None:
        return {"formato": None, "paquetes": [], "error": "No parece un .rbxm/.rbxmx de Roblox."}
    try:
        pk = _analizar_bin(datos) if f == "bin" else _analizar_xml(datos)
        return {"formato": f, "paquetes": pk, "error": None}
    except Exception as e:
        return {"formato": f, "paquetes": [], "error": f"No se pudo analizar ({type(e).__name__}: {e})"}


def convertir(datos):
    """Devuelve (copia_sin_PackageLink, cantidad_quitada). Lanza ValueError si no es seguro."""
    f = _formato(datos)
    if f is None:
        raise ValueError("Formato no reconocido.")
    return _convertir_bin(datos) if f == "bin" else _convertir_xml(datos)
