from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración vía variables de entorno o archivo .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- VLM ---
    # "ollama" usa la API nativa de Ollama (local en Mac).
    # "openai" usa cualquier servidor compatible con OpenAI (vLLM, LM Studio, etc.).
    vlm_backend: Literal["ollama", "openai"] = "ollama"
    vlm_base_url: str = "http://localhost:11434"
    vlm_model: str = "qwen3-vl:8b-instruct"
    vlm_api_key: str = "EMPTY"
    vlm_timeout_s: float = 180.0
    vlm_max_side: int = 1600  # lado mayor (px) de la imagen que se manda al VLM
    vlm_max_retries: int = 2
    vlm_num_ctx: int = 8192
    # Ollama: False desactiva el "razonamiento" en modelos que lo soportan; None no lo envía.
    vlm_think: bool | None = None
    vlm_enabled: bool = True  # False = sólo OCR + MRZ (útil para depurar)

    # --- OCR ---
    ocr_det_model: str = "PP-OCRv5_mobile_det"
    ocr_rec_model: str = "PP-OCRv6_medium_rec"
    ocr_enabled: bool = True

    # --- Documento ---
    render_dpi: int = 250
    max_pages: int = 4
    max_upload_mb: int = 25

    # Si un campo se reconstruyó (p. ej. CURP tapada por un sello) el documento va a revisión.
    revisar_reconstruidos: bool = True

    # Rechazar documentos que no parecen una credencial INE mexicana (estado "rechazado").
    # Antes del VLM se suman señales del OCR (encabezado +3, MRZ mexicana +3, cada etiqueta +1);
    # con menos de `ine_min_senales` se rechaza. Al final también se rechaza si no se reconoció nada.
    rechazar_no_ine: bool = True
    ine_min_senales: int = 2

    # --- API ---
    # Número de documentos que se procesan a la vez dentro del proceso.
    max_concurrent_jobs: int = 2
    api_key: str | None = None  # si se define, se exige el header X-API-Key
    enable_ui: bool = True  # página de prueba en "/"
    enable_docs: bool = True  # documentación interactiva en "/docs"

    # --- Base de datos (opcional) ---
    # p. ej. postgresql+asyncpg://ine:clave@db:5432/ine ; vacío = no se guarda nada
    database_url: str | None = None
    guardar_resultados: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
