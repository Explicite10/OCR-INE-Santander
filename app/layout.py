"""Localiza anverso y reverso de la credencial dentro de cada página.

En lugar de detectar el rectángulo de la tarjeta (los sellos del banco, marcos y
recuadros de las fotocopias lo confunden), se usa el texto que encuentra el OCR:

* Anverso: el encabezado "INSTITUTO NACIONAL ELECTORAL" + "CREDENCIAL PARA VOTAR".
* Reverso: las 3 líneas MRZ ("IDMEX...").

Con la posición y el ancho de esas anclas se estima el rectángulo de la tarjeta
(proporciones medidas sobre credenciales reales) y se agrega un margen.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import cv2
import numpy as np

from .ocr import Line
from .validators import strip_accents

CARD_RATIO = 85.6 / 54.0  # ancho / alto (ID-1)
MARGIN = 0.07

# Proporciones medidas: ancho de tarjeta respecto al ancla y desplazamientos.
FRONT_W_PER_HEADER = 1.68
FRONT_LEFT_OFFSET = 0.366  # fracción del ancho de tarjeta a la izquierda del encabezado
FRONT_TOP_OFFSET = 0.077  # fracción del alto de tarjeta por encima del encabezado
BACK_W_PER_MRZ = 1.128
BACK_LEFT_OFFSET = 0.031
BACK_BOTTOM_OFFSET = 0.089

@dataclass
class Box:
    x0: int
    y0: int
    x1: int
    y1: int

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def clip(self, w: int, h: int) -> "Box":
        return Box(max(0, self.x0), max(0, self.y0), min(w, self.x1), min(h, self.y1))

    @property
    def area(self) -> int:
        return max(0, self.x1 - self.x0) * max(0, self.y1 - self.y0)


@dataclass
class Side:
    page: int
    box: Box
    image: np.ndarray
    lines: list[Line]  # líneas OCR dentro del recorte, ordenadas de arriba abajo
    method: str  # "ancla" | "pagina_completa"
    mrz_lines: list[str] = field(default_factory=list)


@dataclass
class Layout:
    front: Side | None
    back: Side | None
    rotated: bool
    pages: int
    warnings: list[str]


def _n(t: str) -> str:
    return re.sub(r"[^A-Z0-9<]", "", strip_accents(t.upper()))


def _fuzzy_in(text: str, target: str, threshold: float = 0.72) -> bool:
    t = _n(text)
    if target in t:
        return True
    if len(t) < len(target) * 0.6:
        return False
    return SequenceMatcher(None, t[: len(target) + 4], target).ratio() >= threshold


def is_header(t: str) -> bool:
    return _fuzzy_in(t, "INSTITUTONACIONALELECTORAL") or _fuzzy_in(t, "INSTITUTOFEDERALELECTORAL")


def is_credencial(t: str) -> bool:
    return _fuzzy_in(t, "CREDENCIALPARAVOTAR")


_MRZ1 = re.compile(r"[1IL]?D[MN]EX\d{5}")
_MRZ2 = re.compile(r"\d{6}[0-9OD][HMX]\d{6}[0-9OD]ME")


def _x_overlap(a: Line, b: Line) -> float:
    inter = min(a.x1, b.x1) - max(a.x0, b.x0)
    return inter / max(1, min(a.w, b.w))


def find_mrz(lines: list[Line]) -> list[Line] | None:
    """Las líneas MRZ (2 ó 3) agrupadas geométricamente."""
    l1 = next((l for l in lines if _MRZ1.search(_n(l.text))), None)
    if l1 is None:
        l2 = next((l for l in lines if _MRZ2.search(_n(l.text))), None)
        if l2 is None:
            return None
        below = [l for l in lines if l.cy > l2.cy and l.cy - l2.cy < 2.5 * l2.h and _x_overlap(l, l2) > 0.5]
        return [l2] + sorted(below, key=lambda l: l.cy)[:1]
    below = [
        l for l in lines
        if l is not l1 and l.cy > l1.cy + 0.4 * l1.h and l.cy - l1.cy < 4.5 * l1.h and _x_overlap(l, l1) > 0.5
        and len(_n(l.text)) >= 12
    ]
    below.sort(key=lambda l: l.cy)
    return [l1] + below[:2]


def _back_box(mrz: list[Line]) -> Box:
    x0 = min(l.x0 for l in mrz)
    x1 = max(l.x1 for l in mrz)
    y1 = max(l.y1 for l in mrz)
    if len(mrz) < 3:  # falta una línea: el ancho medido es igual, el fondo queda corto
        y1 += int(1.2 * sum(l.h for l in mrz) / len(mrz)) * (3 - len(mrz))
    card_w = (x1 - x0) * BACK_W_PER_MRZ
    card_h = card_w / CARD_RATIO
    left = x0 - BACK_LEFT_OFFSET * card_w
    bottom = y1 + BACK_BOTTOM_OFFSET * card_h
    return _with_margin(left, bottom - card_h, card_w, card_h)


def _front_box(header: Line) -> Box:
    n = len(_n(header.text))
    # Si el OCR cortó el encabezado, se compensa el ancho con el número de letras.
    w = header.w * min(1.5, max(1.0, 26 / max(n, 1)))
    card_w = w * FRONT_W_PER_HEADER
    card_h = card_w / CARD_RATIO
    return _with_margin(header.x0 - FRONT_LEFT_OFFSET * card_w, header.y0 - FRONT_TOP_OFFSET * card_h,
                        card_w, card_h)


def _front_box_from_labels(nombre: Line, clave: Line) -> Box:
    card_h = max(1, clave.y0 - nombre.y0) / 0.40
    card_w = card_h * CARD_RATIO
    return _with_margin(nombre.x0 - 0.36 * card_w, nombre.y0 - 0.31 * card_h, card_w, card_h)


def _with_margin(left: float, top: float, w: float, h: float) -> Box:
    return Box(int(left - MARGIN * w), int(top - MARGIN * h), int(left + w * (1 + MARGIN)),
               int(top + h * (1 + MARGIN)))


def find_front(lines: list[Line], exclude: Box | None) -> Box | None:
    def ok(l: Line) -> bool:
        return exclude is None or not exclude.contains(l.cx, l.cy)

    creds = [l for l in lines if ok(l) and is_credencial(l.text)]
    headers = [l for l in lines if ok(l) and is_header(l.text)]
    # El reverso también trae "INSTITUTO NACIONAL ELECTORAL" impreso: se exige que
    # "CREDENCIAL PARA VOTAR" esté justo debajo o que haya etiquetas del anverso.
    for h in headers:
        if any(0 < c.cy - h.cy < 3 * h.h and abs(c.x0 - h.x0) < 2 * h.h for c in creds):
            return _front_box(h)
    for c in creds:  # encabezado ilegible: se estima a partir de "CREDENCIAL PARA VOTAR"
        fake = Line("INSTITUTO NACIONAL ELECTORAL", 0, c.x0, c.y0 - c.h, c.x0 + int(c.w * 1.45), c.y0)
        return _front_box(fake)
    nombre = next((l for l in lines if ok(l) and _n(l.text) == "NOMBRE"), None)
    clave = next((l for l in lines if ok(l) and "CLAVEDEELECTOR" in _n(l.text)), None)
    if nombre and clave and clave.y0 > nombre.y0:
        return _front_box_from_labels(nombre, clave)
    return None


def _lines_in(lines: list[Line], box: Box) -> list[Line]:
    return sorted((l for l in lines if box.contains(l.cx, l.cy)), key=lambda l: (l.cy, l.x0))


def _crop(img: np.ndarray, box: Box) -> np.ndarray:
    return img[box.y0:box.y1, box.x0:box.x1].copy()


def _locate(img: np.ndarray, lines: list[Line]) -> tuple[list[Line] | None, Box | None, Box | None]:
    # PaddleOCR también lee texto vertical: sólo cuentan las líneas horizontales,
    # si no, una página girada 90° "encontraría" anclas de lado.
    horiz = [l for l in lines if l.w >= l.h]
    mrz = find_mrz(horiz)
    h, w = img.shape[:2]
    back_box = _back_box(mrz).clip(w, h) if mrz else None
    if back_box is not None and back_box.area == 0:
        mrz, back_box = None, None
    front_box = find_front(horiz, back_box)
    if front_box is not None:
        front_box = front_box.clip(w, h)
        if front_box.area == 0:
            front_box = None
    return mrz, back_box, front_box


def _mostly_vertical(lines: list[Line]) -> bool:
    long = [l for l in lines if len(l.text) >= 6]
    return sum(l.h > 1.5 * l.w for l in long) > sum(l.w > 1.5 * l.h for l in long)


def analyze(pages: list[np.ndarray], ocr_read) -> Layout:
    """Busca anverso y reverso en las páginas. `ocr_read(img) -> list[Line]`."""
    front: Side | None = None
    back: Side | None = None
    warnings: list[str] = []
    rotated = False
    page_cache: list[tuple[np.ndarray, list[Line]]] = []

    for i, page in enumerate(pages):
        img, lines = page, ocr_read(page)
        page_cache.append((img, lines))
        mrz, back_box, front_box = _locate(img, lines)
        if not (mrz or front_box) and len(lines) >= 3:
            # Página girada: si el texto es vertical se prueban 90° en ambos sentidos, si no, 180°.
            tries = ((cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE) if _mostly_vertical(lines)
                     else (cv2.ROTATE_180,))
            for rot in tries:
                r_img = cv2.rotate(page, rot)
                r_lines = ocr_read(r_img)
                r = _locate(r_img, r_lines)
                if r[0] or r[2]:
                    img, lines, (mrz, back_box, front_box) = r_img, r_lines, r
                    rotated = True
                    break

        if back is None and back_box:
            back = Side(i, back_box, _crop(img, back_box), _lines_in(lines, back_box), "ancla",
                        mrz_lines=[l.text for l in mrz])
        if front is None and front_box:
            front = Side(i, front_box, _crop(img, front_box), _lines_in(lines, front_box), "ancla")
        if front and back:
            break

    # Sin ancla: se manda la página completa al VLM para no perder el documento.
    if front is None and page_cache:
        img, lines = page_cache[0]
        front = Side(0, Box(0, 0, img.shape[1], img.shape[0]), img, lines, "pagina_completa")
        warnings.append("No se localizó el anverso; se analizó la página completa")
    if back is None and page_cache:
        idx = 1 if len(page_cache) > 1 else 0
        img, lines = page_cache[idx]
        back = Side(idx, Box(0, 0, img.shape[1], img.shape[0]), img, lines, "pagina_completa")
        warnings.append("No se localizó la MRZ del reverso; se analizó la página completa")

    return Layout(front, back, rotated, len(pages), warnings)
