"""Filtro "¿es una INE?": señales del OCR antes del VLM y verificación final en el pipeline."""

import asyncio

import cv2
import numpy as np
import pytest

from app import gate, pipeline
from app.ocr import Line

from .factories import make_mrz


def L(text: str, y: int = 0) -> Line:
    return Line(text, 0.9, 10, y, 10 + 8 * len(text), y + 20)


def test_encabezado_basta():
    v = gate.evaluate([[L("INSTITUTO NACIONAL ELECTORAL")]], front_ancla=True, mrz_mex=False, min_senales=2)
    assert v.es_ine and "encabezado" in v.senales


def test_etiquetas_sin_ancla_pasan():
    lines = [L("NOMBRE"), L("DOMICILIO", 40), L("CURP", 80)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert v.es_ine and v.puntaje == 3


def test_documento_cualquiera_se_rechaza():
    lines = [L("FACTURA"), L("TOTAL $ 1,234.00", 40), L("GRACIAS POR SU COMPRA", 80)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert not v.es_ine and "No parece una credencial INE" in v.motivo


def test_mrz_de_otro_pais_se_rechaza_aunque_haya_etiquetas():
    lines = [L("NOMBRE"), L("DOMICILIO", 40), L("IDUSA1234567890<<1234567890123", 200),
             L("8503121H3012317USA<<<<<<<<<<<4", 230)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert not v.es_ine and "USA" in v.motivo


def test_mrz_mexicana_mal_leida_no_es_extranjera():
    l1, l2, l3 = make_mrz()
    assert gate.foreign_mrz([L(l1.replace("IDMEX", "IDHEX")), L(l2), L(l3)]) is None
    assert gate.foreign_mrz([L(l1), L(l2.replace("MEX", "M3X")), L(l3)]) is None
    assert gate.foreign_mrz([L("P<ESPGARCIA<<MARIA<<<<<<<<<<<<<<<<<<<<<")]) == "ESP"


def _png() -> bytes:
    ok, buf = cv2.imencode(".png", np.full((400, 600, 3), 255, np.uint8))
    assert ok
    return buf.tobytes()


@pytest.fixture
def fake_ocr(monkeypatch):
    from app import config

    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setenv("VLM_ENABLED", "false")
    config.get_settings.cache_clear()
    yield lambda lines: monkeypatch.setattr(pipeline.engine, "read", lambda img: lines)
    config.get_settings.cache_clear()


def test_pipeline_rechaza_antes_del_vlm(fake_ocr):
    fake_ocr([L("RECIBO DE LUZ"), L("TOTAL A PAGAR", 40)])
    r = asyncio.run(pipeline.process([_png()], None, "recibo.png"))
    assert r.estado == "rechazado"
    assert "No parece una credencial INE" in r.error
    assert r.datos.curp is None and "vlm_anverso" not in r.meta.tiempos_ms


def test_pipeline_con_filtro_apagado_sigue_igual(fake_ocr, monkeypatch):
    from app import config

    monkeypatch.setenv("RECHAZAR_NO_INE", "false")
    config.get_settings.cache_clear()
    fake_ocr([L("RECIBO DE LUZ"), L("TOTAL A PAGAR", 40)])
    r = asyncio.run(pipeline.process([_png()], None, "recibo.png"))
    assert r.estado == "revisar" and r.meta.senales_ine == []


def test_pipeline_ine_con_mrz_pasa(fake_ocr):
    l1, l2, l3 = make_mrz()
    fake_ocr([L(l1, 300), L(l2, 325), L(l3, 350)])
    r = asyncio.run(pipeline.process([_png()], None, "reverso.png"))
    assert r.estado != "rechazado"
    assert "mrz_mexicana" in r.meta.senales_ine
    assert r.datos.cic == "123456789"
