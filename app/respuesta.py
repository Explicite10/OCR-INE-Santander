"""Respuesta homologada de la API (formato "API Santander IA").

El pipeline produce un `ResultadoINE` interno (lo usan el lote, el diagnóstico y la base de
datos); aquí se traduce al sobre común:

    { codigo, estado, mensaje, frente, atras: {mrz}, validacion, avisos, clasificacion }

Reglas del formato:
* `codigo` dice qué pasó y no cambia de significado; `mensaje` es sólo para mostrar.
* Si un dato no se pudo leer, la llave no aparece (nunca "" ni un valor de relleno).
* 200 = todo cuadró · 210 = datos que se contradicen · 211 = algo pide ojo humano ·
  212 = quedaron comprobaciones sin correr · 22x = se evaluó pero no se extrajo.
"""

from datetime import date

from . import mrz as mrzmod
from .schemas import ResultadoINE

ESTADOS = {
    200: "PROCESADO",
    210: "PROCESADO_CON_DISCREPANCIAS",
    211: "PROCESADO_CON_AVISOS",
    212: "PROCESADO_INCOMPLETO",
    220: "NO_ES_INE",
    221: "HOJA_EN_BLANCO",
    222: "ARCHIVO_CORRUPTO",
    223: "PDF_PROTEGIDO",
    224: "LIMITE_PAGINAS",
    400: "ARCHIVO_NO_SOPORTADO",
    401: "NO_AUTORIZADO",
    404: "NO_ENCONTRADO",
    405: "METODO_NO_PERMITIDO",
    413: "ARCHIVO_DEMASIADO_GRANDE",
    422: "PETICION_INVALIDA",
    500: "ERROR_INTERNO",
    503: "SERVICIO_NO_DISPONIBLE",
}

MENSAJES = {
    200: "El documento se procesó y todas las comprobaciones cuadraron.",
    210: "El documento se procesó, pero hay datos que se contradicen entre sí: revisa validacion.discrepancias.",
    211: "El documento se procesó sin contradicciones, pero hay puntos que conviene revisar a mano: revisa avisos.",
    212: "El documento se procesó, pero quedaron comprobaciones sin correr: revisa validacion.sin_verificar.",
    220: "El documento no es una credencial para votar (INE). No se extrajo ningún dato.",
    221: "El documento viene en blanco o ilegible: no se leyó ningún texto.",
    222: "El archivo está dañado, incompleto o no es del tipo que dice. Envíalo de nuevo.",
    223: "El PDF está protegido con contraseña. Quítasela y vuelve a enviarlo.",
    224: "El documento excede el máximo de páginas por llamada: envía sólo las de la credencial.",
    400: "Tipo de archivo no soportado.",
    401: "API key inválida o ausente (header X-API-Key).",
    404: "No existe.",
    405: "Método no permitido.",
    413: "El archivo excede el tamaño máximo permitido.",
    422: "La petición no es válida.",
    500: "Error interno. Repórtalo.",
    503: "El servicio no está disponible.",
}


def http_status(codigo: int) -> int:
    """Los 2xx viajan con HTTP 200; en la familia de fallas el código ES el status HTTP."""
    return 200 if codigo < 300 else codigo


def sobre(codigo: int, mensaje: str | None = None, **cuerpo) -> dict:
    out = {"codigo": codigo, "estado": ESTADOS.get(codigo, "DESCONOCIDO"), "mensaje": mensaje or MENSAJES.get(codigo, "")}
    out.update({k: v for k, v in cuerpo.items() if v is not None})
    return out


def _clean(d: dict, keep_none: tuple[str, ...] = ()) -> dict:
    return {k: v for k, v in d.items() if (v not in (None, "", [], {})) or k in keep_none}


# ---------------------------------------------------------------- frente

NO_IMPRESOS_NUEVO = ["estado_clave", "municipio_clave", "localidad_clave", "anio_emision"]
_FORMATO = {"E/F": "anterior", "G/H": "nuevo"}


def frente(r: ResultadoINE) -> dict:
    d = r.datos
    n, dom = d.nombre, d.domicilio
    formato = _FORMATO.get(d.modelo_credencial or "")
    anio_registro = " ".join(x for x in (d.anio_registro, d.anio_registro_sufijo) if x) if d.anio_registro else None
    out = _clean({
        "apellido_paterno": n.apellido_paterno,
        "apellido_materno": n.apellido_materno,
        "nombre": n.nombres,
        "domicilio": _clean({
            "calle_numero": dom.calle,
            "colonia": dom.colonia,
            "codigo_postal": dom.codigo_postal,
            "municipio_delegacion": dom.municipio,
            # el estado tal como viene impreso en el domicilio (CDMX, JAL, MEX...)
            "estado": dom.estado_abreviatura or dom.estado,
        }),
        "clave_elector": d.clave_elector,
        "curp": d.curp,
        "fecha_nacimiento": d.fecha_nacimiento,
        "sexo": d.sexo,
        "anio_registro": anio_registro,
        "seccion": d.seccion,
        "vigencia": d.vigencia.hasta,
    })
    if not out:
        return {}
    out["pais"] = "MEXICO"
    out["formato_credencial"] = formato  # null = no se pudo determinar
    if formato == "anterior":
        out.update(_clean({
            "estado_clave": d.estado_clave,
            "municipio_clave": d.municipio_clave,
            "localidad_clave": d.localidad,
            "anio_emision": d.emision if d.emision and len(d.emision) == 4 else None,
        }))
    elif formato == "nuevo":
        if d.emision and len(d.emision) <= 2:
            out["numero_emision"] = d.emision.zfill(2)
        out["no_impresos"] = list(NO_IMPRESOS_NUEVO)
    return out


# ---------------------------------------------------------------- atras.mrz


def _iso(x: date | None) -> str | None:
    return x.isoformat() if x else None


def mrz(r: ResultadoINE) -> dict:
    lineas = r.datos.mrz
    if not lineas:
        return {"error": "No se localizó la MRZ del reverso"}
    m = mrzmod.parse(lineas)
    if m is None or m.n_validos == 0 or not m.cic:
        return {"error": "MRZ ilegible: ningún dígito de control cuadra", "texto_crudo": "\n".join(lineas)}
    checks = {c.nombre: c.valido for c in m.checks}
    l1, l3 = m.lineas[0], (m.lineas[2] if len(m.lineas) > 2 else "")
    out = _clean({
        "lineas": m.lineas,
        "tipo_documento": l1[0:2],
        "pais_emisor": l1[2:5],
        "cic": m.cic,
        "folio": m.cic,  # obsoleto: mismo valor que `cic`, se conserva por compatibilidad
        "seccion": m.seccion,
        "identificador_ciudadano": m.identificador_ciudadano,
        "nacionalidad": m.nacionalidad,
        "fecha_nacimiento": _iso(m.fecha_nacimiento),
        "sexo": m.sexo,
        "vigencia": _iso(m.fecha_vigencia),
        "apellido_paterno": m.apellido_paterno,
        "apellido_materno": m.apellido_materno,
        "nombres": m.nombres,
    })
    out["digitos_control"] = {
        "cic": checks.get("mrz_cic", False),
        "fecha_nacimiento": checks.get("mrz_fecha_nacimiento", False),
        "vigencia": checks.get("mrz_vigencia", False),
        "compuesto": checks.get("mrz_compuesto", False),
    }
    out["integra"] = m.valido
    origen = {"ocr": "texto", "vlm": "entidad"}.get((r.meta.mrz_fuente if r.meta else None) or "")
    if origen:
        out["origen"] = origen
    if len(l3) == 30 and not l3.endswith("<"):
        # el renglón 3 llegó al borde: sólo el último campo puede venir cortado
        out["campo_truncado"] = ("nombres" if "<<" in l3 else
                                 "apellido_materno" if m.apellido_materno else "apellido_paterno")
    return out


# ---------------------------------------------------------------- validacion

_MRZ_RULES = ("mrz_cic", "mrz_fecha_nacimiento", "mrz_vigencia", "mrz_compuesto")
_MRZ_LABEL = {"mrz_cic": "cic", "mrz_fecha_nacimiento": "fecha_nacimiento", "mrz_vigencia": "vigencia",
              "mrz_compuesto": "compuesto"}
_NAME_RULES = ("curp_vs_nombre", "curp_consonantes_vs_nombre", "clave_elector_vs_nombre")
LABELS = {
    "nombre_vs_mrz": "apellidos y nombre (MRZ)",
    "vigencia_vs_mrz": "vigencia (MRZ)",
    "seccion_vs_mrz": "seccion (MRZ)",
    "anio_registro_sufijo_vs_mrz": "anio_registro (MRZ)",
    "curp_vs_mrz": "fecha_nacimiento (CURP vs MRZ)",
    "fecha_nacimiento_consistente": "fecha_nacimiento (MRZ, CURP, clave_elector)",
    "sexo_consistente": "sexo (MRZ, CURP, clave_elector)",
    "curp_estructura_y_digito": "curp (estructura y dígito verificador)",
    "clave_elector_estructura": "clave_elector (estructura)",
    "clave_elector_vs_curp_iniciales": "clave_elector vs CURP (iniciales)",
    "curp_vs_clave_elector": "clave_elector vs CURP (fecha y sexo)",
    "curp_vs_nombre": "nombre vs CURP (iniciales)",
    "curp_consonantes_vs_nombre": "nombre vs CURP (consonantes)",
    "clave_elector_vs_nombre": "nombre vs clave_elector (iniciales)",
    "estado_clave_vs_domicilio": "estado_clave vs domicilio",
    "domicilio_codigo_postal": "domicilio (código postal)",
    "domicilio_estado_reconocido": "domicilio (estado)",
}
# Acuerdo entre los dos lectores (VLM y OCR) y correcciones internas: no son contrastes del
# documento. Un desacuerdo ya se resolvió en la fusión o bajó la confianza del campo (→ aviso).
_INTERNAL = ("_vlm_vs_ocr", "_desempate", "_reconstruida", "nombre_enie_corregida", "nombre_alineado_con_mrz")
_READERS = {"vlm", "ocr"}
# Datos que faltan, no que se contradicen
_GAPS = ("domicilio_codigo_postal", "domicilio_estado_reconocido")


def validacion(r: ResultadoINE) -> dict:
    checks = r.validaciones
    by_rule = {c.regla: c for c in checks}
    coincidencias: list[str] = []
    discrepancias: list[str] = []
    sin_verificar: list[str] = []

    mrz_checks = [c for c in checks if c.regla in _MRZ_RULES]
    mrz_integra = bool(mrz_checks) and all(c.resultado == "ok" for c in mrz_checks)
    if mrz_checks:
        if mrz_integra:
            coincidencias.append("MRZ (dígitos de control)")
        else:
            bad = [_MRZ_LABEL[c.regla] for c in mrz_checks if c.resultado != "ok"]
            discrepancias.append(f"MRZ (dígitos de control): no cuadran {', '.join(bad)}; "
                                 "no se usa como referencia")
    elif "mrz_legible" in by_rule:
        sin_verificar.append("MRZ: no se localizó o no se pudo leer el reverso")

    def result(regla: str) -> str | None:
        return by_rule[regla].resultado if regla in by_rule else None

    codes_agree = result("clave_elector_vs_curp_iniciales") == "ok"
    name_rules_run = [r for r in _NAME_RULES if result(r) in ("ok", "falla")]
    name_matches_codes = bool(name_rules_run) and all(result(r) == "ok" for r in name_rules_run)
    name_matches_mrz = result("nombre_vs_mrz") == "ok" and mrz_integra
    avisos_extra: list[str] = []
    for c in checks:
        regla = c.regla
        if regla in _MRZ_RULES or regla == "mrz_legible" or any(regla.endswith(s) or regla == s for s in _INTERNAL):
            continue
        label = LABELS.get(regla, regla)
        if regla.endswith("_vs_mrz") and mrz_checks and not mrz_integra:
            sin_verificar.append(f"{label}: la MRZ no es íntegra")
            continue
        if c.resultado == "falla" and regla == "fecha_nacimiento_consistente":
            # Si sólo difiere UNO de los lectores (VLM u OCR) y las fuentes del documento coinciden,
            # es un error de lectura que la fusión ya descartó, no una contradicción del documento.
            fuentes = {x.strip() for x in (c.detalle or "").removeprefix("difieren:").split(",") if x.strip()}
            if fuentes and fuentes <= _READERS and len(fuentes) == 1:
                coincidencias.append(label)
                continue
        if c.resultado == "falla" and regla in ("seccion_vs_mrz", "anio_registro_sufijo_vs_mrz") and mrz_integra:
            # La MRZ íntegra (dígito compuesto) respalda el dato y es el que se entrega: lo que difiere es
            # la lectura del anverso. Se avisa en vez de acusar al documento.
            campo = "seccion" if regla == "seccion_vs_mrz" else "anio_registro"
            avisos_extra.append(f"frente.{campo}: el anverso se leyó distinto que la MRZ ({c.detalle}); "
                                "se entrega el de la MRZ, respaldado por su dígito verificador")
            continue
        if c.resultado == "falla" and regla == "nombre_vs_mrz" and name_matches_codes:
            # El frente cuadra con la CURP y la clave (impresas); el que discrepa es el renglón 3 de
            # la MRZ, que no tiene dígito verificador: se avisa en vez de acusar al documento.
            avisos_extra.append("atras.mrz: el renglón de apellidos y nombre no coincide con el frente; el frente "
                                "sí cuadra con la CURP y la clave de elector (ese renglón no tiene dígito verificador)")
            continue
        if c.resultado == "falla" and regla in _NAME_RULES and name_matches_mrz and codes_agree:
            # El nombre coincide con la MRZ y los dos códigos concuerdan entre sí: lo que no cuadra es la
            # regla de iniciales (nombres compuestos, partículas, excepciones del RENAPO), no el documento.
            sin_verificar.append(f"{label}: el nombre coincide con la MRZ pero la regla de iniciales no "
                                 "reproduce el código")
            continue
        if c.resultado == "ok":
            coincidencias.append(label)
        elif c.resultado == "no_aplica":
            sin_verificar.append(f"{label}: falta el dato")
        elif regla in _GAPS:
            sin_verificar.append(f"{label}: no se encontró")
        else:
            detalle = f": {c.detalle}" if c.detalle else ""
            if regla in _NAME_RULES and codes_agree:
                detalle += " — la clave de elector y la CURP sí concuerdan entre sí: el dato sospechoso es el nombre"
            discrepancias.append(f"{label}{detalle}")

    if r.meta and "vlm_anverso" not in (r.meta.tiempos_ms or {}):
        sin_verificar.append("frente: el modelo de visión no leyó el anverso; datos sólo del OCR")
    return {
        "ok": not discrepancias,
        "completo": not sin_verificar,
        "coincidencias": list(dict.fromkeys(coincidencias)),
        "discrepancias": discrepancias,
        "sin_verificar": sin_verificar,
        "_avisos": avisos_extra,  # se mueven a `avisos` en construir()
    }


# ---------------------------------------------------------------- avisos

_FIELD_PATH = {"cic": "atras.mrz.cic", "identificador_ciudadano": "atras.mrz.identificador_ciudadano"}
# Advertencias internas que son sólo informativas (no piden ojo humano)
_INFO = ("Se corrigió una Ñ", "Se reacomodaron apellidos", "Localizador:", "El localizador no vio",
         "VLM deshabilitado")
_RECONSTRUIDA = ("CURP reconstruida", "Clave de elector reconstruida")


def avisos(r: ResultadoINE, revisar_reconstruidos: bool) -> list[str]:
    out = [f"{_FIELD_PATH.get(f, 'frente.' + f)}: confianza baja (las lecturas no coinciden o no se pudo "
           "leer); revisar contra el original" for f in r.campos_a_revisar]
    for w in r.advertencias:
        if w.startswith(_INFO):
            continue
        if w.startswith(_RECONSTRUIDA) and not revisar_reconstruidos:
            continue
        out.append(w)
    return out


# ---------------------------------------------------------------- sobre completo


def construir(r: ResultadoINE, revisar_reconstruidos: bool = True) -> dict:
    """ResultadoINE interno → respuesta homologada (dict listo para JSON)."""
    meta = r.meta
    if r.estado == "error":
        return sobre(r.error_codigo or 222, _mensaje_error(r))

    clasif = {
        "categoria": meta.categoria if meta else None,
        "confianza": None,  # el filtro es por reglas, no da una probabilidad
        "caracteres_leidos": meta.caracteres_leidos if meta else None,
        "senales": (meta.senales_ine if meta else None) or None,
    }
    if r.estado == "rechazado":
        blanco = meta is not None and meta.categoria == "hoja_en_blanco"
        clasif.update(motivo=r.error, documento_detectado=meta.documento_detectado if meta else None)
        return sobre(221 if blanco else 220, clasificacion=_clean(clasif, keep_none=("categoria", "confianza")))

    val = validacion(r)
    avs = avisos(r, revisar_reconstruidos) + val.pop("_avisos")
    if val["discrepancias"]:
        codigo = 210
    elif avs:
        codigo = 211
    elif val["sin_verificar"]:
        codigo = 212
    else:
        codigo = 200
    if codigo == 200 and r.estado == "revisar":  # red de seguridad: nunca "todo bien" si el pipeline dudó
        codigo = 211
        avs.append("El documento requiere revisión humana")

    return sobre(
        codigo,
        id=r.id,
        frente=frente(r) or None,
        atras={"mrz": mrz(r)},
        validacion=val,
        avisos=avs,
        clasificacion=_clean(clasif, keep_none=("categoria", "confianza")),
        # extensiones de esta implementación (un cliente que no las conozca las ignora)
        confianza=r.confianza or None,
        campos_reconstruidos=r.campos_reconstruidos or None,
        meta=_clean({"tiempo_total_ms": meta.tiempo_total_ms, "tiempos_ms": meta.tiempos_ms,
                     "modelo_vlm": meta.modelo_vlm, "paginas": meta.paginas,
                     "recorte_anverso": meta.recorte_anverso, "recorte_reverso": meta.recorte_reverso})
        if meta else None,
    )


def _mensaje_error(r: ResultadoINE) -> str:
    base = MENSAJES.get(r.error_codigo or 222, "")
    return f"{base} Detalle: {r.error}" if r.error else base
