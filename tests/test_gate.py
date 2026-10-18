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


def test_clave_de_elector_sin_ancla_pasa():
    lines = [L("NOMBRE"), L("DOMICILIO", 40), L("CLAVE DE ELECTOR", 80), L("CURP", 120)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert v.es_ine and v.puntaje == 6 and "CLAVE DE ELECTOR" in v.senales


def test_solo_etiquetas_genericas_no_bastan():
    lines = [L("NOMBRE"), L("DOMICILIO", 40), L("CURP", 80), L("VIGENCIA", 120)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert not v.es_ine and "clave de elector" in v.motivo


def test_documento_cualquiera_se_rechaza():
    lines = [L("FACTURA"), L("TOTAL $ 1,234.00", 40), L("GRACIAS POR SU COMPRA", 80)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert not v.es_ine and "No es una credencial INE" in v.motivo


def test_licencia_de_conducir_se_rechaza():
    lines = [L("ESTADOS UNIDOS MEXICANOS"), L("SECRETARIA DE MOVILIDAD", 30), L("LICENCIA PARA CONDUCIR", 60),
             L("CURP", 90), L("JUJA010922HDFRMLA9", 120), L("VIGENCIA PERMANENTE", 150), L("TIPO DE SANGRE O+", 180)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert not v.es_ine and "licencia" in v.motivo


def test_licencia_vieja_con_rfc_se_rechaza():
    lines = [L("ESTADOS UNIDOS MEXICANOS"), L("Licencia No.", 30), L("RFC", 60), L("LUAL810415", 90),
             L("Vigencia PERMANENTE", 120)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert not v.es_ine and "RFC" in v.motivo


def test_pasaporte_mexicano_se_rechaza():
    lines = [L("ESTADOS UNIDOS MEXICANOS"), L("CURP/ Personal No", 60), L("JUJA010922HDFRMLA9", 90),
             L("P<MEXJUAREZ<JAIMES<<ALEXIS<EDUARDO<<<<<<<<<<<<", 300),
             L("N042064760MEX0109224M3208081<<<<<<<<<<<<<<06", 330)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert not v.es_ine and "pasaporte" in v.motivo
    assert gate.other_document([L("PASAPORTE")], False) == "pasaporte"


def test_mrz_de_otro_pais_se_rechaza_aunque_haya_etiquetas():
    lines = [L("NOMBRE"), L("CLAVE DE ELECTOR", 40), L("IDUSA1234567890<<1234567890123", 200),
             L("8503121H3012317USA<<<<<<<<<<<4", 230)]
    v = gate.evaluate([lines], front_ancla=False, mrz_mex=False, min_senales=2)
    assert not v.es_ine and "USA" in v.motivo


def test_mrz_mexicana_mal_leida_no_es_extranjera():
    l1, l2, l3 = make_mrz()
    assert gate.other_document([L(l1.replace("IDMEX", "IDHEX")), L(l2), L(l3)], False) is None
    assert gate.other_document([L(l1), L(l2.replace("MEX", "M3X")), L(l3)], False) is None
    assert gate.other_document([L("P<ESPGARCIA<<MARIA<<<<<<<<<<<<<<<<<<<<<")], False) == "pasaporte (ESP)"


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
    assert "No es una credencial INE" in r.error
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


# ---------------------------------------------------------------- con VLM: el OCR no ve nada, el VLM sí


class FakeVlm:
    """VLM simulado: `locate` devuelve las cajas indicadas; `extract` devuelve `front` para el anverso."""

    def __init__(self, cards=(), front=None):
        self.cards, self.front = list(cards), front or {}
        self.locate_calls = 0

    async def locate(self, img):
        self.locate_calls += 1
        return self.cards, 0.1

    async def extract(self, img, prompt, schema, max_side=-1):
        if "mrz_linea1" in schema["properties"]:
            return {"mrz_linea1": "", "mrz_linea2": "", "mrz_linea3": ""}, 0.1
        return self.front, 0.1


def _page(w=2000, h=1400) -> bytes:
    ok, buf = cv2.imencode(".png", np.full((h, w, 3), 255, np.uint8))
    assert ok
    return buf.tobytes()


@pytest.fixture
def with_vlm(monkeypatch):
    from app import config

    monkeypatch.setenv("OCR_ENABLED", "true")
    monkeypatch.setenv("VLM_ENABLED", "true")
    config.get_settings.cache_clear()
    yield lambda read: monkeypatch.setattr(pipeline.engine, "read", read)
    config.get_settings.cache_clear()


def test_sin_senales_no_se_rechaza_antes_del_vlm(with_vlm):
    """OCR sin señales (copia tenue): el VLM lee la página completa y el documento NO se rechaza
    si él sí encontró una clave de elector válida."""
    with_vlm(lambda img: [L("ruido"), L("mas ruido", 40)])
    vlm = FakeVlm(front={"clave_elector": "PRLPJN85031209H400", "apellido_paterno": "PEREZ", "nombres": "JUAN"})
    r = asyncio.run(pipeline.process([_page()], vlm, "tenue.pdf"))
    assert r.estado == "revisar"  # sin MRZ queda a revisión, pero no se perdió
    assert r.datos.clave_elector == "PRLPJN85031209H400"
    assert vlm.locate_calls == 1 and "localizacion_vlm" in r.meta.tiempos_ms


def test_sin_senales_y_vlm_sin_nada_se_rechaza_al_final(with_vlm):
    with_vlm(lambda img: [L("RECIBO DE LUZ"), L("TOTAL A PAGAR", 40)])
    r = asyncio.run(pipeline.process([_page()], FakeVlm(), "recibo.pdf"))
    assert r.estado == "rechazado" and "tras" not in (r.error or "") and "sin encabezado" in r.error
    assert "vlm_anverso" in r.meta.tiempos_ms


def test_otro_documento_se_rechaza_sin_gastar_vlm(with_vlm):
    with_vlm(lambda img: [L("LICENCIA PARA CONDUCIR"), L("NOMBRE", 40)])
    vlm = FakeVlm()
    r = asyncio.run(pipeline.process([_page()], vlm, "licencia.pdf"))
    assert r.estado == "rechazado" and "licencia" in r.error
    assert vlm.locate_calls == 0 and "vlm_anverso" not in r.meta.tiempos_ms


def test_localizador_recorta_y_encuentra_la_mrz(with_vlm):
    """La página completa no tiene anclas (credencial chica); el VLM da la caja, el recorte se
    amplía y ahí el OCR sí encuentra la MRZ: el reverso queda como 'ancla'."""
    l1, l2, l3 = make_mrz()

    def read(img):
        if img.shape[1] >= 1500:  # página completa: nada legible
            return [L("x")]
        return [L(l1, 300), L(l2, 325), L(l3, 350)]  # recorte ampliado: MRZ

    with_vlm(read)
    vlm = FakeVlm(cards=[{"lado": "reverso", "bbox_2d": [200, 200, 900, 650], "rotacion": 0}])
    r = asyncio.run(pipeline.process([_page()], vlm, "chica.pdf"))
    assert r.estado != "rechazado"
    assert r.meta.recorte_reverso == "ancla" and r.datos.cic == "123456789"
    assert "mrz_mexicana" in r.meta.senales_ine
    assert any(w.startswith("Localizador:") for w in r.advertencias)
