"""Extrae campos del anverso a partir de las líneas de PaddleOCR (segunda opinión del VLM)."""

import re
from dataclasses import dataclass, field

from .ocr import Line
from .validators import CLAVE_RE, CURP_RE, normalize_clave, normalize_curp, parse_date, strip_accents


@dataclass
class FrontOcr:
    nombre_lineas: list[str] = field(default_factory=list)
    domicilio_lineas: list[str] = field(default_factory=list)
    curp: str | None = None
    clave_elector: str | None = None
    fecha_nacimiento: str | None = None


def _n(t: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", strip_accents(t.upper()))


def _is_name(t: str) -> bool:
    letters = sum(c.isalpha() and c.isascii() or c in "ÑÁÉÍÓÚÜ" for c in t.upper())
    return letters >= 2 and letters / max(1, len(t.replace(" ", ""))) >= 0.7


def _label_idx(lines: list[Line], *labels: str) -> int | None:
    for i, l in enumerate(lines):
        t = _n(l.text)
        if any(t.startswith(lb) for lb in labels):
            return i
    return None


def _block(lines: list[Line], start: int, stop: int | None, label: Line) -> list[str]:
    """Líneas debajo de una etiqueta, alineadas a su izquierda, hasta la siguiente etiqueta."""
    out = []
    tol = 2.5 * label.h
    for l in lines[start + 1: stop]:
        if l.cy <= label.cy + 0.3 * label.h:
            continue
        if abs(l.x0 - label.x0) > tol:
            continue
        out.append(l.text.strip())
    return out


def _strip_label(text: str, label: str) -> str:
    """Quita el texto de la etiqueta si el OCR la juntó con el valor ("NOMBRE MENDOZA")."""
    t = strip_accents(text.upper()).strip()
    compact = t.replace(" ", "")
    if compact.startswith(label):
        # Avanza en el texto original tantos caracteres no-espacio como tenga la etiqueta
        n, i = 0, 0
        while i < len(t) and n < len(label):
            if t[i] != " ":
                n += 1
            i += 1
        return t[i:].strip()
    return t


def _find_code(texts: list[str], pattern: re.Pattern, normalize) -> str | None:
    for t in texts:
        compact = _n(t)
        for label in ("CLAVEDEELECTOR", "CURP"):
            compact = compact.replace(label, "")
        for i in range(0, max(1, len(compact) - 17)):
            cand = normalize(compact[i:i + 18])
            if cand and len(cand) == 18 and pattern.match(cand):
                return cand
    return None


def extract(lines: list[Line]) -> FrontOcr:
    res = FrontOcr()
    if not lines:
        return res
    texts = [l.text for l in lines]

    i_nom = _label_idx(lines, "NOMBRE")
    i_dom = _label_idx(lines, "DOMICILIO")
    i_cla = _label_idx(lines, "CLAVEDEELECTOR", "CLAVEDE")

    if i_nom is not None:
        first = _strip_label(lines[i_nom].text, "NOMBRE")
        names = ([first] if first else []) + _block(lines, i_nom, i_dom, lines[i_nom])
        res.nombre_lineas = [t for t in names if _is_name(t) and not _n(t).startswith(("SEXO", "FECHADENAC"))]
    if i_dom is not None:
        first = _strip_label(lines[i_dom].text, "DOMICILIO")
        dom = ([first] if first else []) + _block(lines, i_dom, i_cla, lines[i_dom])
        res.domicilio_lineas = [t for t in dom if len(_n(t)) >= 3]

    clave_texts = [lines[i_cla].text] if i_cla is not None else []
    res.clave_elector = _find_code(clave_texts + texts, CLAVE_RE, normalize_clave)
    curp_texts = [t for t in texts if "CURP" in _n(t)] + texts
    res.curp = _find_code(curp_texts, CURP_RE, normalize_curp)

    for t in texts:
        d = parse_date(t)
        if d and 1900 < d.year < 2100:
            res.fecha_nacimiento = d.isoformat()
            break
    return res
