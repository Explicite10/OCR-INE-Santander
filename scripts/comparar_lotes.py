"""Compara dos corridas del lote (dos resultados.csv) campo por campo, SIN mostrar datos personales.

Sirve para saber si un cambio de configuración (p. ej. VLM_NUM_CTX o el tipo de caché de Ollama)
cambió lo que se extrae y si ahorró tiempo.

    python -m scripts.comparar_lotes lote/piloto_a/resultados.csv lote/piloto_b/resultados.csv
"""

import argparse
import csv
import statistics
from collections import Counter
from pathlib import Path

CAMPOS = ["apellido_paterno", "apellido_materno", "nombres", "sexo", "fecha_nacimiento", "curp", "clave_elector",
          "domicilio_completo", "codigo_postal", "seccion", "anio_registro", "vigencia_desde", "vigencia_hasta",
          "cic", "identificador_ciudadano", "numero_ocr"]


def load(p: Path) -> dict[str, dict]:
    with open(p, encoding="utf-8-sig") as f:
        return {r["archivo"]: r for r in csv.DictReader(f)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", type=Path, help="resultados.csv de la corrida A (referencia)")
    ap.add_argument("b", type=Path, help="resultados.csv de la corrida B (cambio)")
    args = ap.parse_args()
    a, b = load(args.a), load(args.b)
    comunes = sorted(set(a) & set(b))
    if not comunes:
        raise SystemExit("Las corridas no tienen archivos en común")

    print(f"Documentos comparados: {len(comunes)}\n")
    for nombre, run in (("A", a), ("B", b)):
        estados = Counter(run[k]["estado"] for k in comunes)
        tiempos = [int(run[k]["tiempo_ms"]) / 1000 for k in comunes if run[k]["tiempo_ms"]]
        med = f"{statistics.median(tiempos):.1f} s" if tiempos else "-"
        print(f"{nombre}: ok {estados['ok']} · revisar {estados['revisar']} · error {estados['error']} · "
              f"mediana por documento {med}")

    difs = Counter()
    por_doc = {}
    for k in comunes:
        cambiados = [c for c in CAMPOS if (a[k].get(c) or "") != (b[k].get(c) or "")]
        if a[k]["estado"] != b[k]["estado"]:
            cambiados.append(f"estado {a[k]['estado']}→{b[k]['estado']}")
        difs.update(c for c in cambiados if not c.startswith("estado"))
        if cambiados:
            por_doc[k] = cambiados

    print(f"\nDocumentos idénticos: {len(comunes) - len(por_doc)} de {len(comunes)}")
    if difs:
        print("\nCampos que cambiaron:")
        for c, n in difs.most_common():
            print(f"  {c}: {n}")
    if por_doc:
        print("\nPor documento (sólo nombres de campo; revisar a mano cuál de las dos lecturas es la correcta):")
        for k, cs in por_doc.items():
            print(f"  {k}: {', '.join(cs)}")


if __name__ == "__main__":
    main()
