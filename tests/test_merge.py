from app import mrz
from app.front_ocr import FrontOcr
from app.merge import Merger, needs_review
from tests.factories import CLAVE, make_curp, make_mrz

CURP = make_curp()


def vlm_front(**over):
    base = {
        "apellido_paterno": "PEREZ", "apellido_materno": "LOPEZ", "nombres": "JUAN CARLOS", "sexo": "H",
        "domicilio_lineas": ["C REFORMA 10", "COL CENTRO 06000", "CUAUHTEMOC, CDMX"],
        "clave_elector": CLAVE, "curp": CURP, "fecha_nacimiento": "12/03/1985", "anio_registro": "2003 02",
        "seccion": "1234", "vigencia": "2020 - 2030", "estado": "", "municipio": "", "localidad": "",
        "emision": "", "campos_ilegibles": [],
    }
    base.update(over)
    return base


def ocr_front(**over):
    base = dict(nombre_lineas=["PEREZ", "LOPEZ", "JUAN CARLOS"],
                domicilio_lineas=["C REFORMA 10", "COL CENTRO 06000", "CUAUHTEMOC, CDMX"],
                curp=CURP, clave_elector=CLAVE, fecha_nacimiento="1985-03-12")
    base.update(over)
    return FrontOcr(**base)


def run(vlm, ocr, m=None):
    m = m if m is not None else mrz.parse(make_mrz())
    mg = Merger(vlm, ocr, m)
    mg.run()
    return mg


def test_all_sources_agree():
    mg = run(vlm_front(), ocr_front())
    d = mg.datos
    assert d.curp == CURP and d.clave_elector == CLAVE
    assert d.fecha_nacimiento == "1985-03-12"
    assert d.vigencia.desde == "2020" and d.vigencia.hasta == "2030"
    assert d.anio_registro == "2003" and d.anio_registro_sufijo == "02"
    assert d.cic == "123456789"
    assert d.modelo_credencial == "G/H"
    assert needs_review(mg.conf) == []
    assert all(mg.conf[f] == "alta" for f in ("nombre", "curp", "clave_elector", "fecha_nacimiento", "sexo", "cic"))


def test_vlm_typo_is_overridden_by_valid_ocr():
    mg = run(vlm_front(curp=CURP[:-1] + "X"), ocr_front())
    assert mg.datos.curp == CURP
    assert mg.conf["curp"] in ("media", "alta")


def test_conflicting_codes_go_to_review():
    other = make_curp("PELJ850313HDFRPN0")
    mg = run(vlm_front(curp=other), ocr_front(curp=other))
    assert mg.conf["curp"] == "baja"
    assert "curp" in needs_review(mg.conf)


def test_missing_mrz_flags_cic():
    mg = Merger(vlm_front(), ocr_front(), None)
    mg.run()
    assert mg.conf["cic"] == "baja"
    assert "cic" in needs_review(mg.conf)


def test_enie_correction():
    curp = make_curp("SENA900101MDFRXN0")
    clave = "SRNZAN90010109M100"
    vlm = vlm_front(apellido_paterno="SERRANO", apellido_materno="NUNEZ", nombres="ANA", sexo="M",
                    curp=curp, clave_elector=clave, fecha_nacimiento="01/01/1990")
    ocr = ocr_front(nombre_lineas=["SERRANO", "NUNEZ", "ANA"], curp=curp, clave_elector=clave,
                    fecha_nacimiento="1990-01-01")
    m = mrz.parse(make_mrz(nac="900101", sexo="M", nombre="SERRANO<NUNEZ<<ANA"))
    mg = run(vlm, ocr, m)
    assert mg.datos.nombre.apellido_materno == "NUÑEZ"


def test_vlm_failure_falls_back_to_ocr():
    mg = run(None, ocr_front())
    assert mg.datos.nombre.apellido_paterno == "PEREZ"
    assert mg.datos.curp == CURP


def test_curp_reconstructed_when_stamp_damages_it():
    damaged = CURP[:1] + CURP[2:10] + "HUP" + CURP[13:]  # falta una letra y estado ilegible
    mg = run(vlm_front(curp=damaged), ocr_front(curp=None))
    assert mg.datos.curp == CURP
    assert mg.conf["curp"] == "media"
    assert "curp" in mg.reconstructed


def test_clave_reconstructed_from_tail_and_validated_date():
    damaged = "PRLPJN" + "O1O312" + CLAVE[12:]  # fecha ilegible
    mg = run(vlm_front(clave_elector=damaged), ocr_front(clave_elector=None))
    assert mg.datos.clave_elector == CLAVE
    assert "clave_elector" in mg.reconstructed


def test_no_reconstruction_without_validated_identity():
    damaged = CURP[:1] + CURP[2:10] + "HUP" + CURP[13:]
    l1, l2, l3 = make_mrz()
    broken_mrz = mrz.parse([l1, l2[:3] + "77" + l2[5:], l3])
    mg = run(vlm_front(curp=damaged, fecha_nacimiento=""), ocr_front(curp=None, fecha_nacimiento=None,
                                                                    clave_elector=None), broken_mrz)
    assert "curp" not in mg.reconstructed
