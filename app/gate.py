"""Decide si el documento es una credencial INE mexicana antes de gastar el modelo de visión.

Se suman las señales que ya encontró el OCR en todas las páginas:

* encabezado del anverso (ancla)          +3
* MRZ mexicana ("IDMEX...")               +3
* etiquetas propias de la credencial      +1 cada una (NOMBRE, DOMICILIO, CLAVE DE ELECTOR, CURP, ...)

Si el puntaje no llega a `ine_min_senales` se rechaza. Una MRZ de otro país
("IDUSA...", "P<ESP...") se rechaza directamente, aunque haya otras señales.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .layout import _MRZ1, _n, is_credencial, is_header
from .ocr import Line

LABELS = {
    "NOMBRE": "NOMBRE",
    "DOMICILIO": "DOMICILIO",
    "CLAVEDEELECTOR": "CLAVE DE ELECTOR",
    "CURP": "CURP",
    "SECCION": "SECCIÓN",
    "VIGENCIA": "VIGENCIA",
    "FECHADENACIMIENTO": "FECHA DE NACIMIENTO",
    "ANODEREGISTRO": "AÑO DE REGISTRO",
    "ESTADOSUNIDOSMEXICANOS": "ESTADOS UNIDOS MEXICANOS",
}
# Primera línea MRZ de documentos de viaje: tipo ("P<", "ID", "I<", "AC", "C<", "V<") + país de 3 letras.
# El tipo va seguido de "<" o forma "ID"/"AC": así la línea de nombres de una INE ("PEREZ<LOPEZ...") no cuenta.
_FOREIGN_MRZ = re.compile(r"^(?:P<|I[D<]|A[C<]|C<|V<)([A-Z]{3})[A-Z0-9<]{15,}$")


@dataclass
class Veredicto:
    es_ine: bool
    puntaje: int
    senales: list[str] = field(default_factory=list)
    motivo: str | None = None


def _mexican_like(country: str) -> bool:
    return SequenceMatcher(None, country, "MEX").ratio() >= 0.6  # a una letra de distancia del OCR


def foreign_mrz(lines: list[Line]) -> str | None:
    """Código de país de una MRZ que NO es mexicana, si aparece."""
    for l in lines:
        t = _n(l.text)
        if len(t) < 20 or t.count("<") < 2 or re.search(r"[1IL]?D[MN]EX", t) or _MRZ1.search(t):
            continue
        m = _FOREIGN_MRZ.match(t)
        if m and not _mexican_like(m.group(1)):
            return m.group(1)
    return None


def evaluate(page_lines: list[list[Line]], front_ancla: bool, mrz_mex: bool, min_senales: int) -> Veredicto:
    senales: list[str] = []
    puntaje = 0
    if front_ancla:
        senales.append("encabezado")
        puntaje += 3
    if mrz_mex:
        senales.append("mrz_mexicana")
        puntaje += 3

    all_lines = [l for lines in page_lines for l in lines]
    country = None if mrz_mex else foreign_mrz(all_lines)
    if country:
        return Veredicto(False, puntaje, senales, f"La MRZ corresponde a un documento de otro país ({country})")

    found: set[str] = set()
    for l in all_lines:
        t = _n(l.text)
        if not front_ancla and (is_header(t) or is_credencial(t)):
            found.add("INSTITUTO NACIONAL ELECTORAL" if is_header(t) else "CREDENCIAL PARA VOTAR")
        for key, label in LABELS.items():
            if t == key or (len(key) >= 8 and key in t):
                found.add(label)
    senales += sorted(found)
    puntaje += len(found)

    if puntaje < min_senales:
        motivo = ("No parece una credencial INE: no se encontró encabezado, MRZ mexicana ni etiquetas "
                  "de la credencial" if not senales else
                  f"No parece una credencial INE: sólo se encontró {', '.join(senales)}")
        return Veredicto(False, puntaje, senales, motivo)
    return Veredicto(True, puntaje, senales)
