"""Carga de PDFs e imágenes a arreglos BGR (formato OpenCV)."""

import cv2
import numpy as np
import pymupdf


class DocumentError(ValueError):
    """El archivo no se pudo leer. `codigo` es el código de la respuesta (222 = archivo corrupto)."""

    codigo = 222


class PdfProtegido(DocumentError):
    codigo = 223


class LimitePaginas(DocumentError):
    codigo = 224


PDF_MAGIC = b"%PDF"


def load_pages(data: bytes, dpi: int, max_pages: int, max_paginas_documento: int | None = None) -> list[np.ndarray]:
    """Devuelve las páginas del archivo como imágenes BGR.

    `max_pages` es cuántas se renderizan (la credencial viene al principio); un PDF con más de
    `max_paginas_documento` páginas se rechaza completo (LimitePaginas).
    """
    if not data:
        raise DocumentError("El archivo llegó vacío")
    if PDF_MAGIC in data[:1024]:
        return _load_pdf(data, dpi, max_pages, max_paginas_documento)
    return [_load_image(data)]


def _load_pdf(data: bytes, dpi: int, max_pages: int, max_paginas_documento: int | None) -> list[np.ndarray]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise DocumentError(f"PDF inválido: {e}") from e
    if doc.needs_pass:
        raise PdfProtegido("El PDF está protegido con contraseña")
    if doc.page_count == 0:
        raise DocumentError("El PDF no tiene páginas")
    if doc.is_repaired and b"%%EOF" not in data[-4096:]:
        # pymupdf "repara" PDFs cortados al subirlos; sin el final se pierden páginas o imágenes
        raise DocumentError("El PDF está incompleto: le falta el final (posiblemente se cortó al subirlo)")
    if max_paginas_documento and doc.page_count > max_paginas_documento:
        raise LimitePaginas(f"El documento trae {doc.page_count} páginas; el máximo por llamada es "
                            f"{max_paginas_documento}")
    pages = []
    try:
        for page in list(doc)[:max_pages]:
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3)
            pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    except Exception as e:  # noqa: BLE001
        raise DocumentError(f"No se pudo leer una página del PDF: {e}") from e
    return pages


def _load_image(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise DocumentError("No se reconoce el formato del archivo o la imagen está dañada")
    return img


def encode_png(img: np.ndarray, max_side: int | None = None) -> bytes:
    if max_side:
        h, w = img.shape[:2]
        scale = max_side / max(h, w)
        if scale < 1:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise DocumentError("No se pudo codificar la imagen")
    return buf.tobytes()
