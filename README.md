# INE OCR API

API en FastAPI que extrae los datos del **anverso y reverso** de credenciales INE
(PDF; fotocopias en blanco y negro, con sellos) y devuelve un JSON
estructurado con **confianza por campo** y **validaciones**.

Todo corre local y gratis: **PaddleOCR** + **Qwen3-VL 8B** (Ollama en Mac, vLLM en servidor).

## Cómo funciona

```
PDF/imagen ─► páginas (250 dpi)
           ─► PaddleOCR de la página ─► ubica anverso (encabezado INE) y reverso (MRZ)
                                       └─ si no las encuentra, prueba girar 90°/180°
           ─► recortes ─► Qwen3-VL lee el anverso (JSON con esquema fijo)
                       └► MRZ del reverso: OCR; si no cuadran los dígitos verificadores, también el VLM
           ─► fusión + validaciones ─► JSON
```

**Fuentes que se cruzan para cada dato**

| Dato | Fuentes |
|---|---|
| Fecha de nacimiento | MRZ (dígito verificador), CURP, clave de elector, VLM, OCR |
| Sexo | MRZ, CURP, clave de elector, VLM |
| CURP / clave de elector | VLM vs OCR, estructura + dígito verificador del CURP, fecha/sexo de la MRZ, iniciales del nombre (si VLM y OCR leen dos códigos válidos distintos, se queda el único que cuadra con MRZ y nombre: `*_desempate`) |
| Nombre | VLM vs OCR vs línea 3 de la MRZ, iniciales de CURP y clave |
| Sección, vigencia | VLM vs MRZ |
| CIC, identificador | MRZ con dígito verificador |
| Domicilio | VLM vs OCR, código postal y estado reconocidos |

**Correcciones automáticas**
- Errores típicos de OCR según el tipo de posición (O/0, I/1, S/5, B/8...).
- **Ñ perdida** en la fotocopia (NUNEZ → NUÑEZ) si la clave de elector / CURP lo confirman.
- **CURP o clave tapadas por un sello**: se reconstruyen con nombre + fecha/sexo validados
  por la MRZ + estado de nacimiento + la parte legible. Se marcan en `campos_reconstruidos`
  y por defecto el documento va a revisión (`REVISAR_RECONSTRUIDOS`).

**Credenciales inclinadas, chicas o muy tenues.** El anverso y el reverso se ubican con sus anclas
en el texto del OCR (encabezado y MRZ), probando la página girada 90°, 180° y 270° si hace falta.
Si en ninguna página aparece un ancla, el modelo de visión ubica las credenciales (`VLM_LOCALIZADOR`),
cada recorte se endereza y amplía, y se repite el OCR ahí; `meta.recorte_anverso` / `recorte_reverso`
dicen cómo se obtuvo cada cara: `ancla`, `localizador` o `pagina_completa`.

**Sólo credenciales INE mexicanas.** Con el texto del OCR, antes de llamar al modelo de visión:
0. Hoja en blanco o sin texto legible (menos de 10 letras/dígitos en todo el OCR) → `rechazado` de inmediato.
1. Texto de otro documento (LICENCIA PARA CONDUCIR, PASAPORTE, RFC, TIPO DE SANGRE, IMSS, INM, cédula,
   acta…), la MRZ de pasaporte (`P<MEX…`) o la de una credencial de otro país (`IDUSA…`) → `rechazado`
   de inmediato, sin gastar GPU.
2. Se cuentan las señales de INE (`meta.senales_ine`): las **exclusivas** (encabezado "INSTITUTO NACIONAL
   ELECTORAL", "CREDENCIAL PARA VOTAR", MRZ `IDMEX`, "CLAVE DE ELECTOR", "AÑO DE REGISTRO") y las genéricas
   (CURP, NOMBRE, VIGENCIA…, que también traen licencias y pasaportes). Si no hay ninguna, el OCR no vio
   la credencial pero el modelo de visión todavía puede verla: el documento sigue (sólo con el VLM
   apagado se rechaza aquí).
3. Si el modelo de visión ya leyó la imagen y aun así no hay encabezado, MRZ válida ni clave de elector
   con estructura válida, se rechaza (el CURP no cuenta: lo traen otros documentos).

El resultado lleva `estado = "rechazado"` y el motivo en `error`.
Se apaga con `RECHAZAR_NO_INE=false`.
La API sólo acepta **PDF** (`SOLO_PDF=true`; otro formato responde 415).

**Confianza**: `alta` = dos fuentes independientes coinciden o lo respalda un dígito
verificador · `media` = una fuente con estructura válida · `baja` = desacuerdo o ilegible.
`estado = "revisar"` si un campo crítico queda en `baja`, la MRZ no es válida o hubo reconstrucción.

## Instalación (Mac)

```bash
brew install ollama && brew services start ollama
ollama pull qwen3-vl:8b-instruct     # usar la variante instruct (la normal "piensa" y es lentísima)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

> Nota: `opencv-contrib-python` (dependencia de PaddleOCR) choca con
> `opencv-python-headless`; no instales ambos en el mismo entorno.

## Uso

**Interfaz web de prueba**

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

Abrir http://localhost:8000: arrastra uno o varios PDF/imágenes y verás los recortes
detectados, los datos con su confianza, las validaciones y el JSON (copiar / descargar).

**API**

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
curl -X POST localhost:8000/v1/ine/extraer -F "archivo=@ine.pdf"
```

- Anverso y reverso en archivos separados: `-F "archivo=@frente.jpg" -F "reverso=@reverso.jpg"`
- Recortes en base64 para una pantalla de revisión: `?incluir_recortes=true`
- Documentación interactiva: http://localhost:8000/docs

**Lote (p. ej. 500 PDFs)**

```bash
.venv/bin/python -m scripts.procesar_lote ~/carpeta_ines --salida resultados --recortes
```

Genera `resultados/json/*.json`, `resultados/resultados.csv` (una fila por INE, abre en
Excel) y `resultados/resumen.md` (tasas de OK/revisar, confianza por campo, validaciones
que más fallan). Es reanudable: si se corta, vuelve a correrlo y continúa.

**Medir la precisión real**

```bash
.venv/bin/python -m scripts.evaluar plantilla --resultados resultados/resultados.csv --n 50
# corregir verdad.csv a mano y marcar verificado = si
.venv/bin/python -m scripts.evaluar comparar --verdad verdad.csv --resultados resultados/resultados.csv
```

Muestra la exactitud por campo y por nivel de confianza (lo clave: que `alta` sea ~100 %).

**Pruebas**

```bash
.venv/bin/python -m pytest -q
```

## Servidor (Docker)

Tres contenedores: **PostgreSQL** (historial), **Ollama** (modelo de visión) y **API**
(FastAPI + PaddleOCR + interfaz). El único modelo de Ollama es **`qwen3-vl:8b-instruct`**
y se descarga solo la primera vez (≈ 6 GB). Los modelos de PaddleOCR vienen dentro de la imagen.

```bash
cp .env.example .env            # editar POSTGRES_PASSWORD (obligatorio) y API_KEY (recomendado)
docker compose up -d --build                                                   # sin GPU
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # con GPU NVIDIA
docker compose logs -f api      # esperar "Application startup complete"
```

- API e interfaz: `http://SERVIDOR:18473` (puerto configurable con `API_PORT`).
- Ollama y PostgreSQL no se exponen fuera del servidor.
- Los datos persisten en los volúmenes `ine-ocr_pg-data` y `ine-ocr_ollama-models`.
- Requisitos: Docker con Compose v2; ~12 GB de RAM libres (sin GPU) o GPU con ≥ 10 GB de VRAM;
  ~15 GB de disco; internet en el primer arranque (imágenes + modelo).
- Con GPU se necesita el NVIDIA Container Toolkit.

### Pruebas por lote en el servidor

El servicio `lote` corre `scripts/procesar_lote.py` dentro de Docker, con el mismo Ollama que la API
(no arranca con `up`; sólo con `run`). Los PDFs nunca salen del servidor.

```bash
mkdir -p lote/pdfs lote/resultados        # copiar los PDFs a lote/pdfs (scp/rsync)
docker compose run --rm lote lote/pdfs --salida lote/resultados --concurrencia 3 --recortes
docker compose run --rm lote --salida lote/resultados --solo-resumen     # sólo regenerar reportes
```

Genera en `lote/resultados/`:
- `resumen.md`: tasas, tiempos por etapa, validaciones que más fallan (sin datos personales).
- `diagnostico.csv`: una fila por documento con estado, reglas que fallaron, advertencias,
  confianza y tiempos, **sin nombres, CURP ni domicilio**. Es lo que se comparte para analizar mejoras.
  Incluye las similitudes VLM/OCR/MRZ del nombre y domicilio (`sim_*`), qué parte del nombre no
  cuadra con los códigos (`nombre_vs_codigos_difiere`: paterno / materno / nombre) y los desempates.
- `resultados.csv` y `json/`: datos completos (personales; no sacar del servidor).

Si el contenedor no puede escribir en `lote/`: `sudo chown -R 1000:1000 lote`.
El lote comparte el modelo con producción: córrelo fuera de horario o con `--concurrencia` baja.

### Endpoints

| Método | Ruta | Uso |
|---|---|---|
| POST | `/v1/ine/extraer` | Extrae una INE (PDF/imagen). Devuelve el JSON y su `id` |
| GET | `/v1/ine/extracciones?estado=revisar&q=perez&curp=...` | Historial con filtros |
| GET | `/v1/ine/extracciones/{id}` | Resultado completo guardado |
| PUT | `/v1/ine/extracciones/{id}/revision` | Guarda la corrección humana (`{"datos": {...}, "revisado_por": "..."}`) |
| GET | `/health` | Estado del modelo y de la base de datos |

`estado` puede ser `ok`, `revisar`, `rechazado` (no es una INE; ver `error`) o `error`.
Si `API_KEY` está definida, todas (menos `/health`) piden el header `X-API-Key`.
La base guarda los datos extraídos, nunca las imágenes.

## Ejemplo de respuesta (resumida)

```json
{
  "estado": "ok",
  "datos": {
    "nombre": {"apellido_paterno": "PEREZ", "apellido_materno": "LOPEZ", "nombres": "JUAN", "nombre_completo": "JUAN PEREZ LOPEZ"},
    "sexo": "H",
    "fecha_nacimiento": "1985-03-12",
    "curp": "PELJ850312HDFRPN0X",
    "clave_elector": "PRLPJN85031209H400",
    "domicilio": {"calle": "C REFORMA 10", "colonia": "COL CENTRO", "codigo_postal": "06000",
                  "municipio": "CUAUHTEMOC", "estado": "CIUDAD DE MEXICO", "lineas": ["..."]},
    "seccion": "1234", "anio_registro": "2003", "anio_registro_sufijo": "02",
    "vigencia": {"desde": "2020", "hasta": "2030", "vigente": true},
    "cic": "123456789", "identificador_ciudadano": "567890123", "numero_ocr": "1234567890123",
    "mrz": ["IDMEX...", "...", "..."], "modelo_credencial": "G/H"
  },
  "confianza": {"nombre": "alta", "curp": "alta", "domicilio": "alta", "cic": "alta"},
  "campos_a_revisar": [],
  "campos_reconstruidos": [],
  "validaciones": [{"regla": "mrz_cic", "resultado": "ok"}],
  "advertencias": [],
  "meta": {"tiempo_total_ms": 27676, "tiempos_ms": {"ocr_y_recorte": 6000, "vlm_anverso": 21000}}
}
```

## Privacidad

Las INE son datos personales. Todo se procesa localmente; los logs sólo registran estado y
tiempos (nunca nombres ni CURP). `samples/` y `resultados/` están en `.gitignore`.
