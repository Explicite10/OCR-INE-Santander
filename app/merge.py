"""Combina VLM + OCR + MRZ, valida y asigna confianza por campo.

Criterio de confianza:
* alta  — al menos dos fuentes independientes coinciden, o un dígito verificador lo respalda.
* media — una sola fuente con estructura válida, sin contradicciones.
* baja  — fuentes en desacuerdo, estructura inválida o campo vacío.

El documento queda en "revisar" si algún campo crítico tiene confianza baja.
"""

import re
from collections import Counter
from itertools import permutations
from datetime import date

from . import validators as v
from .front_ocr import FrontOcr
from .mrz import MrzResult
from .schemas import Confianza, DatosINE, Domicilio, Nombre, Validacion, Vigencia

CRITICAL_FIELDS = ("nombre", "fecha_nacimiento", "sexo", "curp", "clave_elector", "cic")


class Merger:
    def __init__(self, vlm: dict | None, ocr: FrontOcr, mrz: MrzResult | None) -> None:
        self.vlm = vlm or {}
        self.ocr = ocr
        self.mrz = mrz
        self.datos = DatosINE()
        self.conf: dict[str, Confianza] = {}
        self.checks: list[Validacion] = []
        self.warnings: list[str] = []
        self.reconstructed: list[str] = []

    # ------------------------------------------------------------ utilidades

    def _check(self, regla: str, ok: bool | None, detalle: str | None = None) -> None:
        """Registra una validación; si la regla ya existía se reemplaza (p. ej. al volver a
        evaluar la fecha después de reconstruir un código)."""
        resultado = "no_aplica" if ok is None else ("ok" if ok else "falla")
        self.checks = [c for c in self.checks if c.regla != regla]
        self.checks.append(Validacion(regla=regla, resultado=resultado, detalle=detalle))

    def _vlm(self, key: str) -> str | None:
        val = self.vlm.get(key)
        if isinstance(val, str):
            val = val.strip()
        return val or None

    @property
    def mrz_ok(self) -> bool:
        return self.mrz is not None and self.mrz.valido

    def _mrz_check(self, nombre: str) -> bool:
        """True sólo si el dígito verificador cuadró sin reparaciones: una reparación puede
        corregir la posición equivocada, así que nunca se usa como fuente con autoridad."""
        return bool(self.mrz) and any(c.nombre == nombre and c.valido and not c.corregido
                                      for c in self.mrz.checks)

    # ------------------------------------------------------------ campos

    def run(self) -> DatosINE:
        self._mrz_fields()
        self._names()
        self._codes()
        self._birthdate()
        self._sex()
        self._fix_enie()
        self._reconstruct_clave()
        self._reconstruct_curp()
        if self.reconstructed:
            # La lectura dañada ya votó fecha/sexo y su estructura antes de reconstruirse: se vuelve
            # a evaluar con el código reconstruido para que las validaciones no queden desactualizadas.
            d = self.datos
            self._check("curp_estructura_y_digito", v.curp_valid(d.curp) if d.curp else None, d.curp)
            self._check("clave_elector_estructura", v.clave_valid(d.clave_elector) if d.clave_elector else None)
            self._birthdate()
            self._sex()
        self._address()
        self._section_and_dates()
        self._cross_checks()
        self.datos.modelo_credencial = self._model()
        return self.datos

    def _mrz_fields(self) -> None:
        m = self.mrz
        if not m:
            self.conf["cic"] = "baja"
            self.warnings.append("No se pudo leer la MRZ del reverso")
            self._check("mrz_legible", False)
            return
        self.datos.mrz = m.lineas
        for c in m.checks:
            self._check(c.nombre, c.valido, "corregido con dígito verificador" if c.corregido else None)
        self.datos.cic = m.cic
        self.datos.identificador_ciudadano = m.identificador_ciudadano
        self.datos.numero_ocr = m.numero_ocr
        cic_check = next((c for c in m.checks if c.nombre == "mrz_cic"), None)
        if m.cic and cic_check and cic_check.valido:
            self.conf["cic"] = "media" if cic_check.corregido else "alta"
        else:
            self.conf["cic"] = "baja"
        if m.numero_ocr:
            self.conf["identificador_ciudadano"] = "alta" if self._mrz_check("mrz_compuesto") else "media"

    def _names(self) -> None:
        pat, mat, nom = self._vlm("apellido_paterno"), self._vlm("apellido_materno"), self._vlm("nombres")
        source = "vlm"
        if not (pat and nom) and self.ocr.nombre_lineas:
            ol = self.ocr.nombre_lineas
            pat, mat, nom = ol[0], (ol[1] if len(ol) > 2 else None), " ".join(ol[2:] if len(ol) > 2 else ol[1:])
            source = "ocr"
        if not (pat or nom) and self.mrz and self.mrz.apellido_paterno:
            pat, mat, nom = self.mrz.apellido_paterno, self.mrz.apellido_materno, self.mrz.nombres
            source = "mrz"
            self.warnings.append("Nombre tomado sólo de la MRZ (puede venir truncado y sin Ñ/acentos)")

        def clean(s: str | None) -> str | None:
            return re.sub(r"\s+", " ", s.upper()).strip() if s else None

        pat, mat, nom = clean(pat), clean(mat), clean(nom)
        if source == "vlm" and (from_ocr := self._ocr_names_if_vlm_wrong(pat, mat, nom)):
            # El VLM revolvió las partes del nombre (p. ej. materno como paterno y el paterno dentro de
            # los nombres) y el OCR, que lee renglón por renglón, sí coincide con la MRZ íntegra.
            pat, mat, nom = from_ocr
            self._check("nombre_alineado_con_mrz", True, "apellidos y nombre tomados del OCR (coinciden con la MRZ)")
            self.warnings.append("Se reacomodaron apellidos y nombre según la MRZ")
        elif source != "mrz" and (aligned := self._align_with_mrz(pat, mat, nom)) and aligned != (pat, mat, nom):
            # Mismas palabras impresas, pero repartidas (u ordenadas) distinto que en la MRZ:
            # p. ej. el VLM puso el nombre de pila como apellido, o partió mal "DE LA CRUZ".
            pat, mat, nom = aligned
            self._check("nombre_alineado_con_mrz", True, "apellidos y nombre reacomodados según la MRZ")
            self.warnings.append("Se reacomodaron apellidos y nombre según la MRZ")
        full = " ".join(x for x in (nom, pat, mat) if x) or None
        self.datos.nombre = Nombre(apellido_paterno=pat, apellido_materno=mat, nombres=nom, nombre_completo=full)

        agree = 0
        printed = " ".join(x for x in (pat, mat, nom) if x)
        if self.ocr.nombre_lineas and source != "ocr":
            sim = v.similarity(printed, " ".join(self.ocr.nombre_lineas))
            self._check("nombre_vlm_vs_ocr", sim >= 0.92, f"similitud {sim:.2f}")
            agree += sim >= 0.92
        if self.mrz and self.mrz.lineas and len(self.mrz.lineas) > 2 and source != "mrz":
            sim = self._mrz_name_sim(pat, mat, nom)
            self._check("nombre_vs_mrz", sim >= 0.92, f"similitud {sim:.2f}")
            agree += sim >= 0.92
        if not printed:
            self.conf["nombre"] = "baja"
        elif agree >= 1 and source == "vlm":
            self.conf["nombre"] = "alta"
        else:
            self.conf["nombre"] = "media" if agree or source == "vlm" else "baja"

    def _mrz_name_sim(self, pat: str | None, mat: str | None, nom: str | None) -> float | None:
        """Similitud entre el nombre (en orden paterno, materno, nombres) y el renglón 3 de la MRZ."""
        if not (self.mrz and self.mrz.lineas and len(self.mrz.lineas) > 2):
            return None
        expected = v.norm_text(f"{pat or ''}<{mat or ''}<<{nom or ''}".replace(" ", "<")).replace(" ", "")
        got = self.mrz.lineas[2].rstrip("<").replace("<", "")
        return v.similarity(expected.replace("<", "")[: len(got)], got)

    def _ocr_names_if_vlm_wrong(self, pat: str | None, mat: str | None, nom: str | None) -> tuple | None:
        """Nombre del OCR (3+ renglones: paterno, materno, nombres) cuando el del VLM no cuadra con la
        MRZ íntegra y el del OCR sí. Se conserva la ortografía del VLM (Ñ, acentos) palabra por palabra."""
        ol = self.ocr.nombre_lineas
        if not (self.mrz and self.mrz.valido and len(ol) >= 3):
            return None
        o_pat, o_mat, o_nom = ol[0], ol[1], " ".join(ol[2:])
        vlm_sim = self._mrz_name_sim(pat, mat, nom) or 0.0
        ocr_sim = self._mrz_name_sim(o_pat, o_mat, o_nom) or 0.0
        if vlm_sim >= 0.92 or ocr_sim < 0.92:
            return None
        spelled = {v.norm_text(w).replace(" ", ""): w for w in " ".join(x for x in (pat, mat, nom) if x).split()}

        def respell(text: str) -> str:
            return " ".join(spelled.get(v.norm_text(w).replace(" ", ""), w) for w in text.upper().split())

        return respell(o_pat), respell(o_mat), respell(o_nom)

    def _align_with_mrz(self, pat: str | None, mat: str | None, nom: str | None) -> tuple | None:
        """Corrige el orden de los campos del nombre y la frontera apellidos / nombres con la MRZ.

        La MRZ sólo es confiable en el "<<" que separa apellidos de nombres: dentro de los apellidos
        usa el mismo "<" para las palabras de un apellido compuesto ("DE<LA<CRUZ") y para separar
        paterno de materno, así que esa división se respeta tal como la leyó el VLM. Sólo con MRZ
        íntegra y sólo con las MISMAS palabras impresas (se conservan Ñ y acentos).
        """
        m = self.mrz
        if not (m and m.valido and m.nombres and len(m.lineas) > 2 and "<<" in m.lineas[2]):
            return None

        def norm(words: list[str]) -> str:
            return v.norm_text(" ".join(words)).replace(" ", "")

        surnames = norm(m.lineas[2].split("<<", 1)[0].split("<"))
        given = norm([m.nombres])
        truncated = not m.lineas[2].endswith("<")  # el último campo puede venir cortado
        fields = [x for x in (pat, mat, nom) if x]
        for order in permutations(fields):  # el orden actual va primero
            first = order[0].split()
            words = " ".join(order).split()
            for j in range(len(first), len(words)):  # el primer campo completo es apellido paterno
                rest = norm(words[j:])
                if norm(words[:j]) == surnames and (rest == given or (truncated and rest.startswith(given))):
                    return " ".join(first), " ".join(words[len(first):j]) or None, " ".join(words[j:])
        return None

    def _pick_code(self, name: str, vlm_raw: str | None, ocr_val: str | None, normalize, valid) -> str | None:
        vlm_val = normalize(vlm_raw)
        cands = [c for c in (vlm_val, ocr_val) if c]
        valids = [c for c in cands if valid(c)]
        if vlm_val and ocr_val:
            self._check(f"{name}_vlm_vs_ocr", vlm_val == ocr_val,
                        None if vlm_val == ocr_val else f"vlm={vlm_val} ocr={ocr_val}")
        if vlm_val and ocr_val and vlm_val == ocr_val and valid(vlm_val):
            self.conf[name] = "alta"
            return vlm_val
        if len(set(valids)) == 1:
            self.conf[name] = "media"
            return valids[0]
        if len(set(valids)) > 1:
            # VLM y OCR leyeron dos códigos distintos y ambos con estructura válida (difieren en
            # un carácter). Se desempata con fuentes independientes: fecha y sexo de la MRZ (con
            # dígito verificador) y las letras que se derivan del nombre. Si exactamente uno
            # cuadra con todo, se toma con confianza media; si no, queda en baja (lo decide una persona).
            scored = {c: self._code_evidence(name, c) for c in set(valids)}
            clean = [c for c, (ok, _) in scored.items() if ok]
            if len(clean) == 1:
                vlm_or_ocr = "vlm" if clean[0] == vlm_val else "ocr"
                fails = [why for ok, whys in scored.values() if not ok for why in whys]
                self._check(f"{name}_desempate", True,
                            f"se tomó la lectura del {vlm_or_ocr}; la otra no cuadra con {', '.join(fails)}")
                self.conf[name] = "media"
                return clean[0]
            self.conf[name] = "baja"
            return self._consistent(valids) or valids[0]
        self.conf[name] = "baja"
        return cands[0] if cands else None

    def _code_evidence(self, name: str, code: str) -> tuple[bool, list[str]]:
        """¿El código cuadra con la MRZ validada y con el nombre? (ok, [lo que no cuadra])."""
        fails: list[str] = []
        n = self.datos.nombre
        if name == "curp":
            date_, sex = code[4:10], code[10]
            if n.apellido_paterno and n.nombres:
                exp = v.curp_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres)
                if exp and (exp[0] != code[0] or exp[2:] != code[2:4]):
                    fails.append("nombre")
                cons = v.curp_inner_consonants(n.apellido_paterno, n.apellido_materno, n.nombres)
                if cons and cons != code[13:16]:
                    fails.append("consonantes del nombre")
        else:
            date_, sex = code[6:12], code[14]
            if n.apellido_paterno and n.nombres:
                exps = {v.clave_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres, skip)
                        for skip in (False, True)} - {None}
                if exps and code[:6] not in exps:
                    fails.append("nombre")
            if self.datos.curp and v.curp_valid(self.datos.curp):
                if v.CURP_STATE_TO_CODE.get(self.datos.curp[11:13], code[12:14]) != code[12:14]:
                    fails.append("estado de la CURP")
        if self._mrz_check("mrz_fecha_nacimiento") and self.mrz.fecha_nacimiento:
            if self.mrz.fecha_nacimiento.strftime("%y%m%d") != date_:
                fails.append("fecha de la MRZ")
        if self.mrz and self.mrz.sexo and self._mrz_check("mrz_compuesto") and self.mrz.sexo != sex:
            fails.append("sexo de la MRZ")
        return not fails, fails

    def _consistent(self, options: list[str]) -> str | None:
        """Entre dos códigos válidos, elige el que coincide con la fecha/sexo de la MRZ."""
        if not self.mrz_ok or not self.mrz.fecha_nacimiento:
            return None
        target = self.mrz.fecha_nacimiento.strftime("%y%m%d")
        for o in options:
            if target in o and (self.mrz.sexo is None or self.mrz.sexo in o[10:15]):
                return o
        return None

    def _codes(self) -> None:
        d = self.datos
        d.curp = self._pick_code("curp", self._vlm("curp"), self.ocr.curp, v.normalize_curp, v.curp_valid)
        self._check("curp_estructura_y_digito", v.curp_valid(d.curp) if d.curp else None, d.curp)
        d.clave_elector = self._pick_code("clave_elector", self._vlm("clave_elector"), self.ocr.clave_elector,
                                          v.normalize_clave, v.clave_valid)
        self._check("clave_elector_estructura", v.clave_valid(d.clave_elector) if d.clave_elector else None)

    def _birthdate(self) -> None:
        votes: list[tuple[str, date]] = []
        if self.mrz and self.mrz.fecha_nacimiento:
            if self._mrz_check("mrz_fecha_nacimiento"):
                votes.append(("mrz", self.mrz.fecha_nacimiento))
            elif any(c.nombre == "mrz_fecha_nacimiento" and c.corregido for c in self.mrz.checks):
                votes.append(("mrz_corregida", self.mrz.fecha_nacimiento))
        if self.datos.curp and v.curp_valid(self.datos.curp):
            votes.append(("curp", v.curp_birthdate(self.datos.curp)))
        if self.datos.clave_elector and v.clave_valid(self.datos.clave_elector):
            votes.append(("clave", v.clave_birthdate(self.datos.clave_elector)))
        if (d := v.parse_date(self._vlm("fecha_nacimiento"))):
            votes.append(("vlm", d))
        if self.ocr.fecha_nacimiento:
            votes.append(("ocr", date.fromisoformat(self.ocr.fecha_nacimiento)))
        votes = [(s, d) for s, d in votes if d]
        if not votes:
            self.conf["fecha_nacimiento"] = "baja"
            return
        # La clave de elector sólo trae 2 dígitos de año: se le asigna el siglo de otra fuente
        others = [d for s, d in votes if s != "clave"]
        votes = [(s, next((o for o in others if o.strftime("%y%m%d") == d.strftime("%y%m%d")), d))
                 if s == "clave" else (s, d) for s, d in votes]
        counts = Counter(d for _, d in votes)
        mrz_date = next((d for s, d in votes if s == "mrz"), None)
        best, n = (mrz_date, counts[mrz_date]) if mrz_date else counts.most_common(1)[0]
        self.datos.fecha_nacimiento = best.isoformat()
        disagree = [s for s, d in votes if d != best]
        self._check("fecha_nacimiento_consistente", not disagree,
                    f"difieren: {', '.join(disagree)}" if disagree else f"fuentes: {len(votes)}")
        if mrz_date and n >= 2:
            # MRZ con dígito verificador + otra fuente: la que discrepa es la sospechosa
            self.conf["fecha_nacimiento"] = "alta"
        elif not disagree and (n >= 2 or mrz_date):
            self.conf["fecha_nacimiento"] = "alta"
        else:
            self.conf["fecha_nacimiento"] = "media" if n >= 2 else "baja"

    def _sex(self) -> None:
        votes = []
        if self.mrz and self.mrz.sexo and self._mrz_check("mrz_compuesto"):
            votes.append(self.mrz.sexo)
        if self.datos.curp and v.curp_valid(self.datos.curp):
            votes.append(self.datos.curp[10])
        if self.datos.clave_elector and v.clave_valid(self.datos.clave_elector):
            votes.append(self.datos.clave_elector[14])
        if (s := self._vlm("sexo")) in ("H", "M", "X"):
            votes.append(s)
        if not votes:
            self.conf["sexo"] = "baja"
            return
        best, n = Counter(votes).most_common(1)[0]
        self.datos.sexo = best
        self._check("sexo_consistente", n == len(votes), f"{n}/{len(votes)} fuentes")
        self.conf["sexo"] = "alta" if n >= 2 and n == len(votes) else ("media" if n >= 2 else "baja")

    def _address(self) -> None:
        lines = [re.sub(r"\s+", " ", l).strip().upper() for l in (self.vlm.get("domicilio_lineas") or []) if l]
        lines = [l for l in lines if l]
        source_ocr = False
        if not lines and self.ocr.domicilio_lineas:
            lines, source_ocr = [l.upper() for l in self.ocr.domicilio_lineas], True
        dom = parse_address(lines)
        self.datos.domicilio = dom
        if not lines:
            self.conf["domicilio"] = "baja"
            return
        sim = None
        if self.ocr.domicilio_lineas and not source_ocr:
            sim = v.similarity(" ".join(lines), " ".join(self.ocr.domicilio_lineas))
            self._check("domicilio_vlm_vs_ocr", sim >= 0.85, f"similitud {sim:.2f}")
        self._check("domicilio_codigo_postal", bool(dom.codigo_postal), dom.codigo_postal)
        self._check("domicilio_estado_reconocido", bool(dom.estado), dom.estado_abreviatura)
        structure_ok = bool(dom.codigo_postal and dom.estado)
        if sim is not None and sim >= 0.85 and structure_ok:
            self.conf["domicilio"] = "alta"
        elif (sim is None or sim >= 0.7) and (structure_ok or sim is not None):
            self.conf["domicilio"] = "media"
        else:
            self.conf["domicilio"] = "baja"

    def _section_and_dates(self) -> None:
        d = self.datos
        sec = re.sub(r"\D", "", self._vlm("seccion") or "") or None
        mrz_sec = self.mrz.seccion if self.mrz else None
        if sec and len(sec) < 4:
            sec = sec.zfill(4)
        if sec and mrz_sec:
            self._check("seccion_vs_mrz", sec == mrz_sec, None if sec == mrz_sec else f"anverso={sec} mrz={mrz_sec}")
        mrz_sec_ok = bool(mrz_sec) and self._mrz_check("mrz_compuesto")  # el compuesto cubre la sección
        d.seccion = mrz_sec if mrz_sec_ok else (sec or mrz_sec)
        if sec and sec == mrz_sec:
            self.conf["seccion"] = "alta"
        elif mrz_sec_ok:
            self.conf["seccion"] = "media"  # el anverso se leyó distinto: se queda la de la MRZ verificada
        else:
            self.conf["seccion"] = "baja" if sec and mrz_sec else "media"
        if not d.seccion:
            self.conf["seccion"] = "baja"

        # Año de registro "2008 02"
        reg = re.findall(r"\d+", self._vlm("anio_registro") or "")
        if reg:
            d.anio_registro = reg[0] if len(reg[0]) == 4 else None
            d.anio_registro_sufijo = reg[1] if len(reg) > 1 else None
            if len(reg[0]) == 6:  # "200802" sin espacio
                d.anio_registro, d.anio_registro_sufijo = reg[0][:4], reg[0][4:]
        if self.mrz and self.mrz.anio_registro_sufijo:
            if d.anio_registro_sufijo:
                self._check("anio_registro_sufijo_vs_mrz", d.anio_registro_sufijo == self.mrz.anio_registro_sufijo,
                            None if d.anio_registro_sufijo == self.mrz.anio_registro_sufijo else
                            f"anverso={d.anio_registro_sufijo} mrz={self.mrz.anio_registro_sufijo}")
                if self._mrz_check("mrz_compuesto"):
                    d.anio_registro_sufijo = self.mrz.anio_registro_sufijo
            else:
                d.anio_registro_sufijo = self.mrz.anio_registro_sufijo
        self.conf["anio_registro"] = "media" if d.anio_registro else "baja"

        # Vigencia "2019 - 2029" o "2028"
        years = re.findall(r"(?:19|20)\d{2}", self._vlm("vigencia") or "")
        desde = years[0] if len(years) >= 2 else None
        hasta = years[-1] if years else None
        mrz_hasta = str(self.mrz.fecha_vigencia.year) if self.mrz and self.mrz.fecha_vigencia else None
        if hasta and mrz_hasta:
            self._check("vigencia_vs_mrz", hasta == mrz_hasta, None if hasta == mrz_hasta else f"anverso={hasta} mrz={mrz_hasta}")
        hasta = hasta or mrz_hasta
        if mrz_hasta and self._mrz_check("mrz_vigencia"):
            hasta = mrz_hasta
        vig = Vigencia(desde=desde, hasta=hasta, vigente=(int(hasta) >= date.today().year) if hasta else None)
        d.vigencia = vig
        self.conf["vigencia"] = "alta" if hasta and hasta == mrz_hasta else ("media" if hasta else "baja")
        if vig.vigente is False:
            self.warnings.append(f"La credencial no está vigente (vigencia {hasta})")

        for key, attr in (("estado", "estado_clave"), ("municipio", "municipio_clave"),
                          ("localidad", "localidad"), ("emision", "emision")):
            val = re.sub(r"\D", "", self._vlm(key) or "") or None
            setattr(d, attr, val)

    def _name_codes_match(self, pat: str | None, mat: str | None, nom: str | None) -> tuple[bool | None, bool | None]:
        d = self.datos
        clave_ok = curp_ok = None
        if d.clave_elector and v.clave_valid(d.clave_elector):
            clave_ok = v.clave_name_prefix(pat, mat, nom) == d.clave_elector[:6]
        if d.curp and v.curp_valid(d.curp):
            curp_ok = v.curp_inner_consonants(pat, mat, nom) == d.curp[13:16]
        return clave_ok, curp_ok

    def _fix_enie(self) -> None:
        """La tilde de la Ñ casi desaparece en fotocopias. Si la clave de elector / CURP sólo
        cuadran cambiando una N por Ñ en el nombre, se corrige."""
        n = self.datos.nombre
        parts = [n.apellido_paterno, n.apellido_materno, n.nombres]
        if not parts[0] or not parts[2] or self.conf.get("nombre") == "baja":
            return
        clave_ok, curp_ok = self._name_codes_match(*parts)
        if clave_ok is not False and curp_ok is not False:
            return
        fixes = []
        for i, part in enumerate(parts):
            for j, ch in enumerate(part or ""):
                if ch != "N":
                    continue
                cand = list(parts)
                cand[i] = part[:j] + "Ñ" + part[j + 1:]
                c_ok, u_ok = self._name_codes_match(*cand)
                if c_ok is not False and u_ok is not False and (c_ok or u_ok):
                    fixes.append(cand)
        if len(fixes) == 1:
            pat, mat, nom = fixes[0]
            n.apellido_paterno, n.apellido_materno, n.nombres = pat, mat, nom
            n.nombre_completo = " ".join(x for x in (nom, pat, mat) if x)
            self._check("nombre_enie_corregida", True, "N -> Ñ según clave de elector / CURP")
            self.warnings.append("Se corrigió una Ñ en el nombre usando la clave de elector / CURP")

    # ------------------------------------------------- reconstrucción de códigos
    #
    # Si un sello tapa la CURP o la clave de elector, casi todo el código se puede derivar
    # de datos ya validados: nombre (iniciales), fecha y sexo (MRZ con dígito verificador)
    # y estado de nacimiento (viene en ambos códigos). Lo que no es derivable (homoclave)
    # se toma de la lectura dañada. El resultado se marca como reconstruido.

    def _reliable_identity(self) -> bool:
        return (self.conf.get("fecha_nacimiento") == "alta" and self.conf.get("sexo") == "alta"
                and self.conf.get("nombre") in ("alta", "media") and bool(self.datos.fecha_nacimiento))

    def _raw_reads(self, key: str, normalize) -> list[str]:
        reads = [normalize(self._vlm(key)), getattr(self.ocr, key, None)]
        return [r for r in reads if r]

    def _contradicts_identity(self, code: str | None, date_slice: slice, sex_pos: int, key: str) -> bool:
        """El código es válido pero no cuadra con fecha/sexo validados. Si VLM y OCR leyeron
        exactamente lo mismo, no se reconstruye: es un conflicto real y lo decide una persona."""
        d = self.datos
        if not code or len(code) != 18 or not d.fecha_nacimiento:
            return False
        normalize = v.normalize_curp if key == "curp" else v.normalize_clave
        reads = self._raw_reads(key, normalize)
        if len(reads) >= 2 and len(set(reads)) == 1:
            return False
        yymmdd = date.fromisoformat(d.fecha_nacimiento).strftime("%y%m%d")
        return code[date_slice] != yymmdd or code[sex_pos] != d.sexo

    def _reconstruct_clave(self) -> None:
        d = self.datos
        if not self._reliable_identity():
            return
        if self.conf.get("clave_elector") != "baja" and not self._contradicts_identity(d.clave_elector, slice(6, 12), 14,
                                                                                          "clave_elector"):
            return
        reads = self._raw_reads("clave_elector", v.normalize_clave)
        n = d.nombre
        rule = v.clave_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres)
        yymmdd = date.fromisoformat(d.fecha_nacimiento).strftime("%y%m%d")
        state = None
        if d.curp and v.curp_valid(d.curp):
            state = v.CURP_STATE_TO_CODE.get(d.curp[11:13])
        options = set()
        for r in reads:
            tail = r[-6:]
            m = re.fullmatch(r"(\d\d)([HMX])(\d{3})", v.normalize_clave("A" * 12 + tail)[12:] if len(tail) == 6 else "")
            if not m or m[2] != d.sexo:
                continue
            prefix = r[:6] if re.fullmatch(r"[A-Z]{6}", r[:6]) else rule
            if not prefix or (rule and sum(a == b for a, b in zip(prefix, rule)) < 4):
                continue  # la lectura no se parece al nombre: no se arriesga
            cand = f"{prefix}{yymmdd}{state or m[1]}{d.sexo}{m[3]}"
            if v.clave_valid(cand):
                options.add(cand)
        if len(options) == 1:
            d.clave_elector = options.pop()
            self.conf["clave_elector"] = "media"
            self.reconstructed.append("clave_elector")
            self._check("clave_elector_reconstruida", True, "nombre + fecha/sexo validados + lectura parcial")
            self.warnings.append("Clave de elector reconstruida (lectura dañada, posiblemente por un sello)")

    def _reconstruct_curp(self) -> None:
        d = self.datos
        if not self._reliable_identity():
            return
        if self.conf.get("curp") != "baja" and not self._contradicts_identity(d.curp, slice(4, 10), 10, "curp"):
            return
        reads = self._raw_reads("curp", v.normalize_curp)
        n = d.nombre
        prefix = v.curp_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres)
        cons = v.curp_inner_consonants(n.apellido_paterno, n.apellido_materno, n.nombres)
        if not prefix or not cons or not reads:
            return
        birth = date.fromisoformat(d.fecha_nacimiento)
        state = None
        if d.clave_elector and v.clave_valid(d.clave_elector):
            state = v.CODE_TO_CURP_STATE.get(d.clave_elector[12:14])
        if state is None:
            state = next((r[11:13] for r in reads if len(r) == 18 and r[11:13] in v.CURP_STATES), None)
        if state is None:
            return
        base = f"{prefix}{birth.strftime('%y%m%d')}{d.sexo}{state}{cons}"
        diffs = "0123456789" if birth.year < 2000 else "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        cands = {base + c + v.curp_check_digit(base + c) for c in diffs}
        options = set()
        for r in reads:
            if v.similarity(r[:16], base) < 0.75:
                continue  # la lectura no se parece a lo derivado (nombre con excepción, etc.)
            exact = [c for c in cands if c[16:] == r[-2:]]
            by_diff = [c for c in cands if c[16] == r[-2]]
            pick = exact or by_diff
            if len(pick) == 1:
                options.add(pick[0])
        if len(options) == 1:
            d.curp = options.pop()
            self.conf["curp"] = "media"
            self.reconstructed.append("curp")
            self._check("curp_reconstruida", True, "nombre + fecha/sexo validados + estado + lectura parcial")
            self.warnings.append("CURP reconstruida (lectura dañada, posiblemente por un sello)")

    def _cross_checks(self) -> None:
        d = self.datos
        n = d.nombre
        # En el detalle se indica QUÉ parte del nombre no cuadra (paterno / materno / nombre):
        # el diagnóstico del lote lo recoge sin exponer datos personales.
        if d.curp and v.curp_valid(d.curp) and n.apellido_paterno and n.nombres:
            exp = v.curp_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres)
            # Las primeras 4 letras pueden cambiar por palabras altisonantes: se comparan inicial y nombre
            ok = exp is not None and exp[0] == d.curp[0] and exp[2:] == d.curp[2:4]
            self._check("curp_vs_nombre", ok, None if ok else
                        f"esperado {exp}, curp {d.curp[:4]} ({_differs(exp, d.curp[:4], ('paterno', 'paterno', 'materno', 'nombre'))})")
            if not ok and self.conf.get("nombre") == "alta":
                self.conf["nombre"] = "media"
        if d.clave_elector and v.clave_valid(d.clave_elector) and n.apellido_paterno and n.nombres:
            exp = v.clave_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres)
            alt = v.clave_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres, skip_common_first=False)
            got = d.clave_elector[:6]
            ok = got in (exp, alt)
            detalle = None
            if ok and got != exp:
                detalle = "cuadra sin saltar MARIA/JOSE"
            elif not ok:
                detalle = f"esperado {exp}, clave {got} ({_differs(exp, got, ('paterno',) * 2 + ('materno',) * 2 + ('nombre',) * 2)})"
            self._check("clave_elector_vs_nombre", ok, detalle)
        if d.curp and v.curp_valid(d.curp) and n.apellido_paterno and n.nombres:
            exp = v.curp_inner_consonants(n.apellido_paterno, n.apellido_materno, n.nombres)
            ok = exp == d.curp[13:16]
            self._check("curp_consonantes_vs_nombre", ok, None if ok else
                        f"esperado {exp}, curp {d.curp[13:16]} ({_differs(exp, d.curp[13:16], ('paterno', 'materno', 'nombre'))})")
        if d.curp and d.clave_elector and v.curp_valid(d.curp) and v.clave_valid(d.clave_elector):
            # Las 6 letras de la clave salen de las mismas letras que codifica la CURP (iniciales en
            # 1, 3, 4 y consonantes internas en 14-16). Se comparan los dos códigos impresos entre sí,
            # sin pasar por el nombre leído: si cuadran y el nombre no, el sospechoso es el nombre.
            # Una X en la CURP sustituye a la Ñ o a una consonante que no existe: vale cualquier letra.
            exp = d.curp[0] + d.curp[13] + d.curp[2] + d.curp[14] + d.curp[3] + d.curp[15]
            got = d.clave_elector[:6]
            ok = all(e == g or e == "X" for e, g in zip(exp, got))
            if not ok and all(e == g or e == "X" for e, g in zip(exp[:4], got[:4])) and n.nombres:
                # La CURP siempre salta MARIA / JOSE como primer nombre; la clave de elector a veces no
                # (en el lote, 5 credenciales usan las letras de MARIA / JOSE): también es válido.
                sin_saltar = v.clave_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres,
                                                 skip_common_first=False)
                ok = bool(sin_saltar) and sin_saltar[4:] == got[4:] and sin_saltar[4:] != (
                    v.clave_name_prefix(n.apellido_paterno, n.apellido_materno, n.nombres) or "")[4:]
            self._check("clave_elector_vs_curp_iniciales", ok, None if ok else f"clave '{got}' vs CURP '{exp}'")
            ok = d.curp[4:10] == d.clave_elector[6:12] and d.curp[10] == d.clave_elector[14]
            self._check("curp_vs_clave_elector", ok, "fecha y sexo")
            if ok:  # se respaldan mutuamente (salvo si uno se reconstruyó a partir del otro)
                for f in ("curp", "clave_elector"):
                    if self.conf.get(f) == "media" and not self.reconstructed:
                        self.conf[f] = "alta"
            else:
                for f in ("curp", "clave_elector"):
                    self.conf[f] = "baja"
        if self._mrz_check("mrz_fecha_nacimiento") and d.curp and v.curp_valid(d.curp):
            ok = d.curp[4:10] == self.mrz.fecha_nacimiento.strftime("%y%m%d")
            self._check("curp_vs_mrz", ok)
            if not ok:
                self.conf["curp"] = "baja"
        if d.estado_clave and d.domicilio.estado:
            ok = v.STATE_CODES.get(d.estado_clave.zfill(2)) == d.domicilio.estado
            self._check("estado_clave_vs_domicilio", ok)

    def _model(self) -> str | None:
        if self.datos.estado_clave or self.datos.municipio_clave:
            return "E/F"
        if self.datos.vigencia.desde:
            return "G/H"
        return None


def _differs(expected: str | None, got: str, parts: tuple[str, ...]) -> str:
    """'difiere: materno, nombre' — qué partes del nombre no cuadran con el código."""
    if not expected:
        return "difiere: sin nombre"
    bad = list(dict.fromkeys(p for e, g, p in zip(expected, got, parts) if e != g))
    return "difiere: " + ", ".join(bad or ["?"])


# --------------------------------------------------------------- domicilio

_CP = re.compile(r"\b(\d{5})\b\s*$")
_CP_ANY = re.compile(r"(?<![\d-])(\d{5})(?![\d-])")  # "C.P. 50000 TENANCINGO": el CP en medio del renglón


def _state_from(text: str) -> tuple[str | None, str | None]:
    key = re.sub(r"[^A-Z ]", "", v.strip_accents(text.upper())).strip()
    compact = key.replace(" ", "")
    for cand in (key, compact):
        if cand in v.STATE_ABBR:
            return v.STATE_ABBR[cand], cand
    if compact in ("QROO",):
        return "QUINTANA ROO", "Q ROO"
    full = {s for s in v.STATE_ABBR.values()}
    if key in full:
        return key, None
    return None, None


def parse_address(lines: list[str]) -> Domicilio:
    dom = Domicilio(lineas=lines, completo=", ".join(lines) or None)
    if not lines:
        return dom
    last = lines[-1]
    if "," in last:
        muni, st = last.rsplit(",", 1)
        estado, abbr = _state_from(st)
        if estado:
            dom.municipio = muni.strip(" .") or None
            dom.estado, dom.estado_abreviatura = estado, abbr
    if dom.estado is None:
        # "TENANCINGO MEX." sin coma: se prueba con la última palabra
        parts = last.rsplit(" ", 1)
        if len(parts) == 2:
            estado, abbr = _state_from(parts[1])
            if estado:
                dom.municipio, dom.estado, dom.estado_abreviatura = parts[0].strip(" ,."), estado, abbr
    body = lines[:-1] if dom.estado else lines
    for pattern in (_CP, _CP_ANY):  # primero el CP al final del renglón (lo normal), luego en cualquier parte
        for i in range(len(body) - 1, -1, -1):
            m = pattern.search(body[i])
            if m:
                dom.codigo_postal = m.group(1)
                rest = (body[i][: m.start()] + " " + body[i][m.end():]).strip(" ,.-")
                dom.colonia = re.sub(r"\s+", " ", re.sub(r"\bC\.?\s?P\.?(?!\w)", "", rest)).strip(" ,.-") or None
                dom.calle = " ".join(body[:i]) or None
                return dom
    dom.calle = " ".join(body[:1]) or None
    dom.colonia = " ".join(body[1:]) or None
    return dom


def needs_review(conf: dict[str, Confianza]) -> list[str]:
    fields = [f for f, c in conf.items() if c == "baja"]
    order = {f: i for i, f in enumerate(CRITICAL_FIELDS)}
    return sorted(fields, key=lambda f: order.get(f, 99))
