"""Plan de atención (sección INE): las 7 validaciones y el bloque `validacion_plan`. Datos ficticios."""
from datetime import date

from app import plan, respuesta
from app.schemas import Meta, ResultadoINE
from tests.test_merge import CURP
from tests.test_respuesta import resultado

IDS = ["hoja_en_blanco", "imagen_ilegible", "es_ine", "vigente", "nombre_coincide", "curp_coincide",
       "fecha_nacimiento_coincide"]

FRENTE = {"apellido_paterno": "PEREZ", "apellido_materno": "LOPEZ", "nombre": "JUAN CARLOS", "sexo": "H",
          "curp": CURP, "clave_elector": "PRLPJN85031209H100", "fecha_nacimiento": "1985-03-12",
          "vigencia": str(date.today().year + 2)}
MRZ = {"cic": "123456789", "vigencia": f"{date.today().year + 2}-12-31",
       "digitos_control": {"cic": True, "fecha_nacimiento": True, "vigencia": True, "compuesto": True}}


def _por_id(bloque: dict) -> dict:
    return {i["id"]: i for i in bloque["validaciones"]}


def test_cumple_todo():
    sol = plan.Solicitud(nombre="Juan Carlos Pérez López", curp=CURP.lower(), fecha_nacimiento="12/03/1985")
    b = plan.evaluar("ine", FRENTE, MRZ, sol)
    assert [i["id"] for i in b["validaciones"]] == IDS
    assert b["cumple"] is True and b["motivos_rechazo"] == [] and b["mensaje"] == plan.MENSAJE_OK
    assert all(i["resultado"] == ("NO" if i["id"] in ("hoja_en_blanco", "imagen_ilegible") else "SI")
               for i in b["validaciones"])


def test_rechazo_con_motivos():
    sol = plan.Solicitud(apellido_paterno="Perez", nombres="Juan", fecha_nacimiento="1985-03-13")
    b = plan.evaluar("ine", FRENTE, MRZ, sol)
    r = _por_id(b)
    assert b["cumple"] is False and b["mensaje"] == plan.MENSAJE_RECHAZO
    assert r["nombre_coincide"]["resultado"] == "NO" and "CARLOS LOPEZ" in r["nombre_coincide"]["detalle"]
    assert r["curp_coincide"]["resultado"] == "NO_VERIFICADO" and not r["curp_coincide"]["ok"]
    assert r["fecha_nacimiento_coincide"]["resultado"] == "NO"
    assert len(b["motivos_rechazo"]) == 3


def test_curp_distinta_no_coincide():
    otra = CURP[:4] + "850313" + CURP[10:]
    r = _por_id(plan.evaluar("ine", FRENTE, MRZ, plan.Solicitud(curp=otra)))["curp_coincide"]
    assert r["resultado"] == "NO" and otra in r["detalle"]


def test_sin_solicitud_no_verificado():
    b = plan.evaluar("ine", FRENTE, MRZ, plan.Solicitud())
    r = _por_id(b)
    assert b["cumple"] is False
    assert r["nombre_coincide"]["resultado"] == "NO_VERIFICADO"
    assert r["fecha_nacimiento_coincide"]["resultado"] == "NO_VERIFICADO"
    assert r["curp_coincide"]["resultado"] == "NO_VERIFICADO"
    assert any("No se enviaron datos de la solicitud" in m for m in b["motivos_rechazo"])


def test_curp_tolerante_a_confusiones_ocr():
    leida = CURP[:4] + "85O312" + CURP[10:]  # O por 0 en la credencial
    b = plan.evaluar("ine", dict(FRENTE, curp=leida), MRZ, plan.Solicitud(curp=CURP))
    assert _por_id(b)["curp_coincide"]["resultado"] == "SI"


def test_curp_ilegible_en_la_credencial_no_se_da_por_buena():
    frente = {k: val for k, val in FRENTE.items() if k != "curp"}
    r = _por_id(plan.evaluar("ine", frente, MRZ, plan.Solicitud(curp=CURP)))["curp_coincide"]
    assert r["resultado"] == "NO_VERIFICADO" and not r["ok"]


def test_vigencia_por_anio_y_por_fecha():
    hoy = date(2026, 10, 27)
    assert plan.vigencia({"vigencia": "2026"}, {}, "anio", hoy)[0] is True
    assert plan.vigencia({"vigencia": "2026"}, {}, "fecha", hoy)[0] is True  # vence el 31 de diciembre
    assert plan.vigencia({"vigencia": "2025"}, {}, "anio", hoy)[0] is False
    assert plan.vigencia({}, {}, "anio", hoy)[0] is None


def test_vigencia_manda_la_mrz_verificada():
    hoy = date(2026, 10, 27)
    mrz = {"vigencia": "2025-12-31", "digitos_control": {"vigencia": True}}
    vig, det = plan.vigencia({"vigencia": "2028"}, mrz, "anio", hoy)
    assert vig is False and "MRZ" in det
    mrz_rota = {"vigencia": "2025-12-31", "digitos_control": {"vigencia": False}}
    vig, det = plan.vigencia({"vigencia": "2028"}, mrz_rota, "anio", hoy)
    assert vig is True and "anverso" in det


def test_no_es_ine_y_hoja_en_blanco():
    b = plan.evaluar("otro", None, None, plan.Solicitud(nombre="X"))
    r = _por_id(b)
    assert r["es_ine"]["resultado"] == "NO" and r["vigente"]["resultado"] == "NO_VERIFICADO"
    assert b["motivos_rechazo"] == ["¿Es una INE? NO: El documento no es una credencial para votar del INE."]
    b = plan.evaluar("hoja_en_blanco", None, None, plan.Solicitud())
    assert _por_id(b)["hoja_en_blanco"]["resultado"] == "SI" and len(b["motivos_rechazo"]) == 3


def test_ilegible_si_faltan_datos_esenciales():
    b = plan.evaluar("ine", {"vigencia": "2030"}, {"error": "MRZ ilegible"}, plan.Solicitud(nombre="X"))
    assert _por_id(b)["imagen_ilegible"]["resultado"] == "SI"


def test_respuesta_completa_con_validacion_plan():
    """construir + con_plan: el sobre homologado trae validacion_plan con las 7 preguntas en orden."""
    body = respuesta.con_plan(respuesta.construir(resultado()),
                              plan.Solicitud(nombre="PEREZ LOPEZ JUAN CARLOS", curp=CURP, fecha_nacimiento="1985-03-12"))
    assert body["codigo"] == 200
    vp = body["validacion_plan"]
    assert set(vp) == {"cumple", "mensaje", "motivos_rechazo", "regla_vigencia", "validaciones"}
    assert [i["id"] for i in vp["validaciones"]] == IDS
    assert vp["cumple"] is True and vp["regla_vigencia"] == "anio"


def test_rechazo_220_tambien_trae_validacion_plan():
    r = ResultadoINE(estado="rechazado", error="parece pasaporte",
                     meta=Meta(tiempo_total_ms=1, categoria="otro", documento_detectado="pasaporte"))
    body = respuesta.con_plan(respuesta.construir(r), plan.Solicitud())
    assert body["codigo"] == 220 and body["validacion_plan"]["cumple"] is False
    assert body["validacion_plan"]["motivos_rechazo"] == [
        "¿Es una INE? NO: El documento no es una credencial para votar del INE."]


def test_error_de_archivo_sin_validacion_plan():
    r = ResultadoINE(estado="error", error="PDF dañado", error_codigo=222, meta=Meta(tiempo_total_ms=1))
    body = respuesta.con_plan(respuesta.construir(r), plan.Solicitud())
    assert body["codigo"] == 222 and "validacion_plan" not in body
