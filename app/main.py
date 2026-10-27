import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Query, Request, Security, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import pipeline, plan
from .config import get_settings
from .db import Database, Extraccion
from .documents import PDF_MAGIC
from .ocr import engine
from .respuesta import ESTADOS, MENSAJES, con_plan, construir, http_status, sobre
from .schemas import ExtraccionDetalle, ExtraccionResumen, ListaExtracciones, Revision
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
_TAGS = [
    {"name": "Credencial INE", "description": "Endpoint principal. Usar este en los sistemas que consumen la API."},
    {"name": "Historial y revisión", "description": "Consulta de extracciones guardadas y captura de la revisión humana."},
    {"name": "Sistema", "description": "Estado del servicio."},
]
app = FastAPI(
    title="OCR Credencial para Votar INE - Open Source",
    version="1.0.0",
    description="Extracción de la credencial para votar del INE (anverso y reverso) con PaddleOCR + Qwen3-VL "
                "y validación contra MRZ, CURP y clave de elector.",
    openapi_tags=_TAGS,
    lifespan=lifespan,
    docs_url="/docs" if _s.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _s.enable_docs else None,
)


# En /docs aparece como el botón "Authorize" (candado): se captura una vez y va en todas las llamadas.
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False,
                        description="El valor de API_KEY del .env del servidor (si no está definida, no se pide)")


def _error_doc(*codigos: int) -> dict:
    """Errores documentados en /docs con el mismo sobre que devuelve la API."""
    return {c: {"description": ESTADOS[c], "content": {"application/json": {"example": sobre(c)}}} for c in codigos}


def check_api_key(x_api_key: str | None = Security(_api_key)) -> None:
    expected = get_settings().api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail=MENSAJES[401])


# ------------------------------------------------------------------ errores con el mismo sobre
# Toda respuesta abre con codigo / estado / mensaje, también las fallas (404, 422, 500...).


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    mensaje = exc.detail if isinstance(exc.detail, str) and exc.detail not in ("Not Found", "Method Not Allowed") else None
    return JSONResponse(sobre(exc.status_code, mensaje), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    errores = [f"{'.'.join(str(x) for x in e.get('loc', []) if x != 'body')}: {e.get('msg')}" for e in exc.errors()]
    return JSONResponse(sobre(422, f"{MENSAJES[422]} {'; '.join(errores)}"), status_code=422)


@app.exception_handler(Exception)
async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Error no controlado")
    return JSONResponse(sobre(500), status_code=500)


@app.get("/health", tags=["Sistema"], summary="Estado del servicio")
async def health():
    s = get_settings()
    vlm_ok = await app.state.vlm.health() if app.state.vlm else None
    return {"status": "ok" if vlm_ok is not False else "degradado", "vlm": vlm_ok,
            "modelo_vlm": s.vlm_model, "backend_vlm": s.vlm_backend,
            "base_de_datos": app.state.db is not None}


# Tipos aceptados (por contenido, no por el nombre): PDF y, si SOLO_PDF=false, imágenes
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"II*\x00", b"MM\x00*", b"BM")


def _tipo(data: bytes) -> str | None:
    if PDF_MAGIC in data[:1024]:
        return "pdf"
    if data.startswith(_IMAGE_MAGIC) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "imagen"
    return None


async def _read(f: UploadFile) -> bytes | JSONResponse:
    """Lee el archivo; si no se puede procesar devuelve directamente la respuesta (400/413/222)."""
    s = get_settings()
    limit = s.max_upload_mb * 1024 * 1024
    data = await f.read(limit + 1)
    if len(data) > limit:
        return JSONResponse(sobre(413, f"{f.filename}: excede {s.max_upload_mb} MB"), status_code=413)
    if not data:
        return JSONResponse(sobre(222, f"{f.filename}: el archivo llegó vacío"))
    tipo = _tipo(data)
    dice_pdf = (f.content_type == "application/pdf") or (f.filename or "").lower().endswith(".pdf")
    if dice_pdf and tipo != "pdf":
        return JSONResponse(sobre(222, f"{f.filename}: dice ser PDF pero su contenido es otra cosa"))
    if tipo is None or (s.solo_pdf and tipo != "pdf"):
        permitidos = "PDF" if s.solo_pdf else "PDF, JPEG, PNG, TIFF o WEBP"
        return JSONResponse(sobre(400, f"{f.filename}: tipo no soportado; se acepta {permitidos}"), status_code=400)
    return data


_EJEMPLO = {"codigo": 200, "estado": "PROCESADO", "mensaje": MENSAJES[200],
            "frente": {"apellido_paterno": "RAMIREZ", "nombre": "ANA LUCIA", "curp": "RASA880315MDFMLN04"},
            "atras": {"mrz": {"cic": "123456789", "integra": True}},
            "validacion": {"ok": True, "completo": True, "coincidencias": ["MRZ (dígitos de control)"],
                           "discrepancias": [], "sin_verificar": []},
            "avisos": [], "clasificacion": {"categoria": "ine", "confianza": None},
            "validacion_plan": {"cumple": False, "mensaje": plan.MENSAJE_RECHAZO,
                                "motivos_rechazo": ["¿La CURP coincide? NO: La CURP no coincide: credencial "
                                                    "'RASA880315MDFMLN04' vs solicitud 'RASA880315MDFMLN05'."],
                                "regla_vigencia": "anio", "validaciones": [
                                    {"id": "curp_coincide", "pregunta": "¿La CURP coincide?", "resultado": "NO",
                                     "ok": False, "detalle": "La CURP no coincide: credencial 'RASA880315MDFMLN04' "
                                                             "vs solicitud 'RASA880315MDFMLN05'."}]}}

_D_NOMBRE = ("Nombre completo capturado en la solicitud, en cualquier orden. "
             "Alternativa: apellido_paterno, apellido_materno y nombres.")
_D_CURP = "CURP capturada en la solicitud."
_D_FECHA = "Fecha de nacimiento capturada: AAAA-MM-DD (también acepta DD/MM/AAAA)."


@app.post("/ine", dependencies=[Depends(check_api_key)], response_class=JSONResponse, tags=["Credencial INE"],
          summary="Extrae los datos de una credencial INE y valida contra la solicitud",
          description="Los datos de la solicitud son opcionales; con ellos `validacion_plan` responde las 7 "
                      "preguntas del plan de atención (hoja en blanco, ilegible, es INE, vigencia, nombre, "
                      "CURP y fecha de nacimiento).",
          responses={200: {"description": "Desenlace en `codigo` (200, 210-212, 220-224)",
                           "content": {"application/json": {"example": _EJEMPLO}}},
                     **_error_doc(400, 401, 413, 422, 500)})
async def ine(
    file: UploadFile = File(..., description="PDF con la credencial (anverso y reverso)"),
    nombre: str | None = Form(None, description=_D_NOMBRE),
    apellido_paterno: str | None = Form(None), apellido_materno: str | None = Form(None),
    nombres: str | None = Form(None), curp: str | None = Form(None, description=_D_CURP),
    fecha_nacimiento: str | None = Form(None, description=_D_FECHA),
) -> JSONResponse:
    solicitud = plan.Solicitud(nombre=nombre, apellido_paterno=apellido_paterno, apellido_materno=apellido_materno,
                               nombres=nombres, curp=curp, fecha_nacimiento=fecha_nacimiento)
    data = await _read(file)
    if isinstance(data, JSONResponse):
        return data
    async with app.state.sem:
        result = await pipeline.process([data], app.state.vlm, file.filename)
    log.info("archivo procesado estado=%s tiempo_ms=%s revisar=%s", result.estado,
             result.meta.tiempo_total_ms if result.meta else None, result.campos_a_revisar)
    if app.state.db is not None and get_settings().guardar_resultados:
        try:
            result.id = await app.state.db.save(result.model_dump(mode="json"))
        except Exception:  # noqa: BLE001  (no perder la extracción si falla el guardado)
            log.exception("No se pudo guardar el resultado")
            result.advertencias.append("No se pudo guardar el resultado en la base de datos")
    body = con_plan(construir(result, get_settings().revisar_reconstruidos), solicitud)
    log.info("codigo=%s cumple_plan=%s", body["codigo"], body.get("validacion_plan", {}).get("cumple"))
    return JSONResponse(body, status_code=http_status(body["codigo"]))


# ------------------------------------------------------------------ historial


def _db() -> Database:
    if app.state.db is None:
        raise HTTPException(status_code=503, detail="Base de datos no configurada (DATABASE_URL)")
    return app.state.db


def _resumen(r: Extraccion) -> ExtraccionResumen:
    return ExtraccionResumen(id=r.id, creado=r.creado, archivo=r.archivo, estado=r.estado, curp=r.curp,
                             clave_elector=r.clave_elector, nombre_completo=r.nombre_completo,
                             campos_a_revisar=r.campos_a_revisar or [], tiempo_ms=r.tiempo_ms)


@app.get("/v1/ine/extracciones", response_model=ListaExtracciones, dependencies=[Depends(check_api_key)],
         responses=_error_doc(401, 503), tags=["Historial y revisión"], summary="Lista las extracciones guardadas")
async def listar(
    estado: str | None = Query(None, description="ok | revisar | error | revisado"),
    curp: str | None = None,
    q: str | None = Query(None, description="Busca en nombre o archivo"),
    limite: int = Query(50, ge=1, le=500),
    desde: int = Query(0, ge=0),
) -> ListaExtracciones:
    total, rows = await _db().list(estado, curp, q, limite, desde)
    return ListaExtracciones(total=total, elementos=[_resumen(r) for r in rows])


@app.get("/v1/ine/extracciones/{id_}", response_model=ExtraccionDetalle, dependencies=[Depends(check_api_key)],
         responses=_error_doc(401, 404), tags=["Historial y revisión"], summary="Detalle de una extracción")
async def detalle(id_: str) -> ExtraccionDetalle:
    r = await _db().get(id_)
    if r is None:
        raise HTTPException(status_code=404, detail="No existe")
    return ExtraccionDetalle(**_resumen(r).model_dump(), resultado=r.resultado,
                             datos_corregidos=r.datos_corregidos, revisado_por=r.revisado_por,
                             revisado_en=r.revisado_en)


@app.put("/v1/ine/extracciones/{id_}/revision", response_model=ExtraccionDetalle,
         dependencies=[Depends(check_api_key)], responses=_error_doc(401, 404), tags=["Historial y revisión"],
         summary="Guarda la revisión humana de una extracción")
async def revisar(id_: str, revision: Revision = Body(...)) -> ExtraccionDetalle:
    """Guarda los datos corregidos por una persona y marca la extracción como 'revisado'."""
    r = await _db().review(id_, revision.datos, revision.revisado_por)
    if r is None:
        raise HTTPException(status_code=404, detail="No existe")
    return await detalle(id_)
