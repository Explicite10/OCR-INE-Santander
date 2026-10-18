"""Orquestación: archivo -> páginas -> OCR + recortes -> VLM -> MRZ -> fusión -> JSON."""

import asyncio
import base64
import logging
import re
import time

import numpy as np

from . import front_ocr, gate, mrz
from . import validators as v
from .config import get_settings
from .documents import DocumentError, encode_png, load_pages
from .layout import Box, CardBox, Layout, analyze, analyze_boxes
from .merge import CRITICAL_FIELDS, Merger, needs_review
from .ocr import engine
from .schemas import Meta, ResultadoINE
from .vlm import BACK_PROMPT, BACK_SCHEMA, FRONT_PROMPT, FRONT_SCHEMA, VlmClient, VlmError

log = logging.getLogger(__name__)

# Menos caracteres (letras y dígitos) que esto en todo el OCR = hoja en blanco
MIN_CARACTERES_OCR = 10


def _ms(t: float) -> int:
    return int(t * 1000)


def _meta(s, times: dict[str, int], t_start: float, layout: Layout, mrz_source: str | None,
          vlm: VlmClient | None, senales: list[str]) -> Meta:
    return Meta(
        tiempo_total_ms=_ms(time.perf_counter() - t_start),
        tiempos_ms=times,
        modelo_vlm=s.vlm_model if vlm is not None and s.vlm_enabled else None,
        paginas=layout.pages,
        rotado=layout.rotated,
        recorte_anverso=layout.front.method if layout.front else None,
        recorte_reverso=layout.back.method if layout.back else None,
        mrz_fuente=mrz_source,
        senales_ine=senales,
    )


async def process(files: list[bytes], vlm: VlmClient | None, filename: str | None = None,
                  include_crops: bool = False) -> ResultadoINE:
    s = get_settings()
    t_start = time.perf_counter()
    times: dict[str, int] = {}

    # 1. Páginas
    t = time.perf_counter()
    try:
        pages: list[np.ndarray] = []
        for data in files:
            pages += await asyncio.to_thread(load_pages, data, s.render_dpi, s.max_pages)
    except DocumentError as e:
        return ResultadoINE(archivo=filename, estado="error", error=str(e),
                            meta=Meta(tiempo_total_ms=_ms(time.perf_counter() - t_start)))
    times["render"] = _ms(time.perf_counter() - t)

    # 2. OCR + localización de anverso / reverso
    t = time.perf_counter()
    ocr_read = engine.read if s.ocr_enabled else (lambda img: [])
    layout: Layout = await asyncio.to_thread(analyze, pages, ocr_read)
    times["ocr_y_recorte"] = _ms(time.perf_counter() - t)

    def gate_eval() -> gate.Veredicto:
        front_ancla = layout.front is not None and layout.front.method == "ancla"
        mrz_mex = layout.back is not None and layout.back.method == "ancla"
        return gate.evaluate(layout.page_lines, front_ancla, mrz_mex, s.ine_min_senales)

    # 2b. Texto de OTRO documento (licencia, pasaporte...): se rechaza sin gastar el VLM.
    #     Sin VLM tampoco hay quien pueda ver lo que el OCR no vio: se rechaza desde aquí.
    veredicto = gate_eval()
    vlm_on = vlm is not None and s.vlm_enabled
    # Hoja en blanco (o sin nada legible): el OCR no encontró texto en ninguna página. Una INE,
    # aun muy tenue o de lado, deja decenas de caracteres; no tiene caso gastar el VLM.
    texto = sum(len(re.sub(r"[^A-Z0-9]", "", l.text.upper())) for lines in layout.page_lines for l in lines)
    if s.rechazar_no_ine and s.ocr_enabled and texto < MIN_CARACTERES_OCR:
        motivo = "No es una credencial INE: el documento está en blanco o no tiene texto legible"
        log.info("Documento rechazado: %s", motivo)
        return ResultadoINE(archivo=filename, estado="rechazado", error=motivo,
                            advertencias=list(layout.warnings),
                            meta=_meta(s, times, t_start, layout, None, vlm, veredicto.senales))
    if s.rechazar_no_ine and s.ocr_enabled and (veredicto.otro_documento or (not vlm_on and not veredicto.es_ine)):
        log.info("Documento rechazado: %s", veredicto.motivo)
        return ResultadoINE(archivo=filename, estado="rechazado", error=veredicto.motivo,
                            advertencias=list(layout.warnings),
                            meta=_meta(s, times, t_start, layout, None, vlm, veredicto.senales))

    # 2c. Sin anclas en ninguna página: el VLM ubica las credenciales y se repite el OCR en los recortes.
    if vlm_on and s.vlm_localizador and s.ocr_enabled and not layout.anchored:
        t = time.perf_counter()
        try:
            cards: list[CardBox] = []
            for i, page in enumerate(layout.page_images):
                found, _ = await vlm.locate(page)
                cards += [CardBox(i, Box(*c["bbox_2d"]), c["lado"], c["rotacion"]) for c in found]
                if any(c.lado == "anverso" for c in cards) and any(c.lado == "reverso" for c in cards):
                    break
            if cards:
                layout = await asyncio.to_thread(analyze_boxes, layout, cards, ocr_read)
                veredicto = gate_eval()
            else:
                layout.warnings.append("El localizador no vio ninguna credencial en la página")
        except VlmError as e:
            log.error("Localizador falló: %s", e)
            layout.warnings.append("El localizador de credenciales falló; se analizó la página completa")
        times["localizacion_vlm"] = _ms(time.perf_counter() - t)
    warnings = list(layout.warnings)
    front_ancla = layout.front is not None and layout.front.method == "ancla"

    ocr_front = front_ocr.extract(layout.front.lines if layout.front and layout.front.method == "ancla" else [])
    ocr_mrz = mrz.parse(layout.back.mrz_lines) if layout.back and layout.back.mrz_lines else None

    # 3. VLM (anverso siempre; reverso sólo si la MRZ del OCR no cuadra al 100 %)
    vlm_front: dict | None = None
    vlm_mrz: mrz.MrzResult | None = None
    need_back_vlm = not (ocr_mrz and ocr_mrz.valido and not ocr_mrz.corregido and len(ocr_mrz.lineas) == 3)
    if vlm is not None and s.vlm_enabled:
        jobs = {}
        if layout.front is not None:
            jobs["vlm_anverso"] = vlm.extract(layout.front.image, FRONT_PROMPT, FRONT_SCHEMA)
        if need_back_vlm and layout.back is not None:
            jobs["vlm_reverso"] = vlm.extract(layout.back.image, BACK_PROMPT, BACK_SCHEMA)
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)
        for name, res in zip(jobs, results):
            if isinstance(res, Exception):
                log.error("%s falló: %s", name, res)
                warnings.append(f"El modelo de visión falló en {name.split('_')[1]}; se usó sólo OCR")
                continue
            data, secs = res
            times[name] = _ms(secs)
            if name == "vlm_anverso":
                vlm_front = data
            else:
                vlm_mrz = mrz.parse([data.get("mrz_linea1", ""), data.get("mrz_linea2", ""),
                                     data.get("mrz_linea3", "")])
    elif vlm is None or not s.vlm_enabled:
        warnings.append("VLM deshabilitado: resultados sólo con OCR")

    chosen = mrz.best(ocr_mrz, vlm_mrz)
    mrz_source = None
    if chosen is not None:
        mrz_source = "ocr" if chosen is ocr_mrz else "vlm"
        other = vlm_mrz if chosen is ocr_mrz else ocr_mrz
        if other is not None and other.valido and chosen.valido and other.cic != chosen.cic:
            warnings.append("OCR y VLM leyeron MRZ válidas pero distintas")

    # 4. Fusión y validación
    merger = Merger(vlm_front, ocr_front, chosen)
    datos = merger.run()
    warnings += merger.warnings
    review = needs_review(merger.conf)
    critical_bad = [f for f in review if f in CRITICAL_FIELDS]
    estado = "revisar" if critical_bad or not (chosen and chosen.valido) else "ok"
    if merger.reconstructed and s.revisar_reconstruidos:
        estado = "revisar"
    if vlm_front is None and vlm is not None and s.vlm_enabled:
        estado = "revisar"

    meta = _meta(s, times, t_start, layout, mrz_source, vlm, veredicto.senales)

    # 5. Verificación final: el VLM leyó la imagen y aun así no hay NADA exclusivo de la INE
    #    (el CURP no cuenta: también lo traen pasaportes y licencias).
    #    Sólo aplica si el VLM respondió; si falló o está apagado no hay con qué juzgar y queda "revisar".
    if (s.rechazar_no_ine and vlm_front is not None and not front_ancla
            and not (chosen is not None and chosen.n_validos > 0)
            and not v.clave_valid(datos.clave_elector)):
        motivo = ("No se reconoció una credencial INE: sin encabezado, sin MRZ válida y sin "
                  "clave de elector con estructura válida")
        log.info("Documento rechazado tras el VLM: %s", motivo)
        return ResultadoINE(archivo=filename, estado="rechazado", error=motivo, advertencias=warnings, meta=meta)

    result = ResultadoINE(
        archivo=filename, estado=estado, datos=datos, confianza=merger.conf, campos_a_revisar=review,
        campos_reconstruidos=merger.reconstructed,
        validaciones=merger.checks, advertencias=warnings, meta=meta,
    )
    if include_crops:
        result.recortes = {
            name: base64.b64encode(encode_png(side.image, 1400)).decode()
            for name, side in (("anverso", layout.front), ("reverso", layout.back)) if side is not None
        }
    return result
