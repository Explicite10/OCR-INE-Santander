"""Procesa una carpeta de PDFs/imágenes de INE y genera JSON, CSV y un resumen.

Ejemplos:
    python -m scripts.procesar_lote ~/Downloads/ines --salida resultados
    python -m scripts.procesar_lote ~/Downloads/ines --salida resultados --limite 20 --recortes
    python -m scripts.procesar_lote --salida resultados --solo-resumen

Es reanudable: si se interrumpe, al volver a correrlo salta los archivos que ya
tienen JSON (usa --forzar para reprocesar).
"""

import argparse
import asyncio
import base64
import csv
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import pipeline  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.ocr import engine  # noqa: E402
from app.vlm import VlmClient  # noqa: E402

EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"}
CONF_FIELDS = ["nombre", "fecha_nacimiento", "sexo", "curp", "clave_elector", "domicilio", "seccion",
               "vigencia", "anio_registro", "cic", "identificador_ciudadano"]


def flatten(r: dict) -> dict:
    d = r.get("datos") or {}
    n, dom, vig = d.get("nombre") or {}, d.get("domicilio") or {}, d.get("vigencia") or {}
    conf = r.get("confianza") or {}
    checks = {c["regla"]: c["resultado"] for c in r.get("validaciones") or []}
    mrz_ok = all(checks.get(k) == "ok" for k in ("mrz_cic", "mrz_fecha_nacimiento", "mrz_vigencia", "mrz_compuesto"))
    row = {
        "archivo": r.get("archivo"),
        "estado": r.get("estado"),
        "campos_a_revisar": ";".join(r.get("campos_a_revisar") or []),
        "apellido_paterno": n.get("apellido_paterno"),
        "apellido_materno": n.get("apellido_materno"),
        "nombres": n.get("nombres"),
        "sexo": d.get("sexo"),
        "fecha_nacimiento": d.get("fecha_nacimiento"),
        "curp": d.get("curp"),
        "clave_elector": d.get("clave_elector"),
        "domicilio_calle": dom.get("calle"),
        "domicilio_colonia": dom.get("colonia"),
        "codigo_postal": dom.get("codigo_postal"),
        "municipio": dom.get("municipio"),
        "estado_domicilio": dom.get("estado"),
        "domicilio_completo": dom.get("completo"),
        "seccion": d.get("seccion"),
        "anio_registro": d.get("anio_registro"),
        "anio_registro_sufijo": d.get("anio_registro_sufijo"),
        "vigencia_desde": vig.get("desde"),
        "vigencia_hasta": vig.get("hasta"),
        "vigente": vig.get("vigente"),
        "estado_clave": d.get("estado_clave"),
        "municipio_clave": d.get("municipio_clave"),
        "localidad": d.get("localidad"),
        "emision": d.get("emision"),
        "cic": d.get("cic"),
        "identificador_ciudadano": d.get("identificador_ciudadano"),
        "numero_ocr": d.get("numero_ocr"),
        "modelo_credencial": d.get("modelo_credencial"),
        "mrz_valida": mrz_ok,
        "tiempo_ms": (r.get("meta") or {}).get("tiempo_total_ms"),
        "advertencias": " | ".join(r.get("advertencias") or []),
        "error": r.get("error"),
    }
    for f in CONF_FIELDS:
        row[f"conf_{f}"] = conf.get(f)
    return row


def diagnose(r: dict) -> dict:
    """Fila SIN datos personales (ni nombres, ni CURP, ni domicilio): sólo qué falló y cuánto tardó.
    Es lo que se puede compartir para analizar y mejorar el sistema."""
    meta = r.get("meta") or {}
    tiempos = meta.get("tiempos_ms") or {}
    conf = r.get("confianza") or {}
    row = {
        "archivo": r.get("archivo"),
        "estado": r.get("estado"),
        "campos_a_revisar": ";".join(r.get("campos_a_revisar") or []),
        "campos_reconstruidos": ";".join(r.get("campos_reconstruidos") or []),
        "validaciones_falla": ";".join(c["regla"] for c in r.get("validaciones") or [] if c["resultado"] == "falla"),
        "advertencias": ";".join(a.split("(")[0].strip() for a in r.get("advertencias") or []),
        "error_tipo": (r.get("error") or "").split(":")[0] or None,
        # Motivo completo del rechazo: texto fijo del sistema (p. ej. "parece pasaporte (ESP)"), sin datos
        "motivo_rechazo": (r.get("error") or None) if r.get("estado") == "rechazado" else None,
        "senales_ine": ";".join(meta.get("senales_ine") or []),
        "paginas": meta.get("paginas"),
        "rotado": meta.get("rotado"),
        "recorte_anverso": meta.get("recorte_anverso"),
        "recorte_reverso": meta.get("recorte_reverso"),
        "mrz_fuente": meta.get("mrz_fuente"),
        "tiempo_total_ms": meta.get("tiempo_total_ms"),
    }
    # Detalles no personales de algunas validaciones: similitudes (0-1) entre fuentes y, cuando el
    # nombre no cuadra con los códigos, qué parte difiere (paterno / materno / nombre).
    checks = {c["regla"]: c for c in r.get("validaciones") or []}
    for regla, col in (("nombre_vlm_vs_ocr", "sim_nombre_vlm_ocr"), ("nombre_vs_mrz", "sim_nombre_mrz"),
                       ("domicilio_vlm_vs_ocr", "sim_domicilio_vlm_ocr")):
        m = re.search(r"similitud ([\d.]+)", (checks.get(regla) or {}).get("detalle") or "")
        row[col] = m.group(1) if m else None
    difs = []
    for regla in ("clave_elector_vs_nombre", "curp_vs_nombre", "curp_consonantes_vs_nombre"):
        c = checks.get(regla)
        m = re.search(r"\(difiere: ([^)]*)\)", (c or {}).get("detalle") or "")
        if c and c["resultado"] == "falla" and m:
            difs.append(f"{regla.split('_vs_')[0]}: {m.group(1)}")
    row["nombre_vs_codigos_difiere"] = " | ".join(difs) or None
    row["clave_salta_maria_jose"] = "cuadra saltando" in ((checks.get("clave_elector_vs_nombre") or {}).get("detalle") or "")
    desempates = [c["detalle"] for k, c in checks.items() if k.endswith("_desempate")]
    row["desempates"] = " | ".join(desempates) or None
    for etapa, ms in tiempos.items():
        row[f"ms_{etapa}"] = ms
    for f in CONF_FIELDS:
        row[f"conf_{f}"] = conf.get(f)
    return row


def write_reports(out: Path) -> None:
    results = []
    for p in sorted((out / "json").glob("*.json")):
        try:
            results.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            print(f"JSON corrupto, se omite: {p}")
    if not results:
        print("No hay resultados todavía.")
        return
    rows = [flatten(r) for r in results]
    with open(out / "resultados.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    diag = [diagnose(r) for r in results]
    fields = list(dict.fromkeys(k for d in diag for k in d))
    with open(out / "diagnostico.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(diag)

    total = len(rows)
    states = Counter(r["estado"] for r in rows)
    pct = lambda n: f"{100 * n / total:.1f}%"  # noqa: E731
    times = [r["tiempo_ms"] / 1000 for r in rows if r["tiempo_ms"]]
    checks = Counter()
    for r in results:
        for c in r.get("validaciones") or []:
            if c["resultado"] == "falla":
                checks[c["regla"]] += 1
    review = Counter(f for r in rows for f in (r["campos_a_revisar"] or "").split(";") if f)
    warnings = Counter()
    for r in results:
        for a in r.get("advertencias") or []:
            warnings[a.split("(")[0].strip()] += 1

    lines = [
        "# Resumen del lote", "",
        f"- Documentos: **{total}**",
        f"- OK (sin revisión): **{states['ok']}** ({pct(states['ok'])})",
        f"- Revisar: **{states['revisar']}** ({pct(states['revisar'])})",
        f"- Error: **{states['error']}** ({pct(states['error'])})",
        f"- Rechazados (no parecen INE): **{states['rechazado']}** ({pct(states['rechazado'])})",
        f"- MRZ válida (4 dígitos verificadores): **{sum(r['mrz_valida'] for r in rows)}** "
        f"({pct(sum(r['mrz_valida'] for r in rows))})",
    ]
    if times:
        lines += [f"- Tiempo por documento: mediana {statistics.median(times):.1f} s, "
                  f"p90 {sorted(times)[int(0.9 * (len(times) - 1))]:.1f} s, total {sum(times) / 60:.1f} min"]
    stages: dict[str, list[float]] = {}
    for r in results:
        for etapa, ms in ((r.get("meta") or {}).get("tiempos_ms") or {}).items():
            stages.setdefault(etapa, []).append(ms / 1000)
    if stages:
        lines += ["- Mediana por etapa: " + ", ".join(f"{k} {statistics.median(v):.1f} s" for k, v in stages.items())]
    lines += ["", "## Confianza por campo", "", "| Campo | Alta | Media | Baja | Sin dato |", "|---|---|---|---|---|"]
    for f in CONF_FIELDS:
        c = Counter(r[f"conf_{f}"] for r in rows)
        lines.append(f"| {f} | {pct(c['alta'])} | {pct(c['media'])} | {pct(c['baja'])} | {pct(c[None])} |")
    lines += ["", "## Validaciones que más fallan", "", "| Regla | Documentos |", "|---|---|"]
    lines += [f"| {k} | {v} ({pct(v)}) |" for k, v in checks.most_common(15)]
    lines += ["", "## Campos enviados a revisión", "", "| Campo | Documentos |", "|---|---|"]
    lines += [f"| {k} | {v} ({pct(v)}) |" for k, v in review.most_common()]
    rechazos = Counter(r["error"].split(":")[0] for r in rows if r["estado"] == "rechazado" and r["error"])
    if rechazos:
        lines += ["", "## Motivos de rechazo", "", "| Motivo | Documentos |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in rechazos.most_common()]
    if warnings:
        lines += ["", "## Advertencias", "", "| Advertencia | Documentos |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in warnings.most_common(10)]
    (out / "resumen.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:8]))
    print(f"\nCSV: {out / 'resultados.csv'}\nResumen: {out / 'resumen.md'}")


async def run(files: list[Path], out: Path, concurrency: int, save_crops: bool) -> None:
    s = get_settings()
    vlm = VlmClient(s) if s.vlm_enabled else None
    if vlm and not await vlm.health():
        print(f"ERROR: el modelo {s.vlm_model} no responde en {s.vlm_base_url}")
        sys.exit(1)
    print("Cargando PaddleOCR...")
    await asyncio.to_thread(engine.warmup)

    sem = asyncio.Semaphore(concurrency)
    done = 0
    t0 = time.perf_counter()

    async def one(path: Path) -> None:
        nonlocal done
        async with sem:
            try:
                result = await pipeline.process([path.read_bytes()], vlm, path.name, include_crops=save_crops)
                data = result.model_dump(mode="json")
            except Exception as e:  # noqa: BLE001  (un archivo malo no debe tumbar el lote)
                data = {"archivo": path.name, "estado": "error", "error": f"{type(e).__name__}: {e}"}
            crops = data.pop("recortes", None) or {}
            for side, b64 in crops.items():
                (out / "recortes" / f"{path.stem}_{side}.png").write_bytes(base64.b64decode(b64))
            tmp = out / "json" / f"{path.stem}.json.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(out / "json" / f"{path.stem}.json")
            done += 1
            elapsed = time.perf_counter() - t0
            eta = elapsed / done * (len(files) - done)
            revisar = ",".join(data.get("campos_a_revisar") or [])
            print(f"[{done}/{len(files)}] {path.name}: {data['estado']}"
                  f"{' (' + revisar + ')' if revisar else ''}  ETA {eta / 60:.1f} min", flush=True)

    await asyncio.gather(*(one(f) for f in files))
    if vlm:
        await vlm.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entrada", nargs="?", type=Path, help="Carpeta con PDFs/imágenes")
    ap.add_argument("--salida", type=Path, default=Path("resultados"))
    ap.add_argument("--concurrencia", type=int, default=2,
                    help="Documentos en paralelo (2 en Mac; más en servidor con GPU)")
    ap.add_argument("--limite", type=int, default=None, help="Procesar sólo los primeros N")
    ap.add_argument("--forzar", action="store_true", help="Reprocesar aunque ya exista el JSON")
    ap.add_argument("--recortes", action="store_true", help="Guardar recortes de anverso/reverso")
    ap.add_argument("--solo-resumen", action="store_true", help="Sólo regenerar CSV y resumen")
    args = ap.parse_args()

    out: Path = args.salida
    (out / "json").mkdir(parents=True, exist_ok=True)
    (out / "recortes").mkdir(exist_ok=True)

    if not args.solo_resumen:
        if not args.entrada or not args.entrada.is_dir():
            ap.error("indica la carpeta de entrada")
        files = sorted(p for p in args.entrada.rglob("*") if p.suffix.lower() in EXTS)
        pending = [p for p in files if args.forzar or not (out / "json" / f"{p.stem}.json").exists()]
        if args.limite:
            pending = pending[: args.limite]
        print(f"{len(files)} archivos encontrados, {len(pending)} por procesar")
        if pending:
            asyncio.run(run(pending, out, args.concurrencia, args.recortes))
    write_reports(out)


if __name__ == "__main__":
    main()
