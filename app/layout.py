"""Localiza anverso y reverso de la credencial dentro de cada página.

En lugar de detectar el rectángulo de la tarjeta (los sellos del banco, marcos y
recuadros de las fotocopias lo confunden), se usa el texto que encuentra el OCR:

* Anverso: el encabezado "INSTITUTO NACIONAL ELECTORAL" + "CREDENCIAL PARA VOTAR".
* Reverso: las 3 líneas MRZ ("IDMEX...").

Con la posición y el ancho de esas anclas se estima el rectángulo de la tarjeta
(proporciones medidas sobre credenciales reales) y se agrega un margen.

Si en ninguna página aparece un ancla (credencial inclinada, muy pequeña o copia
muy tenue), el pipeline pide al modelo de visión que ubique las credenciales y
`analyze_boxes` repite la búsqueda de anclas dentro de esos recortes.
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

# Ancho (px) al que se lleva un recorte del localizador antes de pasarle el OCR:
# en una página completa a 250 dpi la credencial mide ~850 px y el OCR lee bien;
# si el localizador la encontró más chica, se amplía.
CROP_MIN_WIDTH = 1100

_ROTATIONS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


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
    method: str  # "ancla" | "localizador" | "pagina_completa"
    mrz_lines: list[str] = field(default_factory=list)


@dataclass
class Layout:
    front: Side | None
    back: Side | None
    rotated: bool
    pages: int
    warnings: list[str]
    page_lines: list[list[Line]] = field(default_factory=list)  # OCR de cada página (ya rotada si aplicó)
    page_images: list[np.ndarray] = field(default_factory=list)  # páginas (ya rotadas si aplicó)

    @property
    def anchored(self) -> bool:
        """Las dos caras se ubicaron con sus anclas (encabezado y MRZ)."""
        return bool(self.front and self.front.method == "ancla" and self.back and self.back.method == "ancla")


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


def _locate_with_rotations(img: np.ndarray, lines: list[Line], ocr_read):
    """Busca anclas en la imagen y, si no hay, en sus giros (90°, 180°, 270°).

    El giro más probable va primero (texto vertical → 90° en ambos sentidos; si no, 180°);
    después se prueban los que falten, porque una credencial de lado lee tan mal que el
    OCR a veces no deja claro si el texto es vertical. Devuelve (img, lines, (mrz, back, front), rotated).
    """
    found = _locate(img, lines)
    if found[0] or found[2] or not lines:
        return img, lines, found, False
    likely = [90, 270] if _mostly_vertical(lines) else [180]
    for deg in likely + [d for d in (90, 180, 270) if d not in likely]:
        r_img = cv2.rotate(img, _ROTATIONS[deg])
        r_lines = ocr_read(r_img)
        r = _locate(r_img, r_lines)
        if r[0] or r[2]:
            return r_img, r_lines, r, True
    return img, lines, found, False


def analyze(pages: list[np.ndarray], ocr_read) -> Layout:
    """Busca anverso y reverso en las páginas. `ocr_read(img) -> list[Line]`."""
    front: Side | None = None
    back: Side | None = None
    warnings: list[str] = []
    rotated = False
    page_lines: list[list[Line]] = []
    page_images: list[np.ndarray] = []

    for i, page in enumerate(pages):
        if front and back:
            # Ya se tienen las dos caras: las páginas restantes sólo se guardan (sin OCR)
            page_images.append(page)
            page_lines.append([])
            continue
        img, lines, (mrz, back_box, front_box), rot = _locate_with_rotations(page, ocr_read(page), ocr_read)
        rotated = rotated or rot
        page_images.append(img)
        page_lines.append(lines)

        if back is None and back_box:
            back = Side(i, back_box, _crop(img, back_box), _lines_in(lines, back_box), "ancla",
                        mrz_lines=[l.text for l in mrz])
        if front is None and front_box:
            front = Side(i, front_box, _crop(img, front_box), _lines_in(lines, front_box), "ancla")

    layout = Layout(front, back, rotated, len(pages), warnings, page_lines, page_images)
    _fill_missing(layout)
    return layout


def _fill_missing(layout: Layout) -> None:
    """Sin ancla: se manda la página completa al VLM para no perder el documento."""
    if layout.front is None and layout.page_images:
        img, lines = layout.page_images[0], layout.page_lines[0]
        layout.front = Side(0, Box(0, 0, img.shape[1], img.shape[0]), img, lines, "pagina_completa")
        layout.warnings.append("No se localizó el anverso; se analizó la página completa")
    if layout.back is None and layout.page_images:
        idx = 1 if len(layout.page_images) > 1 else 0
        img, lines = layout.page_images[idx], layout.page_lines[idx]
        layout.back = Side(idx, Box(0, 0, img.shape[1], img.shape[0]), img, lines, "pagina_completa")
        layout.warnings.append("No se localizó la MRZ del reverso; se analizó la página completa")


@dataclass
class CardBox:
    """Credencial ubicada por el modelo de visión en una página."""
    page: int
    box: Box
    lado: str  # "anverso" | "reverso" | "desconocido"
    rotacion: int = 0  # grados en sentido horario para enderezarla


def _box_sane(box: Box, w: int, h: int) -> bool:
    b = box.clip(w, h)
    frac = b.area / max(1, w * h)
    return 0.02 <= frac <= 0.95 and (b.x1 - b.x0) >= 80 and (b.y1 - b.y0) >= 50


def analyze_boxes(layout: Layout, cards: list[CardBox], ocr_read) -> Layout:
    """Reemplaza las caras sin ancla por los recortes del localizador.

    Cada recorte se endereza, se amplía si es chico y se vuelve a leer con el OCR:
    si ahí aparecen las anclas, la cara queda como "ancla" (con las líneas y la MRZ de
    siempre); si no, el recorte se usa tal cual como "localizador" (mejor que la página
    completa). Un recorte que no pasa las comprobaciones de tamaño se ignora.
    """
    front = layout.front if layout.front and layout.front.method == "ancla" else None
    back = layout.back if layout.back and layout.back.method == "ancla" else None
    fallback_front = fallback_back = None
    used = 0

    for card in cards:
        if front and back:
            break
        if not (0 <= card.page < len(layout.page_images)):
            continue
        page = layout.page_images[card.page]
        h, w = page.shape[:2]
        if not _box_sane(card.box, w, h):
            continue
        box = card.box.clip(w, h)
        crop = _crop(page, box)
        if card.rotacion in _ROTATIONS:
            crop = cv2.rotate(crop, _ROTATIONS[card.rotacion])
        ch, cw = crop.shape[:2]
        if cw < CROP_MIN_WIDTH:
            scale = CROP_MIN_WIDTH / cw
            crop = cv2.resize(crop, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_CUBIC)
        used += 1
        img, lines, (mrz, back_box, front_box), _ = _locate_with_rotations(crop, ocr_read(crop), ocr_read)
        layout.page_lines[card.page] = layout.page_lines[card.page] + lines
        if back is None and back_box:
            back = Side(card.page, back_box, _crop(img, back_box), _lines_in(lines, back_box), "ancla",
                        mrz_lines=[l.text for l in mrz])
        if front is None and front_box:
            front = Side(card.page, front_box, _crop(img, front_box), _lines_in(lines, front_box), "ancla")
        if back_box or front_box:
            continue
        whole = Side(card.page, Box(0, 0, img.shape[1], img.shape[0]), img, lines, "localizador")
        if card.lado == "reverso" and fallback_back is None:
            fallback_back = whole
        elif card.lado == "anverso" and fallback_front is None:
            fallback_front = whole
        elif card.lado == "desconocido":
            if fallback_front is None:
                fallback_front = whole
            elif fallback_back is None:
                fallback_back = whole

    layout.front, layout.back = front or fallback_front, back or fallback_back
    layout.warnings = [w for w in layout.warnings if not w.startswith("No se localizó")]
    layout.warnings.append(
        f"Localizador: {len(cards)} caja(s), {used} usable(s), "
        f"{sum(s.method == 'ancla' for s in (layout.front, layout.back) if s)} con ancla")
    _fill_missing(layout)
    return layout
