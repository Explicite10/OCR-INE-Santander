"""Envoltura de PaddleOCR: carga perezosa, un solo modelo por proceso."""

import os
import threading
from dataclasses import dataclass

import numpy as np

from .config import get_settings

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


@dataclass(frozen=True)
class Line:
    text: str
    score: float
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def w(self) -> int:
        return self.x1 - self.x0


class OcrEngine:
    def __init__(self) -> None:
        self._ocr = None
        self._load_lock = threading.Lock()
        # PaddlePaddle no es seguro entre hilos con un mismo predictor.
        self._run_lock = threading.Lock()

    def _get(self):
        if self._ocr is None:
            with self._load_lock:
                if self._ocr is None:
                    from paddleocr import PaddleOCR

                    s = get_settings()
                    self._ocr = PaddleOCR(
                        text_detection_model_name=s.ocr_det_model,
                        text_recognition_model_name=s.ocr_rec_model,
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        use_textline_orientation=False,
                    )
        return self._ocr

    def warmup(self) -> None:
        self.read(np.full((64, 256, 3), 255, np.uint8))

    def read(self, img: np.ndarray) -> list[Line]:
        ocr = self._get()
        with self._run_lock:
            res = ocr.predict(img)[0]
        lines = []
        for text, score, box in zip(res["rec_texts"], res["rec_scores"], res["rec_boxes"]):
            text = text.strip()
            if not text:
                continue
            x0, y0, x1, y1 = (int(v) for v in box)
            lines.append(Line(text, float(score), x0, y0, x1, y1))
        return lines


engine = OcrEngine()
