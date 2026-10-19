"""Respuesta homologada: sobre codigo/estado/mensaje, frente, atras.mrz, validacion, avisos."""

import pymupdf
import pytest

from app import mrz, respuesta
from app.documents import DocumentError, LimitePaginas, PdfProtegido, load_pages
from app.merge import Merger, needs_review
from app.schemas import Meta, ResultadoINE
from tests.factories import CLAVE, make_mrz
from tests.test_merge import CURP, ocr_front, vlm_front


def resultado(vlm=None, ocr=None, m="default", estado="ok", advertencias=(), fuente="ocr") -> ResultadoINE:
    m = mrz.parse(make_mrz()) if m == "default" else m
    mg = Merger(vlm if vlm is not None else vlm_front(), ocr or ocr_front(), m)
    datos = mg.run()
    return ResultadoINE(estado=estado, datos=datos, confianza=mg.conf, campos_a_revisar=needs_review(mg.conf),
                        campos_reconstruidos=mg.reconstructed, validaciones=mg.checks,
                        advertencias=list(mg.warnings) + list(advertencias),
                        meta=Meta(tiempo_total_ms=9000, tiempos_ms={"vlm_anverso": 5000}, mrz_fuente=fuente,
                                  categoria="ine", caracteres_leidos=640, senales_ine=["encabezado"]))


def test_todo_cuadra_es_200():
    body = respuesta.construir(resultado())
    assert (body["codigo"], body["estado"]) == (200, "PROCESADO")
    f = body["frente"]
    assert f["nombre"] == "JUAN CARLOS" and f["curp"] == CURP and f["clave_elector"] == CLAVE
    assert f["fecha_nacimiento"] == "1985-03-12" and f["anio_registro"] == "2003 02" and f["vigencia"] == "2030"
    assert f["domicilio"] == {"calle_numero": "C REFORMA 10", "colonia": "COL CENTRO", "codigo_postal": "06000",
                              "municipio_delegacion": "CUAUHTEMOC", "estado": "CDMX"}
    assert f["pais"] == "MEXICO" and f["formato_credencial"] == "nuevo" and "estado_clave" in f["no_impresos"]
    m = body["atras"]["mrz"]
    assert m["cic"] == "123456789" and m["folio"] == m["cic"] and m["integra"] is True and m["origen"] == "texto"
    assert m["vigencia"] == "2030-12-31" and m["digitos_control"] == {
        "cic": True, "fecha_nacimiento": True, "vigencia": True, "compuesto": True}
    v = body["validacion"]
    assert v["ok"] and v["completo"] and not v["discrepancias"] and not v["sin_verificar"]
    assert "MRZ (dígitos de control)" in v["coincidencias"]
    assert "clave_elector vs CURP (iniciales)" in v["coincidencias"]
    assert body["avisos"] == [] and body["clasificacion"]["categoria"] == "ine"


def test_sin_cadenas_vacias_ni_nulos_en_frente():
    body = respuesta.construir(resultado(vlm=vlm_front(apellido_materno=""), ocr=ocr_front(nombre_lineas=[])))
    def walk(x):
        if isinstance(x, dict):
            for k, val in x.items():
                assert val != "" and (val is not None or k in ("formato_credencial", "confianza")), k
                walk(val)
        elif isinstance(x, list):
            for i in x:
                walk(i)
    walk(body["frente"])
    assert "apellido_materno" not in body["frente"]


def test_nombre_que_no_cuadra_con_los_codigos_es_discrepancia_210():
    r = resultado(vlm=vlm_front(apellido_materno="GOMEZ"), ocr=ocr_front(nombre_lineas=["PEREZ", "GOMEZ", "JUAN CARLOS"]))
    body = respuesta.construir(r)
    assert body["codigo"] == 210 and not body["validacion"]["ok"]
    d = next(x for x in body["validacion"]["discrepancias"] if x.startswith("nombre vs clave_elector"))
    assert "el dato sospechoso es el nombre" in d


def test_mrz_rota_se_reporta_una_vez_y_no_se_contrasta():
    l1, l2, l3 = make_mrz()
    broken = mrz.parse([l1, l2[:29] + str((int(l2[29]) + 1) % 10), l3])  # falla sólo el compuesto
    body = respuesta.construir(resultado(m=broken))
    v = body["validacion"]
    assert [d for d in v["discrepancias"] if d.startswith("MRZ")] == [
        "MRZ (dígitos de control): no cuadran compuesto; no se usa como referencia"]
    assert any(x.startswith("apellidos y nombre (MRZ)") for x in v["sin_verificar"])
    assert body["atras"]["mrz"]["integra"] is False


def test_sin_mrz_es_incompleto_o_peor():
    body = respuesta.construir(resultado(m=None))
    assert body["atras"]["mrz"] == {"error": "No se localizó la MRZ del reverso"}
    assert "MRZ: no se localizó o no se pudo leer el reverso" in body["validacion"]["sin_verificar"]
    assert body["codigo"] in (211, 212) and body["validacion"]["completo"] is False


def test_credencial_vencida_es_aviso_211():
    body = respuesta.construir(resultado(advertencias=["La credencial no está vigente (vigencia 2020)"]))
    assert body["codigo"] == 211 and body["avisos"] == ["La credencial no está vigente (vigencia 2020)"]


def test_reconstruidos_respetan_la_politica():
    r = resultado(advertencias=["CURP reconstruida (lectura dañada, posiblemente por un sello)"])
    assert respuesta.construir(r, revisar_reconstruidos=False)["codigo"] == 200
    assert respuesta.construir(r, revisar_reconstruidos=True)["codigo"] == 211


def test_revisar_interno_nunca_sale_como_200():
    assert respuesta.construir(resultado(estado="revisar"))["codigo"] == 211


def test_no_es_ine_y_hoja_en_blanco():
    r = ResultadoINE(estado="rechazado", error="No es una credencial INE: parece pasaporte",
                     meta=Meta(tiempo_total_ms=1, categoria="otro", caracteres_leidos=240,
                               documento_detectado="pasaporte"))
    body = respuesta.construir(r)
    assert (body["codigo"], body["estado"]) == (220, "NO_ES_INE") and "frente" not in body
    assert body["clasificacion"]["categoria"] == "otro" and body["clasificacion"]["documento_detectado"] == "pasaporte"
    r = ResultadoINE(estado="rechazado", error="...", meta=Meta(tiempo_total_ms=1, categoria="hoja_en_blanco"))
    body = respuesta.construir(r)
    assert (body["codigo"], body["estado"]) == (221, "HOJA_EN_BLANCO")
    c = body["clasificacion"]
    assert (c["categoria"], c["confianza"], c["caracteres_leidos"]) == ("hoja_en_blanco", None, 0)


def test_errores_de_archivo_llevan_su_codigo():
    for codigo, estado in ((222, "ARCHIVO_CORRUPTO"), (223, "PDF_PROTEGIDO"), (224, "LIMITE_PAGINAS")):
        body = respuesta.construir(ResultadoINE(estado="error", error="x", error_codigo=codigo))
        assert (body["codigo"], body["estado"]) == (codigo, estado)
        assert respuesta.http_status(codigo) == 200


def _pdf(pages=1, **save) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc.tobytes(**save)


def test_pdf_protegido_y_limite_de_paginas():
    protegido = _pdf(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="clave", owner_pw="dueno")
    with pytest.raises(PdfProtegido):
        load_pages(protegido, 72, 4, 15)
    with pytest.raises(LimitePaginas, match="16 páginas"):
        load_pages(_pdf(16), 72, 4, 15)
    assert len(load_pages(_pdf(3), 72, 4, 15)) == 3


def test_pdf_cortado_o_basura():
    completo = _pdf(2)
    with pytest.raises(DocumentError):
        load_pages(completo[: len(completo) // 2], 72, 4, 15)
    with pytest.raises(DocumentError) as e:
        load_pages(b"%PDF-1.4 basura sin estructura", 72, 4, 15)
    assert e.value.codigo == 222


def test_reporte_del_lote_calcula_el_codigo_de_la_api(tmp_path):
    import json

    from scripts import procesar_lote

    (tmp_path / "json").mkdir()
    ok = resultado().model_dump(mode="json")
    malo = resultado(vlm=vlm_front(apellido_materno="GOMEZ"),
                     ocr=ocr_front(nombre_lineas=["PEREZ", "GOMEZ", "JUAN CARLOS"])).model_dump(mode="json")
    for i, r in enumerate((ok, malo, {"archivo": "x.pdf", "estado": "error", "error": "PDF inválido"})):
        r["archivo"] = f"{i}.pdf"
        (tmp_path / "json" / f"{i}.json").write_text(json.dumps(r))
    procesar_lote.write_reports(tmp_path)
    resumen = (tmp_path / "resumen.md").read_text()
    assert "| 200 | PROCESADO | 1" in resumen and "| 210 | PROCESADO_CON_DISCREPANCIAS | 1" in resumen
    assert "| 222 | ARCHIVO_CORRUPTO | 1" in resumen
    assert "nombre vs clave_elector (iniciales)" in resumen
    assert "codigo_api" in (tmp_path / "diagnostico.csv").read_text(encoding="utf-8-sig").splitlines()[0]
