"""Datos sintéticos (ficticios) con dígitos verificadores correctos."""

from app.mrz import check_digit
from app.validators import curp_check_digit


def make_mrz(cic="123456789", seccion="1234", ident="567890123", nac="850312", sexo="H",
             vig="301231", sufijo="02", nombre="PEREZ<LOPEZ<<JUAN<CARLOS") -> list[str]:
    l1 = f"IDMEX{cic}{check_digit(cic)}<<{seccion}{ident}"
    opt = f"<{sufijo}<<12345<"
    body2 = f"{nac}{check_digit(nac)}{sexo}{vig}{check_digit(vig)}MEX{opt}"
    comp = l1[5:30] + body2[0:7] + body2[8:15] + body2[18:29]
    l2 = body2 + str(check_digit(comp))
    l3 = (nombre + "<" * 30)[:30]
    assert len(l1) == 30 and len(l2) == 30
    return [l1, l2, l3]


def make_curp(base17="PELJ850312HDFRPN0") -> str:
    return base17 + curp_check_digit(base17)


CLAVE = "PRLPJN85031209H400"
