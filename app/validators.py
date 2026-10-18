"""Validaciones de CURP, clave de elector y reglas cruzadas entre campos."""

import re
import unicodedata
from datetime import date

CURP_STATES = {
    "AS", "BC", "BS", "CC", "CL", "CM", "CS", "CH", "DF", "DG", "GT", "GR", "HG", "JC", "MC",
    "MN", "MS", "NT", "NL", "OC", "PL", "QT", "QR", "SP", "SL", "SR", "TC", "TS", "TL", "VZ",
    "YN", "ZS", "NE",
}

# Abreviaturas que la INE imprime al final del domicilio -> nombre del estado
STATE_ABBR = {
    "AGS": "AGUASCALIENTES", "BC": "BAJA CALIFORNIA", "BCS": "BAJA CALIFORNIA SUR",
    "CAMP": "CAMPECHE", "COAH": "COAHUILA", "COL": "COLIMA", "CHIS": "CHIAPAS",
    "CHIH": "CHIHUAHUA", "CDMX": "CIUDAD DE MEXICO", "DF": "CIUDAD DE MEXICO",
    "DGO": "DURANGO", "GTO": "GUANAJUATO", "GRO": "GUERRERO", "HGO": "HIDALGO",
    "JAL": "JALISCO", "MEX": "ESTADO DE MEXICO", "MICH": "MICHOACAN", "MOR": "MORELOS",
    "NAY": "NAYARIT", "NL": "NUEVO LEON", "OAX": "OAXACA", "PUE": "PUEBLA",
    "QRO": "QUERETARO", "QR": "QUINTANA ROO", "Q ROO": "QUINTANA ROO", "SLP": "SAN LUIS POTOSI",
    "SIN": "SINALOA", "SON": "SONORA", "TAB": "TABASCO", "TAMPS": "TAMAULIPAS",
    "TAMS": "TAMAULIPAS", "TLAX": "TLAXCALA", "VER": "VERACRUZ", "YUC": "YUCATAN",
    "ZAC": "ZACATECAS",
}

# Clave de entidad (2 dígitos) usada en la credencial (campo ESTADO y clave de elector)
STATE_CODES = {
    "01": "AGUASCALIENTES", "02": "BAJA CALIFORNIA", "03": "BAJA CALIFORNIA SUR",
    "04": "CAMPECHE", "05": "COAHUILA", "06": "COLIMA", "07": "CHIAPAS", "08": "CHIHUAHUA",
    "09": "CIUDAD DE MEXICO", "10": "DURANGO", "11": "GUANAJUATO", "12": "GUERRERO",
    "13": "HIDALGO", "14": "JALISCO", "15": "ESTADO DE MEXICO", "16": "MICHOACAN",
    "17": "MORELOS", "18": "NAYARIT", "19": "NUEVO LEON", "20": "OAXACA", "21": "PUEBLA",
    "22": "QUERETARO", "23": "QUINTANA ROO", "24": "SAN LUIS POTOSI", "25": "SINALOA",
    "26": "SONORA", "27": "TABASCO", "28": "TAMAULIPAS", "29": "TLAXCALA", "30": "VERACRUZ",
    "31": "YUCATAN", "32": "ZACATECAS",
}

# Clave de entidad de la CURP -> clave numérica que usa la clave de elector (estado de nacimiento)
CURP_STATE_TO_CODE = {
    "AS": "01", "BC": "02", "BS": "03", "CC": "04", "CL": "05", "CM": "06", "CS": "07", "CH": "08",
    "DF": "09", "DG": "10", "GT": "11", "GR": "12", "HG": "13", "JC": "14", "MC": "15", "MN": "16",
    "MS": "17", "NT": "18", "NL": "19", "OC": "20", "PL": "21", "QT": "22", "QR": "23", "SP": "24",
    "SL": "25", "SR": "26", "TC": "27", "TS": "28", "TL": "29", "VZ": "30", "YN": "31", "ZS": "32",
}
CODE_TO_CURP_STATE = {v: k for k, v in CURP_STATE_TO_CODE.items()}

CURP_RE = re.compile(r"^[A-Z][AEIOUX][A-Z]{2}\d{6}[HMX][A-Z]{2}[B-DF-HJ-NP-TV-Z]{3}[0-9A-Z]\d$")
CLAVE_RE = re.compile(r"^[A-Z]{6}\d{6}\d{2}[HMX]\d{3}$")

_CURP_ALPHABET = "0123456789ABCDEFGHIJKLMNÑOPQRSTUVWXYZ"
_TO_DIGIT = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2",
                           "S": "5", "B": "8", "G": "6", "T": "7"})
_TO_ALPHA = str.maketrans({"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "4": "A"})


def strip_accents(s: str) -> str:
    s = s.replace("Ñ", "\0")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s.replace("\0", "Ñ")


def norm_text(s: str | None) -> str:
    """Mayúsculas, sin acentos (Ñ -> N), espacios simples."""
    if not s:
        return ""
    s = strip_accents(s.upper()).replace("Ñ", "N")
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", s)).strip()


def _typed(s: str, kinds: str) -> str:
    out = []
    for ch, k in zip(s, kinds):
        if k == "d":
            ch = ch.translate(_TO_DIGIT)
        elif k == "a":
            ch = ch.translate(_TO_ALPHA)
        out.append(ch)
    return "".join(out)


# ------------------------------------------------------------------- CURP


def curp_check_digit(curp17: str) -> str:
    total = sum(_CURP_ALPHABET.index(c) * (18 - i) for i, c in enumerate(curp17))
    return str((10 - total % 10) % 10)


def normalize_curp(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^A-Z0-9Ñ]", "", strip_accents(raw.upper()))
    if len(s) != 18:
        return s or None
    # 4 letras, 6 dígitos, sexo, 2 letras estado, 3 consonantes, 1 libre, 1 dígito
    return _typed(s, "aaaa" + "dddddd" + "a" + "aa" + "aaa" + "*" + "d")


def curp_valid(curp: str | None) -> bool:
    if not curp or not CURP_RE.match(curp) or curp[11:13] not in CURP_STATES:
        return False
    if curp_birthdate(curp) is None:
        return False
    return curp_check_digit(curp[:17]) == curp[17]


def curp_birthdate(curp: str) -> date | None:
    try:
        yy, mm, dd = int(curp[4:6]), int(curp[6:8]), int(curp[8:10])
        century = 2000 if curp[16].isalpha() else 1900
        return date(century + yy, mm, dd)
    except (ValueError, IndexError):
        return None


# ------------------------------------------------------- clave de elector


def normalize_clave(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"[^A-Z0-9]", "", strip_accents(raw.upper()))
    if len(s) != 18:
        return s or None
    return _typed(s, "aaaaaa" + "dddddd" + "dd" + "a" + "ddd")


def clave_valid(clave: str | None) -> bool:
    return bool(clave and CLAVE_RE.match(clave) and clave_birthdate(clave) is not None
                and (clave[12:14] in STATE_CODES or clave[12:14] in ("87", "88")))


def clave_birthdate(clave: str) -> date | None:
    """La clave usa AAMMDD; el siglo se resuelve con la fecha de referencia si existe."""
    try:
        yy, mm, dd = int(clave[6:8]), int(clave[8:10]), int(clave[10:12])
        year = 2000 + yy if yy <= date.today().year % 100 else 1900 + yy
        return date(year, mm, dd)
    except (ValueError, IndexError):
        return None


# ------------------------------------------------------ reglas de nombres

_VOWELS = set("AEIOU")
_SKIP_FIRST = {"MARIA", "MA", "MA.", "JOSE", "J", "J."}
_PARTICLES = {"DA", "DAS", "DE", "DEL", "DER", "DI", "DIE", "DD", "EL", "LA", "LOS", "LAS",
              "LE", "LES", "MAC", "MC", "VAN", "VON", "Y"}


def _main_word(name: str, skip_common_first: bool = False) -> str:
    """Primera palabra significativa, en mayúsculas sin acentos pero conservando la Ñ."""
    clean = re.sub(r"[^A-ZÑ ]", " ", strip_accents(name.upper()))
    words = [w for w in clean.split() if w not in _PARTICLES]
    if skip_common_first and len(words) > 1 and words[0] in _SKIP_FIRST:
        words = words[1:]
    return words[0] if words else ""


def _first_inner(word: str, consonant: bool, fallback: str = "X") -> str:
    for ch in word[1:]:
        if ch == "Ñ":  # la Ñ no se usa como consonante interna
            continue
        if ch.isalpha() and ((ch not in _VOWELS) == consonant):
            return ch
    return fallback


def curp_name_prefix(paterno: str | None, materno: str | None, nombres: str | None) -> str | None:
    """Las 4 letras iniciales esperadas del CURP (sin aplicar el filtro de palabras altisonantes)."""
    p = _main_word(paterno or "")
    n = _main_word(nombres or "", skip_common_first=True)
    if not p or not n:
        return None
    m = _main_word(materno or "")
    return (p[0] + _first_inner(p, consonant=False) + (m[0] if m else "X") + n[0]).replace("Ñ", "X")


def clave_name_prefix(paterno: str | None, materno: str | None, nombres: str | None,
                      skip_common_first: bool = True) -> str | None:
    """Las 6 letras de la clave de elector: inicial + primera consonante interna de cada parte.

    Igual que la CURP, la INE salta MARIA / JOSE como primer nombre (confirmado en un lote de
    500: 42 claves sólo cuadran así y ninguna al revés). `skip_common_first=False` da la variante
    sin saltar, que la validación sigue aceptando por si aparece una excepción.
    """
    parts = [_main_word(paterno or ""), _main_word(materno or ""),
             _main_word(nombres or "", skip_common_first=skip_common_first)]
    if not parts[0] or not parts[2]:
        return None
    out = ""
    for w in parts:
        # Sin consonante interna (p. ej. "XU") la INE usa la segunda letra.
        out += (w[0] + _first_inner(w, True, w[1:2] or "X")) if w else "XX"
    return out


def curp_inner_consonants(paterno: str | None, materno: str | None, nombres: str | None) -> str | None:
    """Posiciones 14-16 del CURP: primera consonante interna de cada parte (Ñ -> X)."""
    p = _main_word(paterno or "")
    n = _main_word(nombres or "", skip_common_first=True)
    if not p or not n:
        return None
    m = _main_word(materno or "")

    def inner(w: str) -> str:
        for ch in w[1:]:
            if ch == "Ñ":
                return "X"
            if ch.isalpha() and ch not in _VOWELS:
                return ch
        return "X"

    return inner(p) + (inner(m) if m else "X") + inner(n)


def similarity(a: str | None, b: str | None) -> float:
    from difflib import SequenceMatcher

    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    m = re.search(r"(\d{1,2})\s*[/\-. ]\s*(\d{1,2})\s*[/\-. ]\s*(\d{4})", s)
    if m:
        try:
            return date(int(m[3]), int(m[2]), int(m[1]))
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    return None
