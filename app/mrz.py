"""Lectura de la zona MRZ (formato TD1, 3 líneas x 30 caracteres) del reverso de la INE.

Estructura que usa la INE:

    Línea 1: IDMEX + CIC (9) + dígito + "<<" + sección (4) + identificador (9)
    Línea 2: nacimiento AAMMDD + dígito + sexo + vigencia AAMMDD + dígito + "MEX"
             + opcional (11, incluye los 2 dígitos que acompañan al año de registro)
             + dígito compuesto
    Línea 3: APELLIDO1<APELLIDO2<<NOMBRE1<NOMBRE2 (truncado a 30)

Los errores típicos del OCR (O/0, I/1, S/5, B/8...) se corrigen según el tipo de
cada posición, y si un dígito verificador no cuadra se intenta una sola
sustitución entre caracteres que se confunden visualmente.
"""

import re
from dataclasses import dataclass, field
from datetime import date

LINE_LEN = 30

_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "T": "1",
                           "Z": "2", "S": "5", "B": "8", "G": "6", "A": "4", "E": "8"})
_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "4": "A"})
_FILLER_LIKE = str.maketrans({"(": "<", "[": "<", "{": "<", "‹": "<", "«": "<", "K": "<", "C": "<"})

# Pares que el OCR suele confundir; se usan para reparar con el dígito verificador.
_CONFUSABLE = {
    "0": "86935", "1": "74", "2": "7", "3": "8956", "4": "19", "5": "6389",
    "6": "5803", "7": "12", "8": "035693", "9": "830",
}


def check_digit(s: str) -> int:
    weights = (7, 3, 1)
    total = 0
    for i, ch in enumerate(s):
        if ch.isdigit():
            v = int(ch)
        elif "A" <= ch <= "Z":
            v = ord(ch) - 55
        else:
            v = 0
        total += v * weights[i % 3]
    return total % 10


@dataclass
class MrzCheck:
    nombre: str
    valido: bool
    corregido: bool = False


@dataclass
class MrzResult:
    lineas: list[str]
    cic: str | None = None
    seccion: str | None = None
    identificador_ciudadano: str | None = None
    numero_ocr: str | None = None
    fecha_nacimiento: date | None = None
    sexo: str | None = None
    fecha_vigencia: date | None = None
    nacionalidad: str | None = None
    anio_registro_sufijo: str | None = None
    apellido_paterno: str | None = None
    apellido_materno: str | None = None
    nombres: str | None = None
    checks: list[MrzCheck] = field(default_factory=list)

    @property
    def valido(self) -> bool:
        return bool(self.checks) and all(c.valido for c in self.checks)

    @property
    def corregido(self) -> bool:
        return any(c.corregido for c in self.checks)

    @property
    def n_validos(self) -> int:
        return sum(c.valido for c in self.checks)


# ---------------------------------------------------------------- normalización


def _clean(s: str) -> str:
    s = s.upper().replace(" ", "").replace("«", "<<")
    return s


def _fit(s: str) -> str:
    return (s + "<" * LINE_LEN)[:LINE_LEN]


def _map(s: str, kind: str) -> str:
    """kind por carácter: d=dígito, a=letra, f=relleno '<', x=dígito o '<', n=letra o '<', *=libre."""
    out = []
    for ch, k in zip(s, kind):
        if k == "d":
            ch = ch.translate(_TO_DIGIT)
        elif k == "a":
            ch = ch.translate(_TO_ALPHA)
        elif k == "f":
            ch = "<"
        elif k == "x":
            ch = ch.translate(_FILLER_LIKE)
            if ch != "<":
                ch = ch.translate(_TO_DIGIT)
        elif k == "n":
            ch = ch.translate(_FILLER_LIKE) if ch in "([{‹«" else ch
            if ch != "<":
                ch = ch.translate(_TO_ALPHA)
        out.append(ch)
    return "".join(out)


L1_KIND = "aaaaa" + "d" * 9 + "d" + "ff" + "d" * 13
L2_KIND = "d" * 6 + "d" + "a" + "d" * 6 + "d" + "aaa" + "x" * 11 + "d"
L3_KIND = "n" * 30


def normalize_line1(raw: str) -> str:
    s = _clean(raw)
    m = re.search(r"[1IL]?D[MN]EX", s)
    if m:
        s = "IDMEX" + s[m.end():]
    elif not s.startswith("IDMEX"):
        s = "IDMEX" + s[5:] if len(s) >= 29 else s
    s = _fit(s)
    s = s[:15] + "<<" + s[17:] if s[15:17] != "<<" else s
    return _map(s, L1_KIND)


def normalize_line2(raw: str) -> str:
    s = _fit(_clean(raw))
    s = _map(s, L2_KIND)
    if s[7] not in "HMX<":
        s = s[:7] + {"N": "M", "W": "M", "4": "H", "K": "H"}.get(s[7], s[7]) + s[8:]
    return s[:15] + "MEX" + s[18:] if s[15:18] in ("M3X", "MFX", "NEX", "MEK", "HEX") else s


def normalize_line3(raw: str) -> str:
    return _map(_fit(_clean(raw)), L3_KIND)


# ---------------------------------------------------------------- reparación


def _repair_candidates(value: str, expected: str) -> list[tuple[str, str]]:
    """Sustituciones de UN carácter confusable que hacen cuadrar el dígito verificador.
    Devuelve pares (valor, dígito)."""
    candidates = set()
    for i, ch in enumerate(value):
        for alt in _CONFUSABLE.get(ch, ""):
            cand = value[:i] + alt + value[i + 1:]
            if str(check_digit(cand)) == expected:
                candidates.add((cand, expected))
    for alt in _CONFUSABLE.get(expected, ""):
        if str(check_digit(value)) == alt:
            candidates.add((value, alt))  # el que estaba mal leído es el verificador
    return sorted(candidates)


def _verify(l1: str, l2: str, start: int, length: int, line: int, nombre: str,
            checks: list[MrzCheck]) -> tuple[str, str]:
    lines = [l1, l2]
    src = lines[line]
    value, expected = src[start:start + length], src[start + length]
    if not _has_content(value, nombre):
        # Un campo vacío ("<<<<<<") da dígito verificador 0: el VLM, ante una hoja sin credencial,
        # devuelve renglones de puro relleno que "cuadran". Sin contenido real no hay validación.
        checks.append(MrzCheck(nombre, False))
        return l1, l2
    if expected.isdigit() and str(check_digit(value)) == expected:
        checks.append(MrzCheck(nombre, True))
        return l1, l2
    options = []
    if expected.isdigit():
        for cand, digit in _repair_candidates(value, expected):
            if nombre != "mrz_cic" and _date(cand, future=nombre == "mrz_vigencia") is None:
                continue  # una "reparación" que no es fecha válida se descarta
            fixed = lines.copy()
            fixed[line] = src[:start] + cand + digit + src[start + length + 1:]
            options.append(fixed)
    if len(options) > 1:
        # Varias reparaciones posibles: sólo se acepta la que además cuadra con el dígito compuesto.
        options = [o for o in options if _composite_ok(o[0], o[1])]
    if len(options) == 1:
        checks.append(MrzCheck(nombre, True, corregido=True))
        return options[0][0], options[0][1]
    checks.append(MrzCheck(nombre, False))
    return l1, l2


def _has_content(value: str, nombre: str) -> bool:
    """El campo trae dígitos de verdad y no sólo relleno ni ceros. (Una fecha con un dígito mal
    leído, p. ej. mes 13, sí cuenta: la reparación con el dígito verificador la corrige después.)"""
    return value.isdigit() and value.strip("0") != ""


def _composite(l1: str, l2: str) -> str:
    return l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]


def _composite_ok(l1: str, l2: str) -> bool:
    if not (_has_content(l1[5:14], "mrz_cic") and _has_content(l2[0:6], "mrz_fecha_nacimiento")):
        return False  # sin CIC ni fecha reales el compuesto "cuadra" con puro relleno
    return l2[29].isdigit() and str(check_digit(_composite(l1, l2))) == l2[29]


# ---------------------------------------------------------------- parseo


def _date(yymmdd: str, *, future: bool) -> date | None:
    if not yymmdd.isdigit():
        return None
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    if future:
        year = 2000 + yy
    else:
        year = 2000 + yy if yy <= date.today().year % 100 else 1900 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def _names(l3: str) -> tuple[str | None, str | None, str | None]:
    parts = l3.rstrip("<").split("<<", 1)
    surnames = [p for p in parts[0].split("<") if p]
    given = " ".join(p for p in parts[1].split("<") if p) if len(parts) > 1 else None
    pat = surnames[0] if surnames else None
    mat = " ".join(surnames[1:]) or None
    return pat, mat, given or None


def parse(lines: list[str]) -> MrzResult | None:
    """Recibe las 3 líneas crudas (en orden) y devuelve los datos con sus validaciones."""
    if len(lines) < 2:
        return None
    l1 = normalize_line1(lines[0])
    l2 = normalize_line2(lines[1])
    l3 = normalize_line3(lines[2]) if len(lines) > 2 else None

    checks: list[MrzCheck] = []
    l1, l2 = _verify(l1, l2, 5, 9, 0, "mrz_cic", checks)
    l1, l2 = _verify(l1, l2, 0, 6, 1, "mrz_fecha_nacimiento", checks)
    l1, l2 = _verify(l1, l2, 8, 6, 1, "mrz_vigencia", checks)

    checks.append(MrzCheck("mrz_compuesto", _composite_ok(l1, l2)))

    r = MrzResult(lineas=[l1, l2] + ([l3] if l3 else []), checks=checks)
    r.cic = l1[5:14] if l1[5:14].isdigit() else None
    opt = l1[17:30]
    if opt.isdigit():
        r.numero_ocr = opt
        r.seccion = opt[:4]
        r.identificador_ciudadano = opt[4:]
    r.fecha_nacimiento = _date(l2[0:6], future=False)
    r.sexo = l2[7] if l2[7] in "HMX" else None
    r.fecha_vigencia = _date(l2[8:14], future=True)
    r.nacionalidad = l2[15:18]
    if l2[19:21].isdigit():
        r.anio_registro_sufijo = l2[19:21]
    if l3:
        r.apellido_paterno, r.apellido_materno, r.nombres = _names(l3)
    return r


def best(*candidates: MrzResult | None) -> MrzResult | None:
    """Elige la lectura con más verificaciones válidas (prefiere las no corregidas)."""
    valid = [c for c in candidates if c is not None]
    if not valid:
        return None
    return max(valid, key=lambda c: (c.n_validos, not c.corregido, bool(c.nombres)))


def find_mrz_lines(texts: list[str]) -> list[str] | None:
    """Ubica las 3 líneas MRZ dentro de una lista de textos (ordenados de arriba abajo)."""
    idx = None
    for i, t in enumerate(texts):
        if re.search(r"[1IL]?D[MN]EX\d", _clean(t)):
            idx = i
            break
    if idx is None:
        # Sin línea 1: buscar la línea 2 (fecha + sexo + vigencia + MEX)
        for i, t in enumerate(texts):
            if re.search(r"\d{7}[HMX]\d{7}MEX", _clean(t)):
                return [""] + [texts[i]] + ([texts[i + 1]] if i + 1 < len(texts) else [])
        return None
    return texts[idx:idx + 3]
