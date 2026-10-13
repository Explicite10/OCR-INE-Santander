import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from . import pipeline
from .config import get_settings
from .db import Database, Extraccion
from .ocr import engine
from .schemas import ExtraccionDetalle, ExtraccionResumen, ListaExtracciones, ResultadoINE, Revision
from .vlm import VlmClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ine-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    app.state.vlm = VlmClient(s) if s.vlm_enabled else None
    app.state.sem = asyncio.Semaphore(s.max_concurrent_jobs)
    app.state.db = None
    if s.database_url:
        app.state.db = Database(s.database_url)
        await app.state.db.init()
        log.info("Base de datos lista")
    if s.ocr_enabled:
        log.info("Cargando PaddleOCR...")
        await asyncio.to_thread(engine.warmup)
    if app.state.vlm and not await app.state.vlm.health():
        log.warning("El VLM %s no responde en %s", s.vlm_model, s.vlm_base_url)
    yield
    if app.state.vlm:
        await app.state.vlm.aclose()
    if app.state.db:
        await app.state.db.close()


_s = get_settings()
app = FastAPI(
    title="INE OCR API",
    version="1.0.0",
    description="Extracción estructurada de datos de credenciales INE (anverso y reverso).",
    lifespan=lifespan,
    docs_url="/docs" if _s.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _s.enable_docs else None,
)


def check_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="API key inválida")


@app.get("/", include_in_schema=False)
async def ui() -> FileResponse:
    """Interfaz web para probar la extracción (se apaga con ENABLE_UI=false)."""
    if not get_settings().enable_ui:
        raise HTTPException(status_code=404)
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health")
async def health():
    s = get_settings()
    vlm_ok = await app.state.vlm.health() if app.state.vlm else None
    return {"status": "ok" if vlm_ok is not False else "degradado", "vlm": vlm_ok,
            "modelo_vlm": s.vlm_model, "backend_vlm": s.vlm_backend,
            "base_de_datos": app.state.db is not None}


async def _read(f: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"{f.filename}: excede {get_settings().max_upload_mb} MB")
    if not data:
        raise HTTPException(status_code=400, detail=f"{f.filename}: archivo vacío")
    return data


@app.post("/v1/ine/extraer", response_model=ResultadoINE, response_model_exclude_none=False,
          dependencies=[Depends(check_api_key)])
async def extraer(
    archivo: UploadFile = File(..., description="PDF o imagen con anverso (y opcionalmente reverso)"),
    reverso: UploadFile | None = File(None, description="Reverso, si viene en un archivo aparte"),
    incluir_recortes: bool = Query(False, description="Devuelve los recortes en base64 (PNG) para revisión"),
) -> ResultadoINE:
    files = [await _read(archivo)]
    if reverso is not None:
        files.append(await _read(reverso))
    async with app.state.sem:
        result = await pipeline.process(files, app.state.vlm, archivo.filename, include_crops=incluir_recortes)
    log.info("archivo procesado estado=%s tiempo_ms=%s revisar=%s", result.estado,
             result.meta.tiempo_total_ms if result.meta else None, result.campos_a_revisar)
    if app.state.db is not None and get_settings().guardar_resultados:
        try:
            result.id = await app.state.db.save(result.model_dump(mode="json", exclude={"recortes"}))
        except Exception:  # noqa: BLE001  (no perder la extracción si falla el guardado)
            log.exception("No se pudo guardar el resultado")
            result.advertencias.append("No se pudo guardar el resultado en la base de datos")
    if result.estado == "error":
        raise HTTPException(status_code=422, detail=result.error)
    return result


# ------------------------------------------------------------------ historial


def _db() -> Database:
    if app.state.db is None:
        raise HTTPException(status_code=503, detail="Base de datos no configurada (DATABASE_URL)")
    return app.state.db


def _resumen(r: Extraccion) -> ExtraccionResumen:
    return ExtraccionResumen(id=r.id, creado=r.creado, archivo=r.archivo, estado=r.estado, curp=r.curp,
                             clave_elector=r.clave_elector, nombre_completo=r.nombre_completo,
                             campos_a_revisar=r.campos_a_revisar or [], tiempo_ms=r.tiempo_ms)


@app.get("/v1/ine/extracciones", response_model=ListaExtracciones, dependencies=[Depends(check_api_key)])
async def listar(
    estado: str | None = Query(None, description="ok | revisar | error | revisado"),
    curp: str | None = None,
    q: str | None = Query(None, description="Busca en nombre o archivo"),
    limite: int = Query(50, ge=1, le=500),
    desde: int = Query(0, ge=0),
) -> ListaExtracciones:
    total, rows = await _db().list(estado, curp, q, limite, desde)
    return ListaExtracciones(total=total, elementos=[_resumen(r) for r in rows])


@app.get("/v1/ine/extracciones/{id_}", response_model=ExtraccionDetalle, dependencies=[Depends(check_api_key)])
async def detalle(id_: str) -> ExtraccionDetalle:
    r = await _db().get(id_)
    if r is None:
        raise HTTPException(status_code=404, detail="No existe")
    return ExtraccionDetalle(**_resumen(r).model_dump(), resultado=r.resultado,
                             datos_corregidos=r.datos_corregidos, revisado_por=r.revisado_por,
                             revisado_en=r.revisado_en)


@app.put("/v1/ine/extracciones/{id_}/revision", response_model=ExtraccionDetalle,
         dependencies=[Depends(check_api_key)])
async def revisar(id_: str, revision: Revision = Body(...)) -> ExtraccionDetalle:
    """Guarda los datos corregidos por una persona y marca la extracción como 'revisado'."""
    r = await _db().review(id_, revision.datos, revision.revisado_por)
    if r is None:
        raise HTTPException(status_code=404, detail="No existe")
    return await detalle(id_)
