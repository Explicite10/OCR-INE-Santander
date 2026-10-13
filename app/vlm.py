"""Cliente del modelo de visión (VLM) con salida JSON estructurada.

Soporta dos backends con la misma interfaz:
* ollama: API nativa (/api/chat) — para desarrollo local en Mac.
* openai: API compatible con OpenAI (/v1/chat/completions) — vLLM, LM Studio, etc.
"""

import asyncio
import base64
import json
import logging
import time
from typing import Any

import httpx
import numpy as np

from .config import Settings, get_settings
from .documents import encode_png

log = logging.getLogger(__name__)


class VlmError(RuntimeError):
    pass


FRONT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "apellido_paterno": {"type": "string"},
        "apellido_materno": {"type": "string"},
        "nombres": {"type": "string"},
        "sexo": {"type": "string", "enum": ["H", "M", "X", ""]},
        "domicilio_lineas": {"type": "array", "items": {"type": "string"}},
        "clave_elector": {"type": "string"},
        "curp": {"type": "string"},
        "fecha_nacimiento": {"type": "string"},
        "anio_registro": {"type": "string"},
        "seccion": {"type": "string"},
        "vigencia": {"type": "string"},
        "estado": {"type": "string"},
        "municipio": {"type": "string"},
        "localidad": {"type": "string"},
        "emision": {"type": "string"},
        "campos_ilegibles": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "apellido_paterno", "apellido_materno", "nombres", "sexo", "domicilio_lineas",
        "clave_elector", "curp", "fecha_nacimiento", "anio_registro", "seccion", "vigencia",
        "estado", "municipio", "localidad", "emision", "campos_ilegibles",
    ],
}

FRONT_PROMPT = """Eres un sistema de captura de datos. La imagen es el ANVERSO de una credencial para votar del INE (México), fotocopiada en blanco y negro; puede tener sellos o manchas encima.

Transcribe EXACTAMENTE lo que está impreso en cada campo, carácter por carácter, en MAYÚSCULAS:
- apellido_paterno, apellido_materno, nombres: las líneas bajo "NOMBRE" (1ª línea = apellido paterno, 2ª = apellido materno, el resto = nombres). Conserva Ñ y acentos si aparecen.
- sexo: H o M (campo "SEXO").
- domicilio_lineas: cada renglón bajo "DOMICILIO" como un elemento, tal cual aparece.
- clave_elector: 18 caracteres después de "CLAVE DE ELECTOR".
- curp: 18 caracteres después de "CURP".
- fecha_nacimiento: formato DD/MM/AAAA.
- anio_registro: tal cual (ej. "2008 02").
- seccion: 4 dígitos.
- vigencia: tal cual (ej. "2019 - 2029" o "2028").
- estado, municipio, localidad, emision: sólo existen en algunos modelos; si no aparecen deja "".

Reglas:
- NO inventes ni completes datos. Si un carácter o campo no se puede leer con certeza, deja el campo como "" y agrega su nombre a campos_ilegibles.
- Ignora los sellos, firmas y textos que no sean de la credencial.
- Distingue con cuidado 0/O, 1/I, 5/S, 8/B, 2/Z: en CURP y clave de elector las posiciones 5-10 son dígitos de la fecha.
- Fíjate en la tilde de la Ñ (NUÑEZ, MUÑOZ, PEÑA, IBAÑEZ, ORDOÑEZ...): en fotocopias es pequeña pero está.
- La clave de elector y el CURP tienen exactamente 18 caracteres."""

BACK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mrz_linea1": {"type": "string"},
        "mrz_linea2": {"type": "string"},
        "mrz_linea3": {"type": "string"},
    },
    "required": ["mrz_linea1", "mrz_linea2", "mrz_linea3"],
}

BACK_PROMPT = """La imagen es el REVERSO de una credencial para votar del INE (México), fotocopiada en blanco y negro.
En la parte inferior hay 3 renglones de texto tipo máquina (MRZ) de 30 caracteres cada uno; el primero empieza con "IDMEX".
Transcribe los 3 renglones EXACTAMENTE, carácter por carácter, incluyendo cada símbolo "<". No agregues espacios.
Si un carácter no se distingue, escribe tu mejor lectura; no omitas caracteres."""


class VlmClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self._client = httpx.AsyncClient(timeout=self.s.vlm_timeout_s)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def extract(self, img: np.ndarray, prompt: str, schema: dict[str, Any]) -> tuple[dict, float]:
        """Devuelve (json, segundos)."""
        if img is None or img.size == 0:
            raise VlmError("Imagen vacía")
        b64 = base64.b64encode(encode_png(img, self.s.vlm_max_side)).decode()
        last: Exception | None = None
        for attempt in range(self.s.vlm_max_retries + 1):
            t0 = time.perf_counter()
            try:
                if self.s.vlm_backend == "ollama":
                    text = await self._ollama(b64, prompt, schema)
                else:
                    text = await self._openai(b64, prompt, schema)
                return _parse_json(text), time.perf_counter() - t0
            except (httpx.HTTPError, ValueError, KeyError) as e:
                last = e
                log.warning("VLM intento %d falló: %s", attempt + 1, e)
                await asyncio.sleep(1.5 * (attempt + 1))
        raise VlmError(f"El VLM no respondió correctamente: {last}")

    async def _ollama(self, b64: str, prompt: str, schema: dict) -> str:
        payload = {
            "model": self.s.vlm_model,
            "stream": False,
            "format": schema,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": self.s.vlm_num_ctx, "num_predict": 800},
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        }
        if self.s.vlm_think is not None:
            payload["think"] = self.s.vlm_think
        r = await self._client.post(f"{self.s.vlm_base_url.rstrip('/')}/api/chat", json=payload)
        r.raise_for_status()
        return r.json()["message"]["content"]

    async def _openai(self, b64: str, prompt: str, schema: dict) -> str:
        payload = {
            "model": self.s.vlm_model,
            "temperature": 0,
            "max_tokens": 800,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "ine", "schema": schema, "strict": True}},
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }],
        }
        base = self.s.vlm_base_url.rstrip("/")
        url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
        r = await self._client.post(url, json=payload,
                                    headers={"Authorization": f"Bearer {self.s.vlm_api_key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    async def health(self) -> bool:
        base = self.s.vlm_base_url.rstrip("/")
        try:
            if self.s.vlm_backend == "ollama":
                r = await self._client.get(f"{base}/api/tags", timeout=5)
                names = {m["name"] for m in r.json().get("models", [])}
                return self.s.vlm_model in names
            r = await self._client.get(f"{base if base.endswith('/v1') else base + '/v1'}/models", timeout=5)
            return r.status_code == 200
        except httpx.HTTPError:
            return False


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"Respuesta sin JSON: {text[:200]}")
    return json.loads(text[start:end + 1])
