# Evaluación de precisión (lote Santander de INE, 27-oct-2026)

## Cómo se midió

- Lote: 500 PDFs escaneados (fotocopias en blanco y negro con sello "cotejado contra original"),
  procesados con la versión 7 del pipeline (`lote/resultados_v7`, commit `5627bc8`, PaddleOCR + Qwen3-VL 8B).
- Muestra: las 50 credenciales de `scripts.evaluar plantilla` (semilla fija) **más 10 de las que la API
  marcó con 210/211/212**, para no medir sólo los casos fáciles. 60 documentos: 58 INE y 2 que no son INE
  (un pasaporte extranjero con tarjeta INM y un registro de firmas), ambos rechazados correctamente (220).
- Verdad de terreno: cada credencial se leyó de la imagen con un modelo de visión **independiente** del
  pipeline (Claude, sin PaddleOCR ni Qwen), campo por campo, y se guardó en `lote/verdad.csv` (no se versiona:
  datos personales). Las 58 CURP capturadas cuadran con su dígito verificador y los `cic` / `identificador_ciudadano`
  se tomaron de la MRZ. En las 5 diferencias de un solo carácter se amplió la imagen para decidir quién tenía
  razón: en una (`clave_elector` de una credencial girada 90°) el error era de la verdad, no de la API, y se corrigió;
  un código postal tapado por la huella se dejó sin verificar.
- Comparación: `python -m scripts.evaluar comparar --verdad lote/verdad.csv --resultados lote/resultados_v7/resultados.csv`.
  Para el domicilio se ignoraron además las diferencias de puro formato (guion inicial que la credencial sí imprime,
  apóstrofes, espacios alrededor de guiones); lo que queda son errores de lectura reales.

## Resultado (58 credenciales)

| Campo | Exactitud | Errores |
|---|---:|---|
| apellido_materno, nombres, sexo, fecha_nacimiento, seccion, anio_registro, vigencia_hasta, cic, identificador_ciudadano | **100 %** | 0 de 58 |
| apellido_paterno | 98.3 % | 1: apellido compuesto ("MARTINEZ CELIS" → "MARTINEZ"), confianza `media`, código 211 |
| curp | 98.3 % | 1: Ñ leída como N y dígito distinto, confianza `media`, estado `revisar`, código 210 |
| clave_elector | 98.2 % | 1: C leída como G, confianza `baja`, estado `revisar`, código 210 |
| codigo_postal | 98.2 % | 1: no se extrajo, confianza `media`, código 212 |
| domicilio_completo | 80.7 % | 11: una letra cambiada en un renglón (INEONAVIT, QUIRANTE, NUG, LOO, FRACO, GHALCO, PBL0, MENESSES, CIMURIANO, COMX, RRIY) |

- Documentos con **los 14 campos** correctos: 45 de 58 (77.6 %); con **todos los campos críticos** (todo menos
  domicilio y código postal) correctos: **56 de 58 (96.6 %)**.
- Los 2 documentos con un campo crítico mal son justo los que la API devolvió con **210 / 211 y confianza
  `media`/`baja`**: ninguno se habría colado como `200`.

## Exactitud según la confianza que asigna la API

| Confianza | Todos los campos | Campos críticos (sin domicilio) |
|---|---:|---:|
| `alta` | 99.0 % (n=730) | **100 % (n=628)** |
| `media` | 90.7 % (n=75) | 71.4 % (n=7) |
| `baja` | 75.0 % (n=4) | 50.0 % (n=2) |

Lo que importa para aprobar automáticamente: **cuando la API marca un campo crítico con confianza `alta`, en esta
muestra nunca se equivocó**. Las fallas quedan en `media`/`baja`, que es donde el sistema ya pide revisión humana.

## Dónde se equivoca y qué hacer

1. **Domicilio** es el único campo con errores en confianza `alta` (9 de los 11). Son sustituciones de una letra
   en nombres de calle o colonia que no tienen contra qué verificarse (sólo el código postal). Si Santander va a
   comparar el domicilio contra la solicitud, conviene comparar por tokens con tolerancia de 1 carácter, o bajar a
   `media` la confianza del domicilio cuando OCR y VLM no coinciden letra por letra.
2. **Apellidos compuestos** ("MARTINEZ CELIS"): el VLM se queda con la primera palabra. La MRZ sí trae
   `MARTINEZ<CELIS<SA` y la comparación por palabras del plan de atención lo detecta; para `frente.apellido_paterno`
   conviene preferir el renglón completo del OCR cuando la MRZ lo respalda.
3. **Ñ en la CURP** (BAÑUELOS → `X` en la CURP): el VLM leyó `N`; el dígito verificador lo delató y el documento
   salió a revisión. Correcto, pero se podría reconstruir con la regla RENAPO (Ñ → X) en lugar de mandarlo a revisión.

## Limitaciones

- 58 credenciales dan intervalos anchos: un campo con 0 errores de 58 está, con 95 % de confianza, por arriba de
  ~94 % de exactitud real; "100 % en `alta`" sobre 628 campos acota el error por debajo de ~0.5 %.
- La verdad de terreno la capturó otro modelo, no una persona. Se acotó el riesgo con los dígitos verificadores
  (CURP, MRZ) y revisando las diferencias ampliadas, pero un error compartido por ambos modelos en un campo sin
  verificador (domicilio) no se detectaría.
- Es la versión 7 del pipeline (19-oct-2026). Repetir con `scripts.evaluar comparar` tras cada cambio del
  pipeline: `lote/verdad.csv` ya queda capturado y marcado como verificado.
