"""Mide la precisión real comparando contra valores capturados a mano.

1. Generar plantilla a partir de los resultados (ya trae lo que leyó el sistema;
   sólo hay que corregir lo que esté mal):
       python -m scripts.evaluar plantilla --resultados resultados/resultados.csv --salida verdad.csv --n 50

2. Corregir verdad.csv a mano (abrir en Excel; dejar la columna "verificado" en "si").

3. Evaluar:
       python -m scripts.evaluar comparar --verdad verdad.csv --resultados resultados/resultados.csv

El reporte muestra la exactitud por campo y, muy importante, la exactitud según
la confianza que asignó el sistema (si "alta" no es ~100 %, hay que ajustar reglas).
"""

import argparse
import csv
import random
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

FIELDS = [
    "apellido_paterno", "apellido_materno", "nombres", "sexo", "fecha_nacimiento", "curp", "clave_elector",
    "domicilio_completo", "codigo_postal", "seccion", "anio_registro", "vigencia_hasta", "cic",
    "identificador_ciudadano",
]
CONF_OF = {
    "apellido_paterno": "nombre", "apellido_materno": "nombre", "nombres": "nombre",
    "domicilio_completo": "domicilio", "codigo_postal": "domicilio", "vigencia_hasta": "vigencia",
}


def norm(v: str | None) -> str:
    if v is None:
        return ""
    v = unicodedata.normalize("NFD", str(v).upper())
    v = "".join(c for c in v if unicodedata.category(c) != "Mn")
    v = re.sub(r"[.,]", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def read_csv(p: Path) -> dict[str, dict]:
    with open(p, encoding="utf-8-sig") as f:
        return {r["archivo"]: r for r in csv.DictReader(f)}


def plantilla(args) -> None:
    rows = list(read_csv(args.resultados).values())
    random.seed(7)
    sample = random.sample(rows, min(args.n, len(rows)))
    with open(args.salida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["archivo", "verificado"] + FIELDS)
        w.writeheader()
        for r in sample:
            w.writerow({"archivo": r["archivo"], "verificado": "no", **{k: r.get(k, "") for k in FIELDS}})
    print(f"Plantilla con {len(sample)} documentos: {args.salida}")
    print('Corrige los valores y cambia "verificado" a "si" en cada fila revisada.')


def comparar(args) -> None:
    truth = {k: v for k, v in read_csv(args.verdad).items() if norm(v.get("verificado")) in ("SI", "SÍ", "S", "1")}
    res = read_csv(args.resultados)
    if not truth:
        print('No hay filas con verificado = "si".')
        return
    by_field = defaultdict(lambda: [0, 0])
    by_conf = defaultdict(lambda: [0, 0])
    errors = []
    docs_perfect = 0
    for name, t in truth.items():
        r = res.get(name)
        if r is None:
            continue
        perfect = True
        for f in FIELDS:
            if f not in t:
                continue
            ok = norm(t[f]) == norm(r.get(f))
            by_field[f][0] += ok
            by_field[f][1] += 1
            conf = r.get(f"conf_{CONF_OF.get(f, f)}") or "sin_dato"
            by_conf[conf][0] += ok
            by_conf[conf][1] += 1
            if not ok:
                perfect = False
                errors.append((name, f, t[f], r.get(f), conf, r.get("estado")))
        docs_perfect += perfect

    n = len(truth)
    print(f"\nDocumentos evaluados: {n}   100 % correctos: {docs_perfect} ({100 * docs_perfect / n:.1f}%)\n")
    print(f"{'Campo':<26}{'Exactitud':>10}{'  (n)':>8}")
    for f in FIELDS:
        ok, tot = by_field[f]
        if tot:
            print(f"{f:<26}{100 * ok / tot:>9.1f}%{tot:>8}")
    print(f"\n{'Confianza asignada':<26}{'Exactitud':>10}{'  (n)':>8}")
    for c in ("alta", "media", "baja", "sin_dato"):
        ok, tot = by_conf[c]
        if tot:
            print(f"{c:<26}{100 * ok / tot:>9.1f}%{tot:>8}")
    missed = [e for e in errors if e[5] == "ok"]
    print(f"\nErrores en documentos marcados 'ok' (se habrían colado): {len(missed)}")
    for e in missed[:20]:
        print(f"  {e[0]} · {e[1]}: esperado={e[2]!r} obtenido={e[3]!r} confianza={e[4]}")
    if args.detalle:
        print("\nTodos los errores:")
        for e in errors:
            print(f"  {e[0]} · {e[1]}: esperado={e[2]!r} obtenido={e[3]!r} confianza={e[4]} estado={e[5]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plantilla")
    p.add_argument("--resultados", type=Path, required=True)
    p.add_argument("--salida", type=Path, default=Path("verdad.csv"))
    p.add_argument("--n", type=int, default=50)
    c = sub.add_parser("comparar")
    c.add_argument("--verdad", type=Path, required=True)
    c.add_argument("--resultados", type=Path, required=True)
    c.add_argument("--detalle", action="store_true")
    args = ap.parse_args()
    {"plantilla": plantilla, "comparar": comparar}[args.cmd](args)


if __name__ == "__main__":
    main()
