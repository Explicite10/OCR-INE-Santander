"""API de punta a punta con base de datos SQLite y el pipeline simulado (sin OCR ni VLM)."""

import importlib

import pytest
from fastapi.testclient import TestClient

from app.schemas import DatosINE, Meta, Nombre, ResultadoINE


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("OCR_ENABLED", "false")
    monkeypatch.setenv("VLM_ENABLED", "false")
    monkeypatch.setenv("API_KEY", "secreto")
    from app import config

    config.get_settings.cache_clear()
    import app.main as main

    main = importlib.reload(main)

    async def fake_process(files, vlm, filename=None, include_crops=False):
        return ResultadoINE(
            archivo=filename, estado="revisar", campos_a_revisar=["domicilio"],
            datos=DatosINE(nombre=Nombre(nombre_completo="JUAN PEREZ LOPEZ"), curp="PELJ850312HDFRPN09"),
            meta=Meta(tiempo_total_ms=1234),
        )

    monkeypatch.setattr(main.pipeline, "process", fake_process)
    with TestClient(main.app) as c:
        yield c
    config.get_settings.cache_clear()


H = {"X-API-Key": "secreto"}
PDF = {"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}


def test_requires_api_key(client):
    r = client.post("/ine", files=PDF)
    assert r.status_code == 401
    assert r.json()["codigo"] == 401 and r.json()["estado"] == "NO_AUTORIZADO"


def test_solo_pdf(client):
    r = client.post("/ine", files={"file": ("a.png", b"\x89PNG\r\n\x1a\n....", "image/png")}, headers=H)
    assert r.status_code == 400 and r.json()["estado"] == "ARCHIVO_NO_SOPORTADO"


def test_dice_ser_pdf_pero_no_lo_es(client):
    r = client.post("/ine", files={"file": ("a.pdf", b"hola, no soy un pdf", "application/pdf")}, headers=H)
    assert r.status_code == 200 and r.json()["codigo"] == 222


def test_archivo_vacio(client):
    r = client.post("/ine", files={"file": ("a.pdf", b"", "application/pdf")}, headers=H)
    assert r.status_code == 200 and r.json()["codigo"] == 222


def test_falta_el_campo_file(client):
    r = client.post("/ine", files={"archivo": ("a.pdf", b"%PDF-1.4", "application/pdf")}, headers=H)
    assert r.status_code == 422
    body = r.json()
    assert body["codigo"] == 422 and body["estado"] == "PETICION_INVALIDA" and "file" in body["mensaje"]


def test_extract_saves_and_lists(client):
    r = client.post("/ine", files=PDF, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["codigo"] == 211 and body["estado"] == "PROCESADO_CON_AVISOS"
    assert any(a.startswith("frente.domicilio") for a in body["avisos"])
    id_ = body["id"]
    assert id_

    lst = client.get("/v1/ine/extracciones", headers=H).json()
    assert lst["total"] == 1
    assert lst["elementos"][0]["curp"] == "PELJ850312HDFRPN09"
    assert client.get("/v1/ine/extracciones?estado=ok", headers=H).json()["total"] == 0
    assert client.get("/v1/ine/extracciones?q=perez", headers=H).json()["total"] == 1

    det = client.get(f"/v1/ine/extracciones/{id_}", headers=H).json()
    assert det["resultado"]["campos_a_revisar"] == ["domicilio"]


def test_review(client):
    id_ = client.post("/ine", files=PDF, headers=H).json()["id"]
    r = client.put(f"/v1/ine/extracciones/{id_}/revision", headers=H,
                   json={"datos": {"domicilio": {"calle": "C REFORMA 10"}}, "revisado_por": "ana"})
    assert r.status_code == 200
    assert r.json()["estado"] == "revisado"
    assert r.json()["datos_corregidos"]["domicilio"]["calle"] == "C REFORMA 10"
    nf = client.get("/v1/ine/extracciones/no-existe", headers=H)
    assert nf.status_code == 404 and nf.json()["codigo"] == 404


def test_health_reports_db(client):
    assert client.get("/health").json()["base_de_datos"] is True


def test_sin_interfaz_web(client):
    r = client.get("/")
    assert r.status_code == 404 and r.json()["estado"] == "NO_ENCONTRADO"


def test_ruta_vieja_ya_no_existe(client):
    assert client.post("/v1/ine/extraer", files=PDF, headers=H).status_code == 404
