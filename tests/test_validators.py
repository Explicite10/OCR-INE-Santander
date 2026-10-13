from datetime import date

from app import validators as v
from app.merge import parse_address
from tests.factories import CLAVE, make_curp


def test_curp_valid_and_check_digit():
    curp = make_curp()
    assert v.curp_valid(curp)
    assert not v.curp_valid(curp[:17] + str((int(curp[17]) + 1) % 10))
    assert v.curp_birthdate(curp) == date(1985, 3, 12)


def test_curp_normalization_fixes_typed_positions():
    curp = make_curp()
    noisy = curp[:4] + curp[4:10].replace("8", "B").replace("0", "O") + curp[10:]
    assert v.normalize_curp(noisy) == curp


def test_curp_century_letter():
    curp = make_curp("PELJ050312HDFRPNA")
    assert v.curp_birthdate(curp).year == 2005


def test_clave_elector():
    assert v.clave_valid(CLAVE)
    assert v.clave_birthdate(CLAVE) == date(1985, 3, 12)
    assert v.normalize_clave("PRLPJN85O3I209H4OO") == CLAVE
    assert not v.clave_valid("PRLPJN85133209H400")  # mes 13


def test_name_prefixes():
    assert v.curp_name_prefix("PEREZ", "LOPEZ", "JUAN CARLOS") == "PELJ"
    assert v.curp_name_prefix("PEREZ", "LOPEZ", "MARIA FERNANDA") == "PELF"  # se omite MARIA
    assert v.clave_name_prefix("PEREZ", "LOPEZ", "JUAN") == "PRLPJN"
    assert v.clave_name_prefix("SERRANO", "NUÑEZ", "ANA") == "SRNZAN"  # la Ñ se salta
    assert v.curp_inner_consonants("SERRANO", "NUÑEZ", "ANA") == "RXN"  # la Ñ vale X


def test_parse_date():
    assert v.parse_date("09/11/1956") == date(1956, 11, 9)
    assert v.parse_date("31/02/2000") is None
    assert v.parse_date("sin fecha") is None


def test_parse_address():
    d = parse_address(["C MOCTEZUMA OTE 405", "COL CENTRO 52400", "TENANCINGO, MEX."])
    assert d.calle == "C MOCTEZUMA OTE 405"
    assert d.colonia == "COL CENTRO"
    assert d.codigo_postal == "52400"
    assert d.municipio == "TENANCINGO"
    assert d.estado == "ESTADO DE MEXICO"


def test_parse_address_variants():
    d = parse_address(["C 4TA NORTE 12", "- ZONA CENTRO 22000", "TIJUANA, B.C."])
    assert d.colonia == "ZONA CENTRO" and d.estado == "BAJA CALIFORNIA"
    d = parse_address(["AV SIEMPRE VIVA 1", "COL X 77500", "CANCUN, Q. ROO"])
    assert d.estado == "QUINTANA ROO"
    d = parse_address(["CALLE 1", "COL 2"])
    assert d.estado is None and d.codigo_postal is None
