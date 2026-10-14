"""Decide si el documento es una credencial INE mexicana antes de gastar el modelo de visión.

Reglas, en orden:

1. Señales negativas (texto de OTRO documento: licencia, pasaporte, IMSS, cédula...) → rechazo inmediato.
   Incluye la MRZ de pasaporte ("P<MEX...", de cualquier país) y la MRZ de credenciales de otro país ("IDUSA...").
2. Debe haber al menos UNA señal exclusiva de la INE: encabezado del anverso (ancla), MRZ "IDMEX",
   "CLAVE DE ELECTOR", "CREDENCIAL PARA VOTAR", "INSTITUTO NACIONAL/FEDERAL ELECTORAL" o "AÑO DE REGISTRO".
   Las etiquetas genéricas (NOMBRE, CURP, VIGENCIA, ESTADOS UNIDOS MEXICANOS...) también las traen
   licencias y pasaportes: sólo suman puntaje, no bastan solas.
3. Puntaje total (exclusivas +3, genéricas +1) ≥ `ine_min_senales`.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .layout import _MRZ1, _n, is_credencial, is_header
from .ocr import Line

EXCLUSIVE_LABELS = {
    "CLAVEDEELECTOR": "CLAVE DE ELECTOR",
    "ANODEREGISTRO": "AÑO DE REGISTRO",
}
GENERIC_LABELS = {
    "NOMBRE": "NOMBRE",
    "DOMICILIO": "DOMICILIO",
    "CURP": "CURP",
    "SECCION": "SECCIÓN",
    "VIGENCIA": "VIGENCIA",
    "FECHADENACIMIENTO": "FECHA DE NACIMIENTO",
    "ESTADOSUNIDOSMEXICANOS": "ESTADOS UNIDOS MEXICANOS",
}
# Texto que NO aparece en una INE y sí en otros documentos de identidad mexicanos.
NEGATIVE = {
    "LICENCIAPARACONDUCIR": "licencia de conducir",
    "LICENCIADECONDUCIR": "licencia de conducir",
    "LICENCIADEMANEJO": "licencia de conducir",
    "SECRETARIADEMOVILIDAD": "licencia de conducir",
    "TIPODESANGRE": "licencia de conducir",
    "PASAPORTE": "pasaporte",
    "PASSPORT": "pasaporte",
    "CEDULAPROFESIONAL": "cédula profesional",
    "ACTADENACIMIENTO": "acta de nacimiento",
    "SEGUROSOCIAL": "credencial del IMSS",
    "INAPAM": "credencial del INAPAM",
    "CARTILLA": "cartilla militar",
    "TARJETADECIRCULACION": "tarjeta de circulación",
    "RESIDENTETEMPORAL": "tarjeta de residente (INM)",
    "RESIDENTEPERMANENTE": "tarjeta de residente (INM)",
    "INSTITUTONACIONALDEMIGRACION": "documento del INM",
    "SITUACIONFISCAL": "constancia del SAT",
}
NEGATIVE_EXACT = {"RFC": "licencia o documento fiscal (trae RFC)"}

_PASSPORT_MRZ = re.compile(r"^P<([A-Z]{3})[A-Z0-9<]{15,}$")
# Primera línea MRZ de credenciales (TD1): tipo "ID"/"I<"/"AC"/"C<"/"V<" + país de 3 letras.
_TD1_MRZ = re.compile(r"^(?:I[D<]|A[C<]|C<|V<)([A-Z]{3})[A-Z0-9<]{15,}$")


@dataclass
class Veredicto:
    es_ine: bool
    puntaje: int
    senales: list[str] = field(default_factory=list)
    motivo: str | None = None


def _mexican_like(country: str) -> bool:
    return SequenceMatcher(None, country, "MEX").ratio() >= 0.6  # a una letra de distancia del OCR


def other_document(lines: list[Line], mrz_mex: bool) -> str | None:
    """Describe el documento ajeno detectado (p. ej. "pasaporte"), o None."""
    for l in lines:
        t = _n(l.text)
        if t in NEGATIVE_EXACT:
            return NEGATIVE_EXACT[t]
        for key, what in NEGATIVE.items():
            if key in t:
                return what
        if len(t) >= 20 and t.count("<") >= 2:
            m = _PASSPORT_MRZ.match(t)
            if m:
                return f"pasaporte ({m.group(1)})"
            if not mrz_mex and not re.search(r"[1IL]?D[MN]EX", t) and not _MRZ1.search(t):
                m = _TD1_MRZ.match(t)
                if m and not _mexican_like(m.group(1)):
                    return f"documento de otro país ({m.group(1)})"
    return None


def evaluate(page_lines: list[list[Line]], front_ancla: bool, mrz_mex: bool, min_senales: int) -> Veredicto:
    all_lines = [l for lines in page_lines for l in lines]

    other = other_document(all_lines, mrz_mex)
    if other:
        return Veredicto(False, 0, [], f"No es una credencial INE: parece {other}")

    exclusive: set[str] = set()
    generic: set[str] = set()
    if front_ancla:
        exclusive.add("encabezado")
    if mrz_mex:
        exclusive.add("mrz_mexicana")
    for l in all_lines:
        t = _n(l.text)
        if is_header(t):
            exclusive.add("INSTITUTO NACIONAL ELECTORAL")
        if is_credencial(t):
            exclusive.add("CREDENCIAL PARA VOTAR")
        for key, label in EXCLUSIVE_LABELS.items():
            if key in t:
                exclusive.add(label)
        for key, label in GENERIC_LABELS.items():
            if t == key or (len(key) >= 8 and key in t):
                generic.add(label)

    senales = sorted(exclusive) + sorted(generic)
    puntaje = 3 * len(exclusive) + len(generic)
    if not exclusive:
        motivo = ("No es una credencial INE: no se encontró encabezado del INE, MRZ mexicana ni clave de elector"
                  + (f" (sólo {', '.join(senales)})" if senales else ""))
        return Veredicto(False, puntaje, senales, motivo)
    if puntaje < min_senales:
        return Veredicto(False, puntaje, senales, f"No es una credencial INE: señales insuficientes ({puntaje})")
    return Veredicto(True, puntaje, senales)
