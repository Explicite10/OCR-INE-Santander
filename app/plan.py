"""
Validaciones del "Plan de atención - Validación documental", sección INE.

Mismo esquema que app/plan.py de OCR-INM-OpenSource y OCR-INM-DocumentAI: trabaja sobre el JSON
ya homologado (`frente`, `atras.mrz`, `clasificacion.categoria`), así que el bloque
`validacion_plan` tiene la misma forma en las APIs de INE y de forma migratoria.

Responde las 7 preguntas del plan con SI / NO, en el mismo orden:

  1. ¿Es una hoja en blanco?              (pasa si NO)
  2. ¿La imagen es ilegible?              (pasa si NO)
  3. ¿Es una INE?                         (pasa si SI)
  4. ¿Está vigente?                       (pasa si SI)
  5. ¿El nombre coincide?                 (vs captura de la solicitud)
  6. ¿La CURP coincide?                   (vs captura de la solicitud)
  7. ¿La fecha de nacimiento coincide?    (vs captura de la solicitud)

Si no se cumplen al 100 %: mensaje "Revisar documento y motivo de rechazo" + motivos.

Valores de `resultado`:
  SI / NO          respuesta a la pregunta
  NO_VERIFICADO    faltan datos para responder (p. ej. no se envió el dato de la solicitud): bloquea

A diferencia de forma migratoria y pasaporte, el plan no exime la CURP en INE ("siempre y cuando exista el
dato"): todo titular de INE tiene CURP impresa, así que si la solicitud no la trae, la pregunta bloquea.

La "validación ante lista nominal" del plan es una consulta al INE y queda fuera de este servicio.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import validators as v

MENSAJE_OK = "Documento válido: cumple con todas las validaciones."
MENSAJE_RECHAZO = "Revisar documento y motivo de rechazo"


@dataclass
class Solicitud:
    """Datos capturados en la solicitud. Todos opcionales."""
    nombre: Optional[str] = None             # nombre completo, en cualquier orden
    apellido_paterno: Optional[str] = None
    apellido_materno: Optional[str] = None
    nombres: Optional[str] = None
    curp: Optional[str] = None
    fecha_nacimiento: Optional[str] = None   # AAAA-MM-DD o DD/MM/AAAA

    def nombre_completo(self) -> Optional[str]:
        if self.nombre and self.nombre.strip():
            return self.nombre
        partes = [p for p in (self.apellido_paterno, self.apellido_materno, self.nombres) if p and p.strip()]
        return " ".join(partes) or None

    def vacia(self) -> bool:
        return not any((self.nombre_completo(), self.curp, self.fecha_nacimiento))


# ------------------------------------------------------------------ normalizacion


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()


def _tokens(nombre: str) -> list[str]:
    return sorted(t for t in re.split(r"[^A-Z]+", _ascii(nombre)) if t)


def _fecha(s: Optional[str]) -> Optional[str]:
    if not s or not s.strip():
        return None
    d = v.parse_date(s.strip())  # AAAA-MM-DD, DD/MM/AAAA, DD-MM-AAAA
    return d.isoformat() if d else None


# Pares que el OCR confunde con frecuencia en la CURP
_CONFUNDIBLES = {frozenset(p) for p in ("O0", "I1", "S5", "B8", "Z2", "G6", "Q0", "D0")}


# ------------------------------------------------------------------ validaciones


def _item(id_: str, pregunta: str, resultado: str, ok: bool, detalle: str) -> dict:
    return {"id": id_, "pregunta": pregunta, "resultado": resultado, "ok": ok, "detalle": detalle}


def _nombre_completo(frente: dict) -> Optional[str]:
    partes = [frente.get(k) for k in ("apellido_paterno", "apellido_materno", "nombre")]
    return " ".join(p for p in partes if p) or None


def vigencia(frente: dict, mrz: dict, regla: str = "anio", hoy: Optional[date] = None) -> tuple[Optional[bool], str]:
    """(vigente, detalle). La INE vence el 31 de diciembre del año de vigencia.
    regla 'anio': año de vigencia >= año actual (plan de atención). regla 'fecha': vencimiento >= hoy.
    Manda la MRZ si su dígito de control cuadra; si no, el año impreso en el anverso."""
    hoy = hoy or date.today()
    if mrz.get("vigencia") and (mrz.get("digitos_control") or {}).get("vigencia"):
        venc, fuente = date.fromisoformat(mrz["vigencia"]), "MRZ"
    elif str(frente.get("vigencia") or "").isdigit():
        venc, fuente = date(int(frente["vigencia"]), 12, 31), "anverso"
    else:
        return None, "No se pudo leer la vigencia."
    if regla == "fecha":
        return venc >= hoy, f"Vence el {venc.isoformat()} ({fuente}); se compara contra la fecha de hoy ({hoy.isoformat()})."
    return venc.year >= hoy.year, f"Año de vigencia {venc.year} ({fuente}) contra año actual {hoy.year}."


def _ilegible(categoria: str, frente: dict, mrz: dict) -> tuple[bool, str]:
    if categoria == "hoja_en_blanco":
        return True, "No se leyó texto en el documento."
    if categoria != "ine":
        return False, "Se leyó texto, pero el documento no es una credencial INE."
    esenciales = {"nombre": _nombre_completo(frente), "fecha_nacimiento": frente.get("fecha_nacimiento"),
                  "curp": frente.get("curp"), "clave_elector": frente.get("clave_elector")}
    faltan = [k for k, val in esenciales.items() if not val]
    mrz_ok = bool(mrz) and "error" not in mrz
    if (not mrz_ok and faltan) or len(faltan) >= 2:
        return True, ("No se pudieron leer datos esenciales: " + ", ".join(faltan)
                      + ("; la MRZ del reverso tampoco es legible." if not mrz_ok else "."))
    return False, "Los datos esenciales se leyeron" + (" y la MRZ es legible." if mrz_ok else "; la MRZ no es legible.")


def _comparar_nombre(doc: Optional[str], sol: Optional[str]) -> tuple[str, bool, str]:
    if not sol:
        return "NO_VERIFICADO", False, "No se envió el nombre de la solicitud."
    if not doc:
        return "NO_VERIFICADO", False, "No se pudo leer el nombre en la credencial."
    a, b = _tokens(doc), _tokens(sol)
    if a == b:
        return "SI", True, "El nombre coincide con la captura (sin importar orden, acentos ni mayúsculas)."
    faltan = sorted(set(b) - set(a))
    sobran = sorted(set(a) - set(b))
    det = "El nombre no coincide."
    if faltan:
        det += f" En la solicitud y no en la credencial: {' '.join(faltan)}."
    if sobran:
        det += f" En la credencial y no en la solicitud: {' '.join(sobran)}."
    return "NO", False, det


def _comparar_curp(doc: Optional[str], sol: Optional[str], frente: dict) -> tuple[str, bool, str]:
    if not sol:
        return "NO_VERIFICADO", False, "No se envió la CURP de la solicitud."
    sol_n = re.sub(r"[^A-Z0-9]", "", _ascii(sol))
    if not doc:
        return "NO_VERIFICADO", False, "No se pudo leer la CURP en la credencial."
    if doc == sol_n:
        return "SI", True, "La CURP coincide con la captura."
    if len(doc) == len(sol_n) == 18:
        difs = [(x, y) for x, y in zip(doc, sol_n) if x != y]
        if len(difs) <= 2 and all(frozenset((x, y)) in _CONFUNDIBLES for x, y in difs):
            # La lectura de la credencial difiere solo en caracteres que el OCR confunde.
            # Se acepta si la CURP capturada es coherente con la fecha de nacimiento y el sexo leídos.
            fn, sexo = frente.get("fecha_nacimiento"), frente.get("sexo")
            fecha_ok = bool(fn) and sol_n[4:10] == fn[2:4] + fn[5:7] + fn[8:10]
            sexo_ok = sexo not in ("H", "M") or sol_n[10] == sexo
            if fecha_ok and sexo_ok:
                return "SI", True, ("La CURP coincide; la lectura de la credencial difiere solo en "
                                    f"{len(difs)} carácter(es) que el OCR suele confundir, y la CURP capturada "
                                    "es coherente con la fecha de nacimiento y el sexo de la credencial.")
    return "NO", False, f"La CURP no coincide: credencial '{doc}' vs solicitud '{sol_n}'."


def _comparar_fecha(doc: Optional[str], sol: Optional[str]) -> tuple[str, bool, str]:
    if not sol:
        return "NO_VERIFICADO", False, "No se envió la fecha de nacimiento de la solicitud."
    sol_f = _fecha(sol)
    if not sol_f:
        return "NO_VERIFICADO", False, f"La fecha de la solicitud '{sol}' no tiene un formato reconocible (usar AAAA-MM-DD)."
    if not doc:
        return "NO_VERIFICADO", False, "No se pudo leer la fecha de nacimiento en la credencial."
    if doc == sol_f:
        return "SI", True, "La fecha de nacimiento coincide con la captura."
    return "NO", False, f"La fecha de nacimiento no coincide: credencial {doc} vs solicitud {sol_f}."


def evaluar(categoria: str, frente: Optional[dict], mrz: Optional[dict], solicitud: Solicitud,
            regla_vigencia: str = "anio") -> dict:
    """Arma el bloque validacion_plan. categoria: ine | otro | hoja_en_blanco."""
    frente, mrz = frente or {}, mrz or {}
    es_ine = categoria == "ine"
    items: list[dict] = []

    blanco = categoria == "hoja_en_blanco"
    items.append(_item("hoja_en_blanco", "¿Es una hoja en blanco?", "SI" if blanco else "NO", not blanco,
                       "No se leyó texto en el documento." if blanco else "El documento tiene contenido."))

    ileg, det = _ilegible(categoria, frente, mrz)
    items.append(_item("imagen_ilegible", "¿La imagen es ilegible?", "SI" if ileg else "NO", not ileg, det))

    items.append(_item("es_ine", "¿Es una INE?", "SI" if es_ine else "NO", es_ine,
                       "Se identificó una credencial para votar del INE." if es_ine
                       else "El documento no es una credencial para votar del INE."))

    if es_ine:
        vig, det = vigencia(frente, mrz, regla_vigencia)
        res = "NO_VERIFICADO" if vig is None else ("SI" if vig else "NO")
        items.append(_item("vigente", "¿Está vigente?", res, bool(vig), det))
        r, ok, det = _comparar_nombre(_nombre_completo(frente), solicitud.nombre_completo())
        items.append(_item("nombre_coincide", "¿El nombre coincide?", r, ok, det))
        r, ok, det = _comparar_curp(frente.get("curp"), solicitud.curp, frente)
        items.append(_item("curp_coincide", "¿La CURP coincide?", r, ok, det))
        r, ok, det = _comparar_fecha(frente.get("fecha_nacimiento"), solicitud.fecha_nacimiento)
        items.append(_item("fecha_nacimiento_coincide", "¿La fecha de nacimiento coincide?", r, ok, det))
    else:
        for id_, preg in (("vigente", "¿Está vigente?"), ("nombre_coincide", "¿El nombre coincide?"),
                          ("curp_coincide", "¿La CURP coincide?"),
                          ("fecha_nacimiento_coincide", "¿La fecha de nacimiento coincide?")):
            items.append(_item(id_, preg, "NO_VERIFICADO", False, "No aplica: el documento no es una INE."))

    cumple = all(i["ok"] for i in items)
    # Motivos de raíz: si no es INE, las validaciones que dependen de ello no se repiten como motivo.
    motivos = [f"{i['pregunta']} {i['resultado']}: {i['detalle']}" for i in items
               if not i["ok"] and not i["detalle"].startswith("No aplica: el documento no es")]
    if es_ine and solicitud.vacia():
        motivos.append("No se enviaron datos de la solicitud: las validaciones de coincidencia no se pudieron realizar.")
    return {"cumple": cumple, "mensaje": MENSAJE_OK if cumple else MENSAJE_RECHAZO,
            "motivos_rechazo": motivos, "regla_vigencia": regla_vigencia, "validaciones": items}
