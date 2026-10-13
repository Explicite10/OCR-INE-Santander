"""Carga de PDFs e imágenes a arreglos BGR (formato OpenCV)."""

import cv2
import numpy as np
import pymupdf


class DocumentError(ValueError):
    pass


PDF_MAGIC = b"%PDF"


def load_pages(data: bytes, dpi: int, max_pages: int) -> list[np.ndarray]:
    """Devuelve las páginas del archivo como imágenes BGR."""
    if PDF_MAGIC in data[:1024]:
        return _load_pdf(data, dpi, max_pages)
    return [_load_image(data)]


def _load_pdf(data: bytes, dpi: int, max_pages: int) -> list[np.ndarray]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise DocumentError(f"PDF inválido: {e}") from e
    if doc.page_count == 0:
        raise DocumentError("El PDF no tiene páginas")
    pages = []
    for page in list(doc)[:max_pages]:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3)
        pages.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return pages


def _load_image(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise DocumentError("Formato no soportado: se espera PDF, JPG, PNG o similar")
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
