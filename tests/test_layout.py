"""Localización de las caras: giros de la página y recortes del localizador (VLM)."""

import asyncio

import cv2
import numpy as np

from app import layout as ly
from app.ocr import Line
from app.vlm import VlmClient

from .factories import make_mrz


def L(text: str, y: int = 0, x: int = 10) -> Line:
    return Line(text, 0.9, x, y, x + 8 * len(text), y + 20)


def _mrz_lines():
    l1, l2, l3 = make_mrz()
    return [L(l1, 300), L(l2, 325), L(l3, 350)]


def test_prueba_todos_los_giros_aunque_el_texto_no_parezca_vertical():
    """Credencial girada 90° cuyo OCR sin girar sólo da basura 'horizontal': antes sólo se probaba 180°."""
    page = np.full((1400, 2000, 3), 255, np.uint8)
    seen = []

    def read(img):
        seen.append(img.shape[:2])
        if img.shape[:2] == (2000, 1400):  # girada 90°: MRZ legible
            return _mrz_lines()
        return [L("basura"), L("mas basura", 40)]

    lay = ly.analyze([page], read)
    assert lay.rotated and lay.back.method == "ancla"
    assert (2000, 1400) in seen


def test_sin_lineas_no_se_intenta_girar():
    calls = []
    page = np.full((400, 600, 3), 255, np.uint8)
    ly.analyze([page], lambda img: calls.append(1) or [])
    assert len(calls) == 1


def test_analyze_boxes_amplia_el_recorte_y_vuelve_a_buscar_anclas():
    page = np.full((1400, 2000, 3), 255, np.uint8)
    widths = []

    def read(img):
        widths.append(img.shape[1])
        return _mrz_lines() if img.shape[1] == ly.CROP_MIN_WIDTH else []

    lay = ly.analyze([page], read)
    assert lay.back.method == "pagina_completa"
    cards = [ly.CardBox(0, ly.Box(100, 100, 650, 450), "reverso", 0)]  # 550 px de ancho: se amplía
    lay = ly.analyze_boxes(lay, cards, read)
    assert lay.back.method == "ancla" and lay.back.mrz_lines[0].startswith("IDMEX")
    assert ly.CROP_MIN_WIDTH in widths
    assert lay.front.method == "pagina_completa"  # el anverso no apareció: sigue la página completa
    assert any(w.startswith("Localizador:") for w in lay.warnings)


def test_analyze_boxes_recorte_sin_anclas_se_usa_como_localizador():
    page = np.full((1400, 2000, 3), 255, np.uint8)
    lay = ly.analyze([page], lambda img: [])
    cards = [ly.CardBox(0, ly.Box(100, 100, 1300, 900), "anverso", 0),
             ly.CardBox(0, ly.Box(1, 1, 30, 20), "reverso", 0)]  # la segunda caja es absurda: se ignora
    lay = ly.analyze_boxes(lay, cards, lambda img: [])
    assert lay.front.method == "localizador" and lay.front.image.shape[1] == 1200
    assert lay.back.method == "pagina_completa"


def test_analyze_boxes_gira_el_recorte():
    page = np.full((2000, 1400, 3), 255, np.uint8)
    lay = ly.analyze([page], lambda img: [])
    cards = [ly.CardBox(0, ly.Box(50, 50, 1350, 1950), "anverso", 90)]
    lay = ly.analyze_boxes(lay, cards, lambda img: [])
    h, w = lay.front.image.shape[:2]
    assert w > h  # el recorte vertical quedó apaisado


def test_locate_escala_las_coordenadas_a_la_imagen_original(monkeypatch):
    from app.config import Settings

    client = VlmClient(Settings(vlm_locate_max_side=500))
    sent = {}

    async def fake_extract(img, prompt, schema, max_side=-1):
        sent["shape"] = img.shape[:2]
        assert "500x350" in prompt
        return {"credenciales": [{"lado": "anverso", "bbox_2d": [50, 50, 250, 175], "rotacion": 180},
                                 {"lado": "raro", "bbox_2d": [10, 10, 5, 5], "rotacion": 45}]}, 0.1

    monkeypatch.setattr(client, "extract", fake_extract)
    cards, _ = asyncio.run(client.locate(np.full((1400, 2000, 3), 255, np.uint8)))
    assert sent["shape"] == (350, 500)
    assert cards == [{"lado": "anverso", "bbox_2d": [200, 200, 1000, 700], "rotacion": 180}]
