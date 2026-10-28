# Prompt para implementar el cliente de la API OCR de credencial INE

Copia todo el bloque en un asistente de IA y cambia el lenguaje y framework en la primera línea.

```text
Actúa como desarrollador senior. Implementa en [LENGUAJE / FRAMEWORK] un cliente para la API
"OCR Credencial para Votar INE" siguiendo EXACTAMENTE este contrato. Entrega: módulo cliente,
modelos tipados de la respuesta, manejo de errores y reintentos, y un ejemplo de uso.

== QUÉ HACE LA API ==
Recibe un PDF con la credencial para votar del INE (México), anverso y reverso, y devuelve en JSON
los datos impresos (frente), la zona MRZ del reverso (atras.mrz), las comprobaciones cruzadas entre
ambos y las 7 validaciones del plan de atención (validacion_plan). Rechaza cualquier otro documento.

== PETICIÓN ==
POST http://172.10.30.17:18473/ine
Header obligatorio: X-API-Key: <clave>   (leerla de una variable de entorno OCR_INE_API_KEY;
                                           nunca escribirla en el código)
Body: multipart/form-data con UN archivo PDF en el campo "file" (content-type application/pdf).
Campos de texto OPCIONALES en el mismo formulario (datos capturados en la solicitud), para que la
API responda las validaciones de coincidencia del plan de atención:
  nombre (completo, cualquier orden)  -- o bien: apellido_paterno, apellido_materno, nombres
  curp (18 caracteres) · fecha_nacimiento (AAAA-MM-DD o DD/MM/AAAA)
Solo PDF (otro formato responde 400). Tamaño máximo 25 MB (413). Máximo 15 páginas (224).
Anverso y reverso en el mismo archivo. Timeout del cliente: 120 s. La API tarda 10-15 s por
credencial y procesa 2 a la vez; las demás esperan turno. En cargas masivas, enviar como máximo
2 peticiones en paralelo.
GET /health (sin API key) devuelve {"status":"ok"|"degradado", "vlm": bool, ...} para monitoreo.

== RESPUESTA: SIEMPRE ESTE SOBRE JSON ==
{
  "codigo": int,          // decide con ESTE campo, no con el estado HTTP
  "estado": str,          // nombre fijo del código
  "mensaje": str,         // texto para humanos
  "id": str,              // UUID del resultado guardado (solo 200-212)
  "frente": {...},        // datos impresos en el anverso        (solo 200, 210, 211, 212)
  "atras": {"mrz": {...}},// MRZ del reverso                     (solo 200-212)
  "validacion": {"ok": bool, "completo": bool, "coincidencias": [str],
                 "discrepancias": [str], "sin_verificar": [str]},   // solo 200-212
  "avisos": [str],        // solo 200-212
  "clasificacion": {"categoria": "ine"|"otro"|"hoja_en_blanco", "confianza": null,
                    "caracteres_leidos": int, "senales": [str], "documento_detectado": str (opcional)},
  "confianza": {"<campo>": "alta"|"media"|"baja"},   // solo 200-212
  "campos_reconstruidos": [str],                     // opcional
  "meta": {"tiempo_total_ms": int, "tiempos_ms": {...}, "modelo_vlm": str, "paginas": int,
           "recorte_anverso": "ancla"|"localizador"|"pagina_completa", "recorte_reverso": str},
  "validacion_plan": {    // códigos 200-221: plan de atención, sección INE
    "cumple": bool,       // true solo si las 7 validaciones pasan
    "mensaje": str,       // "Documento válido: ..." o "Revisar documento y motivo de rechazo"
    "motivos_rechazo": [str],
    "regla_vigencia": "anio"|"fecha",
    "validaciones": [{"id": str, "pregunta": str, "resultado": "SI"|"NO"|"NO_VERIFICADO",
                      "ok": bool, "detalle": str}]   // 7 elementos, en este orden:
      // hoja_en_blanco (pasa con NO), imagen_ilegible (pasa con NO), es_ine, vigente,
      // nombre_coincide, curp_coincide, fecha_nacimiento_coincide
  }
}
REGLA: si un dato no se pudo leer, la LLAVE NO APARECE (no viene null). Todos los campos de
"frente", "atras.mrz", "confianza" y "meta" deben modelarse como OPCIONALES.

== CAMPOS DE "frente" ==
apellido_paterno (str) · apellido_materno (str) · nombre (str, nombres de pila)
domicilio {calle_numero, colonia, codigo_postal, municipio_delegacion, estado}  (str cada uno)
clave_elector (str, 18 chars) · curp (str, 18 chars) · fecha_nacimiento ("AAAA-MM-DD")
sexo ("H" = hombre, "M" = mujer) · anio_registro (str "2003 02") · seccion (str, 4 dígitos)
vigencia (str, año "2030": la credencial vence el 31 de diciembre de ese año)
pais (siempre "MEXICO") · formato_credencial ("anterior" | "nuevo" | null)
Solo formato "anterior": estado_clave, municipio_clave, localidad_clave, anio_emision.
Solo formato "nuevo": numero_emision y no_impresos (lista de campos que ese modelo no imprime).

== CAMPOS DE "atras.mrz" ==
lineas ([str] x3, 30 caracteres cada una), tipo_documento ("ID"), pais_emisor ("MEX"),
cic (str 9 dígitos), folio (= cic), seccion, identificador_ciudadano (str 9 dígitos),
nacionalidad ("MEX"), fecha_nacimiento ("AAAA-MM-DD"), sexo ("H"|"M"), vigencia ("AAAA-12-31"),
apellido_paterno, apellido_materno, nombres,
digitos_control {cic: bool, fecha_nacimiento: bool, vigencia: bool, compuesto: bool},
integra (bool: todos los dígitos cuadran), origen ("texto" | "entidad").
Si no se pudo leer la MRZ el bloque es solo {"error": str, "texto_crudo": str (opcional)}.

== CÓDIGOS Y ACCIÓN ==
Viajan con HTTP 200 (la petición fue válida):
 200 PROCESADO                    -> ACEPTAR: usar frente directo.
 211 PROCESADO_CON_AVISOS         -> ACEPTAR_CON_REVISION: mostrar "avisos".
 212 PROCESADO_INCOMPLETO         -> ACEPTAR_CON_REVISION: mostrar validacion.sin_verificar.
 210 PROCESADO_CON_DISCREPANCIAS  -> REVISION_OBLIGATORIA: mostrar validacion.discrepancias;
                                     no usar los datos sin revisión humana.
 220 NO_ES_INE                    -> DOCUMENTO_INCORRECTO (sin datos).
 221 HOJA_EN_BLANCO               -> DOCUMENTO_INCORRECTO (reescanear).
 222 ARCHIVO_CORRUPTO             -> ARCHIVO_INVALIDO.
 223 PDF_PROTEGIDO                -> ARCHIVO_INVALIDO (quitar contraseña).
 224 LIMITE_PAGINAS               -> ARCHIVO_INVALIDO (más de 15 páginas: enviar solo la credencial).
Viajan con su mismo estado HTTP:
 400 ARCHIVO_NO_SOPORTADO, 413 ARCHIVO_DEMASIADO_GRANDE, 422 PETICION_INVALIDA -> ARCHIVO_INVALIDO
 401 NO_AUTORIZADO, 404 NO_ENCONTRADO, 405 METODO_NO_PERMITIDO               -> ERROR_CONFIGURACION
 500 ERROR_INTERNO, 503 SERVICIO_NO_DISPONIBLE                               -> REINTENTAR
Cualquier código desconocido -> tratar como REINTENTAR y registrarlo.

== REINTENTOS ==
Reintentar SOLO 500, 503, errores de conexión y timeouts: máximo 3 intentos con espera
de 2 s, 4 s y 8 s. Nunca reintentar 4xx ni 210-224.

== DECISIÓN CON EL PLAN DE ATENCIÓN ==
Si existe "validacion_plan": cumple=true -> APROBADO; cumple=false -> REVISAR y mostrar
"motivos_rechazo". Si no existe (errores técnicos), decidir con la tabla de códigos de arriba.
"codigo" habla de la calidad de la extracción; "validacion_plan.cumple" de si el documento
coincide con la solicitud y está vigente. Son independientes. Sin datos de la solicitud, las
validaciones de coincidencia quedan NO_VERIFICADO y cumple es false.

== REQUISITOS DE IMPLEMENTACIÓN ==
1. Función principal: extraer(archivo_pdf, solicitud opcional) -> resultado tipado con: codigo,
   estado, mensaje, accion (enum de las acciones de arriba), cumple_plan, motivos_rechazo,
   frente (opcional), avisos, discrepancias, sin_verificar y el JSON crudo completo.
2. Leer SIEMPRE el JSON aunque el HTTP sea 4xx/5xx (también trae el sobre).
3. Si la respuesta no es JSON (caída del servidor, proxy), devolver accion=REINTENTAR.
4. No registrar en logs nombres, CURP, clave de elector, domicilio ni el contenido de "frente"
   (datos personales).
5. API key, URL y timeout configurables por variables de entorno.
6. Incluir pruebas unitarias con respuestas simuladas para 200, 210, 212, 220, 401 y 500,
   y para validacion_plan con cumple=true y cumple=false.

== EJEMPLO DE RESPUESTA 200 (datos ficticios, extracto) ==
{"codigo":200,"estado":"PROCESADO","mensaje":"El documento se procesó y todas las comprobaciones cuadraron.",
 "id":"4f1c2d7e-9a3b-4c5d-8e6f-0a1b2c3d4e5f",
 "frente":{"apellido_paterno":"PEREZ","apellido_materno":"LOPEZ","nombre":"JUAN CARLOS",
  "domicilio":{"calle_numero":"C REFORMA 10","colonia":"COL CENTRO","codigo_postal":"06000",
               "municipio_delegacion":"CUAUHTEMOC","estado":"CDMX"},
  "clave_elector":"PRLPJN85031209H400","curp":"PELJ850312HDFRPN05","fecha_nacimiento":"1985-03-12",
  "sexo":"H","anio_registro":"2003 02","seccion":"1234","vigencia":"2030","pais":"MEXICO",
  "formato_credencial":"nuevo","no_impresos":["estado_clave","municipio_clave","localidad_clave","anio_emision"]},
 "atras":{"mrz":{"cic":"123456789","identificador_ciudadano":"567890123","vigencia":"2030-12-31",
  "digitos_control":{"cic":true,"fecha_nacimiento":true,"vigencia":true,"compuesto":true},"integra":true}},
 "validacion":{"ok":true,"completo":true,"coincidencias":["MRZ (dígitos de control)"],
  "discrepancias":[],"sin_verificar":[]},
 "avisos":[],"clasificacion":{"categoria":"ine","confianza":null,"caracteres_leidos":640},
 "confianza":{"nombre":"alta","curp":"alta","clave_elector":"alta","cic":"alta"},
 "validacion_plan":{"cumple":true,"mensaje":"Documento válido: cumple con todas las validaciones.",
  "motivos_rechazo":[],"regla_vigencia":"anio","validaciones":[]}}
```
