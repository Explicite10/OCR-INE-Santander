from datetime import date

from app import mrz
from tests.factories import make_mrz


def test_parse_valid():
    r = mrz.parse(make_mrz())
    assert r.valido and not r.corregido
    assert r.cic == "123456789"
    assert r.seccion == "1234"
    assert r.identificador_ciudadano == "567890123"
    assert r.fecha_nacimiento == date(1985, 3, 12)
    assert r.fecha_vigencia == date(2030, 12, 31)
    assert r.sexo == "H"
    assert r.anio_registro_sufijo == "02"
    assert (r.apellido_paterno, r.apellido_materno, r.nombres) == ("PEREZ", "LOPEZ", "JUAN CARLOS")


def test_ocr_confusions_are_normalized():
    l1, l2, l3 = make_mrz()
    noisy = [
        "DMEX" + l1[5:].replace("1", "I", 1),  # falta la "I" inicial y un 1 leído como I
        l2.replace("0", "O", 1),
        l3.replace("O", "0"),
    ]
    r = mrz.parse(noisy)
    assert r.valido
    assert r.cic == "123456789"
    assert r.apellido_materno == "LOPEZ"


def test_ambiguous_cic_is_not_guessed():
    # Varias sustituciones cuadran con el dígito del CIC (y el compuesto usa los mismos pesos):
    # no se adivina, se marca inválido para que el pipeline pida una segunda lectura al VLM.
    l1, l2, l3 = make_mrz()
    broken = l1[:9] + "8" + l1[10:]  # 5 -> 8 en el CIC
    r = mrz.parse([broken, l2, l3])
    assert not r.valido


def test_repairs_never_produce_invalid_dates():
    l1, l2, l3 = make_mrz()
    for i in range(6):
        for d in "0123456789":
            if d == l2[i]:
                continue
            r = mrz.parse([l1, l2[:i] + d + l2[i + 1:], l3])
            fecha = next(c for c in r.checks if c.nombre == "mrz_fecha_nacimiento")
            if fecha.valido:
                assert r.fecha_nacimiento is not None
                assert fecha.corregido


def test_corrected_mrz_date_does_not_override_other_sources():
    from app.front_ocr import FrontOcr
    from app.merge import Merger
    from tests.factories import CLAVE, make_curp

    l1, l2, l3 = make_mrz()
    m = mrz.parse([l1, "88" + l2[2:], l3])  # 850312 -> 880312: la reparación puede equivocarse
    curp = make_curp()
    mg = Merger({"curp": curp, "clave_elector": CLAVE, "fecha_nacimiento": "12/03/1985"},
                FrontOcr(curp=curp, clave_elector=CLAVE), m)
    mg.run()
    assert mg.datos.fecha_nacimiento == "1985-03-12"


def test_invalid_is_reported():
    l1, l2, l3 = make_mrz()
    r = mrz.parse([l1, l2[:3] + "77" + l2[5:], l3])
    assert not r.valido


def test_birth_century():
    r = mrz.parse(make_mrz(nac="050101"))
    assert r.fecha_nacimiento.year == 2005


def test_find_lines():
    lines = make_mrz()
    texts = ["INE", "firma", *lines, "basura"]
    assert mrz.find_mrz_lines(texts) == lines


def test_best_prefers_valid():
    good = mrz.parse(make_mrz())
    l1, l2, l3 = make_mrz()
    bad = mrz.parse([l1, l2[:3] + "77" + l2[5:], l3])
    assert mrz.best(bad, good) is good
    assert mrz.best(None, None) is None


def test_campos_de_puro_relleno_no_validan():
    """El VLM, ante una hoja sin credencial, puede devolver renglones de relleno con dígitos 0:
    el dígito verificador de "<<<<<<" es 0 y antes 'cuadraba'."""
    r = mrz.parse(["IDMEX<<<<<<<<<0<<<<<<<<<<<<<<<<", "<<<<<<0<<<<<<<0MEX<<<<<<<<<<<0", "PEREZ<<JUAN"])
    assert r.n_validos == 0 and not r.valido and r.cic is None
    r = mrz.parse(["IDMEX0000000000<<<<<<<<<<<<<<<", "0000000<0000000MEX<<<<<<<<<<<0", ""])
    assert r.n_validos == 0
