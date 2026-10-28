# -*- coding: utf-8 -*-
"""Genera la documentación PDF de integración de la API OCR de credencial INE.

    python docs/generar_documentacion.py . docs/Documentacion_API_OCR_INE.pdf

Misma plantilla que OCR-INM-DocumentAI/docai-ocr/docs/generar_documentacion.py. Los ejemplos de
respuesta salen de docs/ejemplos_respuesta.json (generados con el código real y datos ficticios).
"""
import json
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Preformatted, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

PROYECTO = Path(sys.argv[1])
SALIDA = Path(sys.argv[2])
EJ = json.loads((PROYECTO / "docs" / "ejemplos_respuesta.json").read_text(encoding="utf-8"))
PROMPT = (PROYECTO / "docs" / "PROMPT_IA.md").read_text(encoding="utf-8").split("```text", 1)[1].rsplit("```", 1)[0].strip("\n")

URL = "http://172.10.30.17:18473"
VERSION_DOC = "1.0 · 27 de octubre de 2026"
W, Hh = letter
AZUL = colors.HexColor("#1F3A5F")
AZUL2 = colors.HexColor("#2F5D8C")
CLARO = colors.HexColor("#E8EEF6")
GRIS = colors.HexColor("#5F6368")
LINEA = colors.HexColor("#D0D7E2")
CODIGO_BG = colors.HexColor("#F4F6F9")
VERDE = colors.HexColor("#2E7D32")
AMBAR = colors.HexColor("#B26A00")
ROJO = colors.HexColor("#B3261E")

ss = getSampleStyleSheet()
S = {
    "h1": ParagraphStyle("h1", keepWithNext=1, fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=AZUL, spaceBefore=4, spaceAfter=10),
    "h2": ParagraphStyle("h2", keepWithNext=1, fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=AZUL2, spaceBefore=12, spaceAfter=6),
    "h3": ParagraphStyle("h3", keepWithNext=1, fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=colors.black, spaceBefore=8, spaceAfter=4),
    "p": ParagraphStyle("p", fontName="Helvetica", fontSize=9.6, leading=14, spaceAfter=6),
    "li": ParagraphStyle("li", fontName="Helvetica", fontSize=9.6, leading=14, leftIndent=14, bulletIndent=4, spaceAfter=3),
    "cel": ParagraphStyle("cel", fontName="Helvetica", fontSize=8.4, leading=11),
    "celb": ParagraphStyle("celb", fontName="Helvetica-Bold", fontSize=8.4, leading=11),
    "celw": ParagraphStyle("celw", fontName="Helvetica-Bold", fontSize=8.4, leading=11, textColor=colors.white),
    "celm": ParagraphStyle("celm", fontName="Courier", fontSize=8, leading=11),
    "code": ParagraphStyle("code", fontName="Courier", fontSize=7.7, leading=10.2),
    "nota": ParagraphStyle("nota", fontName="Helvetica", fontSize=9, leading=13),
    "toc0": ParagraphStyle("toc0", fontName="Helvetica-Bold", fontSize=10.2, leading=15, leftIndent=0),
    "toc1": ParagraphStyle("toc1", fontName="Helvetica", fontSize=9.0, leading=12.5, leftIndent=16, textColor=GRIS),
}


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def P(t, st="p"):
    return Paragraph(t, S[st])


def H1(t):
    p = Paragraph(t, S["h1"]); p._toc = (0, t); return p


def H2(t):
    p = Paragraph(t, S["h2"]); p._toc = (1, t); return p


def H3(t):
    return Paragraph(t, S["h3"])


def LI(items, st="li"):
    return [Paragraph(t, S[st], bulletText="•") for t in items]


def m(t):
    """texto monoespaciado en línea"""
    return f'<font name="Courier" size="8.6">{esc(t)}</font>'


def _envolver(linea, ancho=104):
    """Parte líneas largas en el último espacio, con sangría de continuación."""
    if len(linea) <= ancho:
        return [linea]
    sangria = " " * (len(linea) - len(linea.lstrip()) + 4)
    out, resto = [], linea
    while len(resto) > ancho:
        corte = resto.rfind(" ", 0, ancho)
        if corte <= len(sangria):
            corte = ancho
        out.append(resto[:corte])
        resto = sangria + resto[corte:].lstrip()
    out.append(resto)
    return out


def code(texto, titulo=None, lineas_por_bloque=14):
    lineas = [x for l in texto.rstrip("\n").split("\n") for x in _envolver(l)]
    filas = []
    if titulo:
        filas.append([Paragraph(titulo, S["celw"])])
    for i in range(0, len(lineas), lineas_por_bloque):
        filas.append([Preformatted("\n".join(lineas[i:i + lineas_por_bloque]), S["code"])])
    t = Table(filas, colWidths=[17.2 * cm])
    est = [("BACKGROUND", (0, 0), (-1, -1), CODIGO_BG), ("BOX", (0, 0), (-1, -1), 0.6, LINEA),
           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
           ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]
    if titulo:
        est += [("BACKGROUND", (0, 0), (-1, 0), AZUL2), ("TOPPADDING", (0, 0), (-1, 0), 4), ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                ("NOSPLIT", (0, 0), (0, 1))]
    est += [("TOPPADDING", (0, 1 if titulo else 0), (-1, 1 if titulo else 0), 6),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]
    t.setStyle(TableStyle(est))
    return [t, Spacer(1, 8)]


def tabla(encabezado, filas, anchos, mono_cols=(), zebra=True):
    data = [[Paragraph(h, S["celw"]) for h in encabezado]]
    for f in filas:
        data.append([Paragraph(str(c), S["celm"] if i in mono_cols else S["cel"]) for i, c in enumerate(f)])
    t = Table(data, colWidths=[a * cm for a in anchos], repeatRows=1)
    est = [("BACKGROUND", (0, 0), (-1, 0), AZUL), ("VALIGN", (0, 0), (-1, -1), "TOP"),
           ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINEA), ("BOX", (0, 0), (-1, -1), 0.5, LINEA),
           ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
           ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
    if zebra:
        for i in range(2, len(data), 2):
            est.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#F7F9FC")))
    t.setStyle(TableStyle(est))
    return [t, Spacer(1, 8)]


def caja(texto, color=AZUL2, fondo=CLARO, titulo=None):
    contenido = (f"<b>{titulo}</b><br/>" if titulo else "") + texto
    t = Table([[Paragraph(contenido, S["nota"])]], colWidths=[17.2 * cm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fondo), ("LINEBEFORE", (0, 0), (0, -1), 3, color),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    return [t, Spacer(1, 8)]


def js(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ documento

class Doc(BaseDocTemplate):
    def __init__(self, fn, **kw):
        super().__init__(fn, **kw)
        marco = Frame(2.1 * cm, 2.0 * cm, W - 4.2 * cm, Hh - 4.1 * cm, id="n")
        self.addPageTemplates([PageTemplate("portada", [Frame(0, 0, W, Hh, id="p")], onPage=portada),
                               PageTemplate("normal", [marco], onPage=pagina)])

    def afterFlowable(self, f):
        if hasattr(f, "_toc"):
            nivel, texto = f._toc
            clave = f"s{id(f)}"
            self.canv.bookmarkPage(clave)
            self.canv.addOutlineEntry(texto, clave, level=nivel, closed=nivel > 0)
            self.notify("TOCEntry", (nivel, texto, self.page, clave))


def portada(c, d):
    c.saveState()
    c.setFillColor(AZUL); c.rect(0, Hh - 9.5 * cm, W, 9.5 * cm, stroke=0, fill=1)
    c.setFillColor(AZUL2); c.rect(0, Hh - 9.8 * cm, W, 0.3 * cm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica", 11); c.drawString(2.4 * cm, Hh - 3.0 * cm, "GUÍA DE INTEGRACIÓN PARA DESARROLLADORES")
    c.setFont("Helvetica-Bold", 28); c.drawString(2.4 * cm, Hh - 4.6 * cm, "API OCR de Credencial")
    c.drawString(2.4 * cm, Hh - 5.8 * cm, "para Votar INE")
    c.setFont("Helvetica", 12.5)
    c.drawString(2.4 * cm, Hh - 7.2 * cm, "Extracción de datos de la credencial INE con PaddleOCR y Qwen3-VL")
    c.drawString(2.4 * cm, Hh - 7.8 * cm, "(open source, 100 % local) y validación MRZ, CURP y clave de elector")
    c.setFillColor(colors.black)
    y = Hh - 12 * cm
    filas = [("Endpoint principal", f"POST {URL}/ine"), ("Autenticación", "Header X-API-Key"),
             ("Formato de respuesta", "JSON (sobre homologado «API Santander IA»)"),
             ("Versión de la API", "1.0.0"), ("Versión del documento", VERSION_DOC),
             ("Elaborado por", "Alexis Eduardo Juárez Jaimes")]
    for k, v in filas:
        c.setFont("Helvetica-Bold", 10); c.setFillColor(GRIS); c.drawString(2.4 * cm, y, k.upper())
        c.setFont("Helvetica", 11.5); c.setFillColor(colors.black); c.drawString(7.4 * cm, y, v)
        c.setStrokeColor(LINEA); c.line(2.4 * cm, y - 0.35 * cm, W - 2.4 * cm, y - 0.35 * cm)
        y -= 1.05 * cm
    c.setFont("Helvetica", 8.5); c.setFillColor(GRIS)
    c.drawString(2.4 * cm, 2.0 * cm, "Uso interno. Contiene la especificación técnica del servicio; no contiene datos de clientes.")
    c.restoreState()


def pagina(c, d):
    c.saveState()
    c.setStrokeColor(LINEA); c.line(2.1 * cm, Hh - 1.45 * cm, W - 2.1 * cm, Hh - 1.45 * cm)
    c.setFont("Helvetica", 7.8); c.setFillColor(GRIS)
    c.drawString(2.1 * cm, Hh - 1.25 * cm, "API OCR de Credencial para Votar INE · Guía de integración")
    c.drawRightString(W - 2.1 * cm, Hh - 1.25 * cm, "v1.0 · 27/10/2026")
    c.drawString(2.1 * cm, 1.2 * cm, "Uso interno")
    c.drawRightString(W - 2.1 * cm, 1.2 * cm, f"Página {d.page}")
    c.restoreState()


st = []
st += [NextPageTemplate("normal"), PageBreak()]

# ------------------------------------------------------------------ índice
st.append(Paragraph("Contenido", S["h1"]))
toc = TableOfContents()
toc.levelStyles = [S["toc0"], S["toc1"]]
toc.dotsMinLevel = 0
toc.tableStyle = TableStyle([("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)])
st += [toc, PageBreak()]

# ------------------------------------------------------------------ 1
st.append(H1("1. Qué hace esta API"))
st.append(P("La API recibe un PDF escaneado con la <b>credencial para votar del Instituto Nacional Electoral (INE)</b>, "
            "anverso y reverso, y devuelve en JSON los datos impresos, la zona MRZ del reverso, las comprobaciones "
            "cruzadas entre todas las fuentes y las validaciones del plan de atención. Todo el procesamiento es "
            "<b>open source y corre en el servidor de la empresa</b>: PaddleOCR para el texto y el modelo de visión "
            "Qwen3-VL 8B para leer el anverso. Ningún documento sale de la red interna."))
st.append(H2("1.1 Cómo funciona por dentro"))
st += tabla(["Paso", "Qué ocurre"], [
    ("1. Recepción", "Valida la API key, que el archivo sea PDF, su tamaño (25 MB) y páginas (15), y que se pueda abrir."),
    ("2. Render y OCR", "Convierte las páginas a imagen (250 dpi) y PaddleOCR lee todo el texto. Con ese texto ubica el "
                        "anverso (encabezado «INSTITUTO NACIONAL ELECTORAL») y el reverso (la MRZ), probando la página "
                        "girada 90°, 180° y 270° si hace falta."),
    ("3. Filtro", "Antes de usar la GPU descarta hojas en blanco (221) y documentos que claramente son otra cosa: licencia, "
                  "pasaporte, RFC, IMSS, tarjeta INM, credencial de otro país (220)."),
    ("4. Recortes", "Recorta y endereza cada cara. Si el OCR no encontró las anclas (credencial inclinada, chica o muy tenue) "
                    "el modelo de visión localiza la credencial y se repite el OCR sobre ese recorte."),
    ("5. Lectura del anverso", "Qwen3-VL lee el anverso y devuelve un JSON con esquema fijo (nombre, domicilio, CURP, clave "
                               "de elector, fechas, sección, vigencia...)."),
    ("6. MRZ del reverso", "Lee las 3 líneas de la MRZ con el OCR y verifica sus 4 dígitos de control (CIC, fecha de "
                           "nacimiento, vigencia y compuesto). Si no cuadran, también la pide al modelo de visión."),
    ("7. Fusión y validación", "Cruza cada dato entre sus fuentes (OCR, modelo de visión, MRZ, CURP y clave de elector), "
                               "corrige confusiones típicas del OCR (O/0, I/1, Ñ perdida), reconstruye CURP o clave tapadas "
                               "por un sello y asigna una confianza por campo."),
    ("8. Plan de atención", "Si se envían los datos capturados en la solicitud, responde las 7 validaciones del plan de "
                            "atención (sección INE) con SÍ/NO y los motivos de rechazo."),
    ("9. Respuesta", "Entrega el JSON con un código que indica la calidad de la extracción, guarda el resultado en la base "
                     "de datos (nunca la imagen) y devuelve su id."),
], [3.4, 13.8])
st.append(H2("1.2 Fuentes que se cruzan para cada dato"))
st.append(P("La precisión viene de que casi todo dato tiene al menos dos fuentes independientes o un dígito verificador:"))
st += tabla(["Dato", "Fuentes"], [
    ("Fecha de nacimiento", "MRZ (dígito verificador), CURP, clave de elector, modelo de visión, OCR"),
    ("Sexo", "MRZ, CURP, clave de elector, modelo de visión"),
    ("CURP / clave de elector", "Modelo de visión vs OCR, estructura y dígito verificador de la CURP, fecha y sexo de la MRZ, "
                                "iniciales y consonantes del nombre"),
    ("Nombre", "Modelo de visión vs OCR vs línea 3 de la MRZ, iniciales de CURP y clave de elector"),
    ("Sección, vigencia, año de registro", "Modelo de visión vs MRZ"),
    ("CIC, identificador del ciudadano", "MRZ con dígito verificador"),
    ("Domicilio", "Modelo de visión vs OCR, código postal y estado reconocidos"),
], [4.6, 12.6])
st.append(H2("1.3 Documentos soportados"))
st += LI(["<b>Credencial para votar del INE</b>, formato anterior (modelos E/F, con estado, municipio y localidad "
          "impresos) y formato nuevo (modelos G/H, con MRZ y QR).",
          "Fotocopias en blanco y negro, con sellos de cotejo, credenciales giradas o chicas en la hoja.",
          "<b>No soportados:</b> licencias, pasaportes, tarjetas INM, cédulas, actas y cualquier otro documento: "
          "devuelven el código <b>220</b>. Una INE de otro país o un formato sin MRZ ni clave válida también se rechaza."])
st += caja("La credencial debe venir con <b>anverso y reverso en el mismo PDF</b>. El reverso contiene la MRZ, que es "
           "la base de la validación: sin reverso los datos se entregan, pero con menos comprobaciones (códigos 211 o 212).",
           titulo="Importante")

# ------------------------------------------------------------------ 2
st.append(H1("2. Inicio rápido"))
st += LI(["Solicita la <b>API key</b> al responsable del servicio. Guárdala en una variable de entorno o gestor de "
          "secretos, nunca en el código.",
          f"Envía el PDF por <b>POST multipart/form-data</b> en el campo {m('file')} a {m('/ine')} "
          f"con el header {m('X-API-Key')}.",
          "Para validar contra la solicitud, agrega en el mismo formulario nombre, curp y fecha_nacimiento "
          "capturados (sección 4.1).",
          f"Lee {m('validacion_plan.cumple')} para saber si el documento pasa el plan de atención, y {m('codigo')} "
          "para la calidad de la extracción (secciones 5.6 y 6)."])
st += code(f'''curl -X POST "{URL}/ine" \\
  -H "X-API-Key: $OCR_INE_API_KEY" \\
  -F "file=@credencial.pdf;type=application/pdf" \\
  -F "nombre=Juan Carlos Perez Lopez" \\
  -F "curp=PELJ850312HDFRPN05" \\
  -F "fecha_nacimiento=1985-03-12"''', "Ejemplo (curl)")

# ------------------------------------------------------------------ 3
st.append(H1("3. Conexión"))
st += tabla(["Concepto", "Valor"], [
    ("URL base", m(URL)),
    ("Red", "Red interna de la empresa (servidor srviatwo). El servicio no está expuesto a internet."),
    ("Protocolo", "HTTP (sin TLS). Ver sección 10, Seguridad."),
    ("Autenticación", f"Header {m('X-API-Key')} con la clave del servicio. Sin ella se responde 401."),
    ("Formato de respuesta", "JSON UTF-8, siempre con el mismo sobre (sección 5)."),
    ("Documentación interactiva", f"Swagger en {m(URL + '/docs')} y especificación OpenAPI en {m(URL + '/openapi.json')}"),
], [4.2, 13])
st.append(H2("3.1 Endpoints"))
st += tabla(["Método", "Ruta", "Auth", "Uso"], [
    ("POST", m("/ine"), "Sí", "<b>Endpoint principal.</b> Usar este en todas las integraciones."),
    ("GET", m("/v1/ine/extracciones"), "Sí", "Historial de resultados guardados, con filtros (estado, curp, texto). Formato interno."),
    ("GET", m("/v1/ine/extracciones/{id}"), "Sí", "Resultado completo guardado de una extracción."),
    ("PUT", m("/v1/ine/extracciones/{id}/revision"), "Sí", "Guarda la corrección hecha por una persona y marca la extracción como revisada."),
    ("GET", m("/health"), "No", "Estado del servicio, del modelo de visión y de la base de datos, para monitoreo."),
], [1.6, 5.2, 1.3, 9.1])
st.append(H2("3.2 Límites y tiempos"))
st += tabla(["Concepto", "Valor", "Qué pasa si se excede"], [
    ("Tamaño máximo", "25 MB por archivo", "Código 413"),
    ("Tipos aceptados", "Solo PDF (se verifica el contenido, no la extensión)", "Código 400"),
    ("Archivos por petición", "1", "Solo se procesa el campo file"),
    ("Páginas", "Hasta 15; se analizan las primeras 4 (la credencial viene al principio)", "Código 224"),
    ("Tiempo de respuesta", "10 a 15 segundos por credencial (mediana 9 s en lote; p90 16 s)", "-"),
    ("Peticiones simultáneas", "2 se procesan a la vez (una GPU de 8 GB compartida)", "Las demás esperan su turno (ver 7.3)"),
    ("Timeout recomendado en el cliente", "120 segundos", "-"),
], [4.4, 7.0, 5.8])

# ------------------------------------------------------------------ 4
st.append(H1("4. Petición"))
st += tabla(["Elemento", "Valor"], [
    ("Método y ruta", m("POST /ine")),
    ("Content-Type", m("multipart/form-data")),
    ("Header obligatorio", m("X-API-Key: <tu clave>")),
    ("Campo del archivo", f"{m('file')} (obligatorio). Si falta o tiene otro nombre: código 422."),
    ("Tipo del archivo", f"PDF. Indicar {m('application/pdf')} en la parte del archivo. La API revisa el contenido real: "
                         "un archivo que dice ser PDF y no lo es responde 222; una imagen responde 400."),
], [4.0, 13.2])
st.append(H2("4.1 Datos de la solicitud (opcionales)"))
st.append(P("Campos de texto en el mismo formulario. Con ellos la API responde las validaciones de coincidencia del "
            "plan de atención. Sin ellos la extracción funciona igual, pero esas validaciones quedan como "
            "NO_VERIFICADO y el plan no se cumple."))
st += tabla(["Campo", "Formato", "Uso"], [
    ("nombre", "Texto", "Nombre completo capturado, en cualquier orden (nombres y apellidos)."),
    ("apellido_paterno, apellido_materno, nombres", "Texto", "Alternativa a nombre, si la solicitud los captura por separado."),
    ("curp", "18 caracteres", "CURP capturada. En INE es obligatoria para cumplir el plan: si no se envía, la validación queda NO_VERIFICADO."),
    ("fecha_nacimiento", "AAAA-MM-DD", "También acepta DD/MM/AAAA."),
], [5.2, 2.6, 9.4], mono_cols=(0,))
st.append(H2("4.2 Ejemplos por lenguaje"))
st += code(f'''import os
import requests

URL = "{URL}/ine"
API_KEY = os.environ["OCR_INE_API_KEY"]

def extraer_ine(ruta_pdf: str, solicitud: dict | None = None) -> dict:
    with open(ruta_pdf, "rb") as f:
        resp = requests.post(
            URL,
            headers={{"X-API-Key": API_KEY}},
            files={{"file": (os.path.basename(ruta_pdf), f, "application/pdf")}},
            data=solicitud or {{}},   # nombre, curp, fecha_nacimiento
            timeout=120,
        )
    return resp.json()          # siempre trae codigo, estado y mensaje

r = extraer_ine("credencial.pdf", {{
    "nombre": "Juan Carlos Perez Lopez", "curp": "PELJ850312HDFRPN05", "fecha_nacimiento": "1985-03-12",
}})
plan = r.get("validacion_plan")
if plan:
    print("Cumple el plan:", plan["cumple"], "-", plan["mensaje"])
    for motivo in plan["motivos_rechazo"]:
        print("  -", motivo)
if r["codigo"] in (200, 210, 211, 212):
    print(r["frente"].get("curp"))   # usar .get(): las llaves pueden faltar''',
                "Python (requests)")
st += code(f'''import {{ readFile }} from "node:fs/promises";

const URL = "{URL}/ine";

export async function extraerIne(rutaPdf, solicitud = {{}}) {{
  const buffer = await readFile(rutaPdf);
  const form = new FormData();
  form.append("file", new Blob([buffer], {{ type: "application/pdf" }}), "credencial.pdf");
  // solicitud: {{ nombre, curp, fecha_nacimiento }} (opcionales)
  for (const [k, v] of Object.entries(solicitud)) if (v) form.append(k, v);

  const resp = await fetch(URL, {{
    method: "POST",
    headers: {{ "X-API-Key": process.env.OCR_INE_API_KEY }},
    body: form,
    signal: AbortSignal.timeout(120_000),
  }});
  return await resp.json();   // {{ codigo, estado, mensaje, frente?, validacion_plan?, ... }}
}}''', "JavaScript / Node.js 18+ (fetch nativo)")
st += code(f'''using System.Net.Http.Headers;
using System.Text.Json;

var http = new HttpClient {{ Timeout = TimeSpan.FromSeconds(120) }};
http.DefaultRequestHeaders.Add("X-API-Key", Environment.GetEnvironmentVariable("OCR_INE_API_KEY"));

using var form = new MultipartFormDataContent();
var archivo = new ByteArrayContent(await File.ReadAllBytesAsync("credencial.pdf"));
archivo.Headers.ContentType = new MediaTypeHeaderValue("application/pdf");
form.Add(archivo, "file", "credencial.pdf");
form.Add(new StringContent("Juan Carlos Perez Lopez"), "nombre");   // datos de la solicitud (opcionales)
form.Add(new StringContent("PELJ850312HDFRPN05"), "curp");
form.Add(new StringContent("1985-03-12"), "fecha_nacimiento");

var resp = await http.PostAsync("{URL}/ine", form);
using var json = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
int codigo = json.RootElement.GetProperty("codigo").GetInt32();
if (json.RootElement.TryGetProperty("validacion_plan", out var plan))
    Console.WriteLine($"Cumple: {{plan.GetProperty("cumple").GetBoolean()}}");
if (codigo is 200 or 210 or 211 or 212)
{{
    var frente = json.RootElement.GetProperty("frente");
    if (frente.TryGetProperty("curp", out var curp)) Console.WriteLine(curp.GetString());
}}''', "C# (.NET 6+)")
st += code(f'''<?php
$ch = curl_init("{URL}/ine");
curl_setopt_array($ch, [
    CURLOPT_POST           => true,
    CURLOPT_HTTPHEADER     => ["X-API-Key: " . getenv("OCR_INE_API_KEY")],
    CURLOPT_POSTFIELDS     => [
        "file"             => new CURLFile("credencial.pdf", "application/pdf", "credencial.pdf"),
        "nombre"           => "Juan Carlos Perez Lopez",   // datos de la solicitud (opcionales)
        "curp"             => "PELJ850312HDFRPN05",
        "fecha_nacimiento" => "1985-03-12",
    ],
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT        => 120,
]);
$body = json_decode(curl_exec($ch), true);
curl_close($ch);

$cumple = $body["validacion_plan"]["cumple"] ?? false;
if (in_array($body["codigo"], [200, 210, 211, 212])) {{
    $curp = $body["frente"]["curp"] ?? null;   // la llave puede no venir
}}''', "PHP (cURL)")

# ------------------------------------------------------------------ 5
st.append(H1("5. Estructura de la respuesta"))
st.append(P("Todas las respuestas, exitosas o de error, comparten el mismo sobre (formato homologado «API Santander IA», "
            "el mismo que la API de formas migratorias). Los bloques de datos solo aparecen cuando aplican. "
            "<b>Regla general: si un dato no se pudo leer, la llave no aparece</b> (no viene con "
            f"{m('null')} ni vacía). Accede a los campos de forma segura."))
st += code('''{
  "codigo": 200,                 // número: qué pasó (sección 6). Úsalo para decidir.
  "estado": "PROCESADO",         // texto fijo equivalente al código
  "mensaje": "...",              // texto para mostrar a una persona; puede cambiar
  "id": "4f1c...",               // id del resultado guardado           (solo códigos 200-212)
  "frente": { ... },             // datos impresos en el anverso        (solo códigos 200-212)
  "atras": { "mrz": { ... } },   // lectura y validación de la MRZ      (solo códigos 200-212)
  "validacion": { ... },         // comprobaciones cruzadas             (solo códigos 200-212)
  "avisos": [ ... ],             // puntos a revisar por una persona    (solo códigos 200-212)
  "clasificacion": { ... },      // qué documento se detectó            (códigos 200-221)
  "confianza": { ... },          // nivel de confianza por campo        (solo códigos 200-212)
  "campos_reconstruidos": [...], // campos derivados de otros datos     (opcional)
  "meta": { ... },               // tiempos, páginas, modelo            (solo códigos 200-212)
  "validacion_plan": { ... }     // las 7 validaciones del plan         (códigos 200-221)
}''', "Sobre de la respuesta")
st.append(H2("5.1 Bloque frente"))
st += tabla(["Campo", "Tipo", "Ejemplo", "Descripción"], [
    ("apellido_paterno", "texto", "PEREZ", "En mayúsculas, como está impreso."),
    ("apellido_materno", "texto", "LOPEZ", "Puede faltar si la credencial no lo trae."),
    ("nombre", "texto", "JUAN CARLOS", "Nombres de pila."),
    ("domicilio", "objeto", "{...}", "calle_numero, colonia, codigo_postal, municipio_delegacion y estado "
                                     "(abreviatura impresa: CDMX, JAL, MEX...)."),
    ("clave_elector", "texto", "PRLPJN85031209H400", "18 caracteres: 6 letras del nombre, fecha, estado, sexo y 3 dígitos."),
    ("curp", "texto", "PELJ850312HDFRPN05", "18 caracteres, con dígito verificador."),
    ("fecha_nacimiento", "fecha", "1985-03-12", "Formato AAAA-MM-DD."),
    ("sexo", "texto", "H", "<b>H = hombre, M = mujer</b>."),
    ("anio_registro", "texto", "2003 02", "Año de registro y sufijo, como está impreso."),
    ("seccion", "texto", "1234", "Sección electoral (4 dígitos)."),
    ("vigencia", "texto", "2030", "Año de vigencia. <b>La credencial vence el 31 de diciembre de ese año.</b>"),
    ("pais", "texto", "MEXICO", "Siempre MEXICO."),
    ("formato_credencial", "texto", "nuevo", "anterior (modelos E/F) · nuevo (modelos G/H) · null si no se determinó."),
    ("estado_clave, municipio_clave, localidad_clave, anio_emision", "texto", "09, 012, 0001, 2018", "Solo formato anterior."),
    ("numero_emision, no_impresos", "texto / lista", "01, [...]", "Solo formato nuevo. no_impresos enumera los campos que ese modelo no trae."),
], [4.0, 1.6, 3.6, 8.0], mono_cols=(0, 2))
st.append(H2("5.2 Bloque atras.mrz"))
st.append(P("La MRZ son las tres líneas de 30 caracteres al pie del reverso (formato ICAO TD1). Sus números y fechas "
            "traen dígitos de control, por eso es la fuente más confiable. Cuando no se pudo leer, el bloque solo trae "
            f"{m('error')} (y a veces {m('texto_crudo')})."))
st += tabla(["Campo", "Tipo", "Descripción"], [
    ("lineas", "lista", "Las 3 líneas tal como se leyeron."),
    ("tipo_documento / pais_emisor", "texto", "ID / MEX."),
    ("cic", "texto", "Código de Identificación de Credencial, 9 dígitos, con dígito verificador. folio es el mismo valor."),
    ("seccion", "texto", "Sección electoral según la MRZ."),
    ("identificador_ciudadano", "texto", "9 dígitos (OCR del ciudadano) según la MRZ."),
    ("fecha_nacimiento / sexo", "fecha / texto", "AAAA-MM-DD · H o M (ya convertido a la convención mexicana)."),
    ("vigencia", "fecha", "AAAA-12-31: la fecha exacta de vencimiento."),
    ("apellido_paterno / apellido_materno / nombres", "texto", "Según la MRZ. Pueden venir recortados: la línea mide 30 caracteres."),
    ("digitos_control", "objeto", "cic, fecha_nacimiento, vigencia y compuesto: true si el dígito cuadra."),
    ("integra", "booleano", "true si los 4 dígitos de control cuadran."),
    ("origen", "texto", "texto (leída del OCR) o entidad (leída por el modelo de visión)."),
    ("error", "texto", "Solo cuando no se localizó o no se pudo leer la MRZ."),
], [4.6, 1.9, 10.7], mono_cols=(0,))
st.append(H2("5.3 Bloque validacion"))
st += tabla(["Campo", "Tipo", "Descripción"], [
    ("ok", "booleano", "true si no hay discrepancias."),
    ("completo", "booleano", "true si se pudieron correr todas las comprobaciones."),
    ("coincidencias", "lista de texto", "Comprobaciones que cuadraron."),
    ("discrepancias", "lista de texto", "Datos que se contradicen. <b>Si no está vacía, el código es 210.</b>"),
    ("sin_verificar", "lista de texto", "Comprobaciones que no se pudieron hacer (dato faltante, sin MRZ...)."),
], [3.4, 2.6, 11.2], mono_cols=(0,))
st.append(P("Comprobaciones que realiza la API: dígitos de control de la MRZ · apellidos y nombre (anverso vs MRZ) · "
            "estructura y dígito verificador de la CURP · estructura de la clave de elector · fecha de nacimiento "
            "(MRZ, CURP, clave de elector) · sexo (MRZ, CURP, clave de elector) · nombre vs CURP (iniciales y consonantes) · "
            "nombre vs clave de elector (iniciales) · clave de elector vs CURP (iniciales, fecha y sexo) · sección, año de "
            "registro y vigencia (anverso vs MRZ) · domicilio (código postal y estado)."))
st.append(H2("5.4 avisos, clasificacion, confianza y meta"))
st += tabla(["Bloque", "Contenido"], [
    ("avisos", "Lista de textos con puntos que conviene revisar a mano, aunque no sean contradicciones: credencial no "
               "vigente, un campo con confianza baja, CURP reconstruida por un sello, sección distinta de la MRZ... "
               "<b>Si no está vacía, el código es 211.</b>"),
    ("clasificacion", "categoria: ine · otro · hoja_en_blanco. caracteres_leidos: cantidad de texto detectado. "
                      "senales: señales de INE que encontró el OCR (encabezado, mrz_mexicana, CLAVE DE ELECTOR...). "
                      "documento_detectado: solo en 220 cuando se reconoce el otro documento (pasaporte, licencia...). "
                      "confianza: siempre null (se conserva por homologación)."),
    ("confianza", "Nivel por campo (nombre, curp, clave_elector, fecha_nacimiento, sexo, domicilio, seccion, "
                  "anio_registro, vigencia, cic, identificador_ciudadano): <b>alta</b> = dos fuentes independientes "
                  "coinciden o lo respalda un dígito verificador · <b>media</b> = una sola fuente con estructura válida · "
                  "<b>baja</b> = desacuerdo o ilegible. Úsalo para resaltar campos en pantallas de revisión."),
    ("campos_reconstruidos", "Campos que no se leyeron completos (p. ej. CURP tapada por un sello) y se derivaron de "
                             "otros datos validados. Por configuración, el documento va a revisión (211)."),
    ("meta", "tiempo_total_ms, tiempos_ms por etapa, modelo_vlm, paginas, y cómo se obtuvo cada cara: recorte_anverso / "
             "recorte_reverso = ancla, localizador o pagina_completa. Informativo; no tomar decisiones con él."),
], [3.4, 13.8], mono_cols=(0,))

st.append(H2("5.5 Historial y revisión humana"))
st.append(P(f"Cada resultado se guarda en la base de datos (solo el JSON, nunca la imagen) y la respuesta trae su {m('id')}. "
            f"Con {m('GET /v1/ine/extracciones?estado=revisar')} se listan los pendientes de revisión; con "
            f"{m('PUT /v1/ine/extracciones/{id}/revision')} y el cuerpo "
            f"{m('{\"datos\": {...}, \"revisado_por\": \"usuario\"}')} se guarda la corrección hecha por una persona. "
            "Estas rutas usan el formato interno del resultado, no el sobre homologado."))

st.append(H2("5.6 Bloque validacion_plan (plan de atención)"))
st.append(P("Responde las 7 validaciones del <b>Plan de atención - Validación documental</b>, sección INE, en el mismo "
            "orden y con la misma forma que la API de formas migratorias. Es independiente de <b>codigo</b>: codigo "
            "describe la calidad de la extracción y validacion_plan dice si el documento cumple el plan."))
st += tabla(["Campo", "Tipo", "Descripción"], [
    ("cumple", "booleano", "true solo si las 7 validaciones pasan al 100 %."),
    ("mensaje", "texto", "«Documento válido: cumple con todas las validaciones.» o «Revisar documento y motivo de rechazo»."),
    ("motivos_rechazo", "lista de texto", "Una línea por cada validación que no pasó, con su explicación."),
    ("regla_vigencia", "texto", "Regla usada para la vigencia: anio (plan de atención) o fecha."),
    ("validaciones", "lista", "Las 7 validaciones: id, pregunta, resultado, ok y detalle."),
], [3.6, 2.6, 11.0], mono_cols=(0,))
st += tabla(["#", "id", "Pregunta del plan", "Pasa cuando", "Regla"], [
    ("1", "hoja_en_blanco", "¿Es una hoja en blanco?", "NO", "Se leyó texto en el documento."),
    ("2", "imagen_ilegible", "¿La imagen es ilegible?", "NO", "SÍ si no hay texto, si faltan 2 o más datos esenciales "
                                                            "(nombre, fecha de nacimiento, CURP, clave de elector) o si falta uno y la MRZ tampoco se lee."),
    ("3", "es_ine", "¿Es una INE?", "SI", "Se identificó una credencial para votar del INE."),
    ("4", "vigente", "¿Está vigente?", "SI", "Año de vigencia igual o mayor al año actual. Se toma de la MRZ si su dígito de "
                                             "control cuadra; si no, del año impreso en el anverso."),
    ("5", "nombre_coincide", "¿El nombre coincide?", "SI", "Mismas palabras sin importar orden, acentos ni mayúsculas."),
    ("6", "curp_coincide", "¿La CURP coincide?", "SI", "Igual a la capturada. Se toleran hasta 2 caracteres que el OCR confunde "
                                                      "(O/0, I/1, S/5, B/8...) si la CURP capturada cuadra con la fecha y el sexo de la credencial. "
                                                      "En INE la CURP es obligatoria: sin ella en la solicitud queda NO_VERIFICADO."),
    ("7", "fecha_nacimiento_coincide", "¿La fecha de nacimiento coincide?", "SI", "Misma fecha (la de la credencial está verificada por la MRZ, CURP y clave)."),
], [0.6, 4.6, 3.4, 1.8, 6.8], mono_cols=(1,))
st += tabla(["resultado", "Significado", "¿Bloquea?"], [
    ("SI / NO", "Respuesta a la pregunta del plan.", "Según la columna «Pasa cuando»"),
    ("NO_VERIFICADO", "Faltan datos para responder: no se envió el dato de la solicitud, no se leyó en la credencial "
                      "o el documento no es una INE.", "Sí"),
], [3.2, 10.0, 4.0], mono_cols=(0,))
st += caja("La regla de vigencia del plan compara solo el año. Si se prefiere comparar la fecha exacta de vencimiento "
           "(31 de diciembre del año de vigencia) contra hoy, el responsable del servicio la cambia con REGLA_VIGENCIA=fecha. "
           "La «validación ante lista nominal» que menciona el plan es una consulta al INE y no la hace este servicio.",
           titulo="Vigencia y lista nominal")

# ------------------------------------------------------------------ 6
st.append(H1("6. Códigos de respuesta"))
st += caja(f"Los códigos <b>200 a 224 viajan con HTTP 200</b>, porque la petición fue correcta aunque el documento no "
           f"sirva. Decide siempre con el campo {m('codigo')} del JSON, no con el estado HTTP. Los errores 4xx y 5xx "
           f"sí viajan con su mismo estado HTTP.", color=AMBAR, fondo=colors.HexColor("#FFF6E5"), titulo="Regla de oro")
st.append(H2("6.1 Documento procesado (trae datos)"))
st += tabla(["Código", "estado", "Significado", "Qué hacer"], [
    ("200", "PROCESADO", "Todo cuadró: anverso, MRZ, CURP y clave de elector coinciden.", "<font color='#2E7D32'><b>Usar los datos directo.</b></font>"),
    ("211", "PROCESADO_CON_AVISOS", "Sin contradicciones, pero hay algo para revisar (ver avisos): credencial vencida, "
                                    "un campo con confianza baja, CURP reconstruida...", "Usar los datos; revisión rápida de avisos."),
    ("212", "PROCESADO_INCOMPLETO", "Sin contradicciones, pero alguna comprobación no se pudo hacer (ver sin_verificar): "
                                    "falta un dato o la MRZ.", "Usar los datos; revisar los campos de sin_verificar."),
    ("210", "PROCESADO_CON_DISCREPANCIAS", "Hay datos que se contradicen (ver discrepancias). Al menos un dato puede estar mal.",
     "<font color='#B3261E'><b>No usar sin revisión humana</b></font> de los campos indicados."),
], [1.4, 5.2, 6.0, 4.6], mono_cols=(0, 1))
st.append(H2("6.2 Documento evaluado pero no extraído (sin datos)"))
st += tabla(["Código", "estado", "Significado", "Qué hacer"], [
    ("220", "NO_ES_INE", "El archivo no trae una credencial para votar (licencia, pasaporte, tarjeta INM, otro).", "Pedir el documento correcto."),
    ("221", "HOJA_EN_BLANCO", "No se leyó texto (menos de 10 letras o dígitos).", "Reescanear con mejor calidad."),
    ("222", "ARCHIVO_CORRUPTO", "El archivo llegó vacío, está dañado o no es el PDF que dice ser.", "Reenviar un archivo válido."),
    ("223", "PDF_PROTEGIDO", "El PDF tiene contraseña.", "Quitar la contraseña y reenviar."),
    ("224", "LIMITE_PAGINAS", "Más de 15 páginas.", "Enviar solo las páginas de la credencial."),
], [1.4, 5.2, 6.0, 4.6], mono_cols=(0, 1))
st.append(H2("6.3 Errores (sin datos, con su estado HTTP)"))
st += tabla(["Código / HTTP", "estado", "Causa", "Qué hacer", "¿Reintentar?"], [
    ("400", "ARCHIVO_NO_SOPORTADO", "El archivo no es PDF.", "Enviar un PDF con su tipo correcto.", "No"),
    ("401", "NO_AUTORIZADO", "Falta el header X-API-Key o la clave es incorrecta.", "Revisar configuración.", "No"),
    ("404", "NO_ENCONTRADO", "Ruta inexistente o id de extracción que no existe.", "Revisar la URL.", "No"),
    ("405", "METODO_NO_PERMITIDO", "Método HTTP incorrecto (p. ej. GET a /ine).", "Usar POST.", "No"),
    ("413", "ARCHIVO_DEMASIADO_GRANDE", "Archivo mayor a 25 MB.", "Comprimir o enviar solo las páginas de la credencial.", "No"),
    ("422", "PETICION_INVALIDA", "Falta el campo file o el multipart está mal formado. El mensaje dice qué falló.", "Corregir la petición.", "No"),
    ("500", "ERROR_INTERNO", "Error no controlado de la API.", "Reportar con hora y archivo.", "Una vez"),
    ("503", "SERVICIO_NO_DISPONIBLE", "Base de datos no configurada (solo rutas de historial).", "Avisar al responsable.", "Sí, con espera"),
], [1.6, 4.6, 4.4, 4.2, 2.4], mono_cols=(0, 1))
st += caja("Si el servidor está caído o no hay red, no llegará ningún JSON: el cliente verá un error de conexión o de "
           "timeout. Trátalo como un 503 (reintentar con espera). Si el modelo de visión no responde, la API sigue "
           "solo con el OCR y lo refleja con avisos (211), no con un error.", titulo="Sin respuesta")

# ------------------------------------------------------------------ 7
st.append(H1("7. Integración recomendada"))
st.append(H2("7.1 Cómo decidir"))
st.append(P("Si integras el plan de atención, decide primero con <b>validacion_plan.cumple</b>: true significa que el "
            "documento pasó las 7 validaciones; false, que hay que revisar el documento con los motivos de rechazo. "
            "Usa <b>codigo</b> para los errores técnicos y para saber qué tan confiable es la extracción."))
st += code('''def decidir_plan(body: dict) -> str:
    plan = body.get("validacion_plan")
    if plan is None:                       # error técnico: no se evaluó el documento
        return decidir(body)
    if plan["cumple"]:
        return "APROBADO"
    return "REVISAR"                       # mostrar plan["motivos_rechazo"]''', "Decisión con el plan de atención (Python)")
st += code('''def decidir(body: dict) -> str:
    c = body["codigo"]
    if c == 200:
        return "ACEPTAR"                    # datos listos
    if c in (211, 212):
        return "ACEPTAR_CON_REVISION"       # revisar body["avisos"] / body["validacion"]["sin_verificar"]
    if c == 210:
        return "REVISION_OBLIGATORIA"       # revisar body["validacion"]["discrepancias"]
    if c in (220, 221):
        return "DOCUMENTO_INCORRECTO"       # pedir otro documento o reescaneo
    if c in (222, 223, 224, 400, 413, 422):
        return "ARCHIVO_INVALIDO"           # corregir el archivo o la petición; no reintentar igual
    if c == 401:
        return "ERROR_CONFIGURACION"        # API key
    return "REINTENTAR"                     # 500, 503 o cualquier código desconocido''', "Lógica de decisión (Python)")
st.append(H2("7.2 Reintentos"))
st += LI(["<b>Reintentar solo 500, 503 y errores de conexión o timeout</b>, con espera creciente: 2 s, 4 s y 8 s "
          "(máximo 3 intentos).",
          "<b>No reintentar</b> 4xx ni los códigos 210 a 224: el resultado no cambiará enviando el mismo archivo.",
          "El modelo de visión corre con semilla fija, así que el mismo archivo da el mismo resultado: un reintento "
          "de 211 o 212 no lo mejora."])
st += code('''import time, requests

def extraer_con_reintentos(ruta, intentos=3):
    for i in range(intentos):
        try:
            body = extraer_ine(ruta)                   # función de la sección 4.2
            if body["codigo"] not in (500, 503):
                return body
        except (requests.ConnectionError, requests.Timeout):
            pass
        time.sleep(2 ** (i + 1))                       # 2 s, 4 s, 8 s
    raise RuntimeError("OCR INE no disponible después de varios intentos")''', "Reintentos con espera (Python)")
st.append(H2("7.3 Concurrencia"))
st.append(P("La API procesa <b>2 credenciales a la vez</b> y el modelo de visión atiende una petición a la vez en la "
            "GPU (8 GB, compartida con las APIs de pasaporte y formas migratorias). Si llegan más, esperan su turno sin "
            "error. Medido en lote con 3 en paralelo: mediana 9 s por credencial, p90 16 s."))
st += tabla(["Peticiones enviadas a la vez", "Tiempo aproximado de la última respuesta"], [
    ("1 a 2", "10 a 15 segundos"), ("3 a 4", "20 a 30 segundos"), ("5 a 6", "30 a 45 segundos")], [7.0, 10.2])
st += LI(["<b>Recomendado:</b> en cargas masivas, que el cliente envíe como máximo 2 en paralelo; enviar más no acelera, solo hace esperar.",
          "Mantén el timeout de 120 segundos: cubre la espera de turno en cargas normales.",
          "El límite protege la GPU compartida y lo ajusta el responsable del servicio (MAX_CONCURRENT_JOBS)."])
st.append(H2("7.4 Buenas prácticas"))
st += LI(["Guarda el JSON completo junto al expediente, o conserva el id: permite auditar qué devolvió la API y por qué.",
          f"Muestra en pantallas de revisión los textos de {m('discrepancias')}, {m('avisos')} y {m('sin_verificar')}, "
          f"y resalta los campos con confianza {m('media')} o {m('baja')}.",
          "Trata cualquier código desconocido como error y regístralo: permite agregar códigos nuevos sin romper la integración.",
          "No registres en logs el contenido de frente (nombres, CURP, clave de elector, domicilio): son datos personales.",
          "No hay costo por documento (todo es open source y local); el límite es la capacidad de la GPU."])

# ------------------------------------------------------------------ 8
st.append(H1("8. Ejemplos de respuesta"))
st.append(P("Ejemplos generados por el código real de la API con <b>datos ficticios</b>."))
st.append(H2("8.1 Código 200: procesado"))
st += code(js(EJ["200"]), "200 PROCESADO (respuesta completa)")


def parcial(c):
    b = EJ[c]
    return {k: b[k] for k in ("codigo", "estado", "mensaje")} | {"frente": "{ ... igual que en 200 ... }",
                                                                "validacion": b["validacion"], "avisos": b["avisos"]}


st.append(H2("8.2 Código 210: con discrepancias"))
st.append(P("El apellido materno leído en el anverso no cuadra con las iniciales de la CURP y la clave de elector, "
            "que sí concuerdan entre sí: el dato sospechoso es el nombre. Requiere revisión."))
st += code(js(parcial("210")), "210 PROCESADO_CON_DISCREPANCIAS (extracto)")
st.append(H2("8.3 Código 211: con avisos"))
st.append(P("Todo cuadra, pero la credencial no está vigente: se entrega con aviso."))
st += code(js(parcial("211")), "211 PROCESADO_CON_AVISOS (extracto)")
st.append(H2("8.4 Sin MRZ: 211 o 212"))
st.append(P("No se localizó el reverso: las comprobaciones contra la MRZ quedan en sin_verificar y la confianza de "
            "CIC e identificador baja. Si además hay avisos el código es 211; si no, 212."))
b212 = EJ["212"]
st += code(js({k: b212[k] for k in ("codigo", "estado", "mensaje", "atras", "validacion", "avisos")}), "Sin reverso (extracto)")
st.append(H2("8.5 Código 220 y 221: sin datos"))
st += code(js(EJ["220"]), "220 NO_ES_INE")
st += code(js(EJ["221"]), "221 HOJA_EN_BLANCO")
st.append(H2("8.6 validacion_plan: cumple"))
st += code(js(EJ["200"]["validacion_plan"]), "validacion_plan con la solicitud correcta")
st.append(H2("8.7 validacion_plan: no cumple"))
st.append(P("Solicitud con nombre incompleto, sin CURP y con otra fecha de nacimiento."))
bp = EJ["plan_rechazo"]
st += code(js({k: bp[k] for k in ("cumple", "mensaje", "motivos_rechazo")}), "validacion_plan con datos que no coinciden (extracto)")
st.append(P("Sin datos de la solicitud, las tres validaciones de coincidencia quedan NO_VERIFICADO:"))
bs = EJ["plan_sin_solicitud"]
st += code(js({k: bs[k] for k in ("cumple", "mensaje", "motivos_rechazo")}), "validacion_plan sin datos de la solicitud (extracto)")
st.append(H2("8.8 Errores"))
st += code("\n\n".join(f"// HTTP {c if int(c) >= 300 else 200}\n" + json.dumps(EJ[c], ensure_ascii=False)
                       for c in ("401", "400", "413", "422", "222", "223", "224", "500")), "Errores (una línea por código)")

# ------------------------------------------------------------------ 9
st.append(H1("9. Health check"))
st.append(P(f"{m('GET /health')} no requiere API key. Úsalo para monitoreo o para verificar la conexión antes de integrar."))
st += code(f'''curl {URL}/health

{{"status": "ok", "vlm": true, "modelo_vlm": "qwen3-vl:8b-instruct", "backend_vlm": "ollama", "base_de_datos": true}}''',
           "Petición y respuesta")
st.append(P("status es «degradado» cuando el modelo de visión no responde: la API sigue extrayendo solo con el OCR y "
            "lo refleja en los avisos. La prueba completa es enviar un documento conocido."))

# ------------------------------------------------------------------ 10
st.append(H1("10. Seguridad"))
st += LI(["<b>API key:</b> trátala como contraseña. Guárdala en variables de entorno o un gestor de secretos; no la "
          "subas a repositorios ni la compartas por correo o chat. Si se filtra, el responsable del servicio la "
          "cambia y la anterior deja de funcionar al instante.",
          "<b>Red:</b> el servicio usa HTTP sin cifrado y está pensado para la red interna. No lo expongas a internet; "
          "si se necesita desde fuera, debe ir detrás de un proxy con HTTPS.",
          "<b>Datos personales:</b> todo se procesa en el servidor de la empresa, sin servicios externos. La API guarda "
          "en su base de datos el JSON del resultado (para el historial y la revisión humana), nunca la imagen ni el PDF. "
          "Los logs solo registran código, estado y tiempos.",
          "<b>Logs:</b> no registres datos de clientes en logs de aplicaciones cliente."])

# ------------------------------------------------------------------ 11
st.append(H1("11. Precisión y limitaciones"))
st.append(P("Medido el 27 de octubre de 2026 sobre el lote de Santander (500 PDFs escaneados): se tomaron <b>58 credenciales</b> "
            "(48 al azar más 10 de las que la API había marcado con 210, 211 o 212) y se capturó cada campo de la imagen con un "
            "modelo de visión independiente del pipeline; las CURP capturadas se verificaron con su dígito y el CIC con la "
            "MRZ. Detalle en docs/EVALUACION.md del proyecto."))
st += tabla(["Indicador", "Resultado", "Base"], [
    ("Precisión por campo, datos de identidad (12 campos sin domicilio)", "<b>99.6 %</b>", "700 campos, 3 errores"),
    ("Precisión por campo, los 14 campos (con domicilio y código postal)", "97.9 %", "814 campos, 17 errores"),
    ("Credenciales con todos los datos de identidad correctos", "<b>96.6 %</b>", "56 de 58"),
    ("Credenciales con los 14 campos correctos", "77.6 %", "45 de 58"),
    ("Datos de identidad con confianza «alta»", "<b>100 %</b>", "628 de 628 correctos"),
    ("Documentos que no son INE rechazados correctamente (220)", "2 de 2", "pasaporte + tarjeta INM; registro de firmas"),
], [8.4, 2.2, 6.6])
st += tabla(["Campo", "Exactitud", "Errores encontrados"], [
    ("apellido_materno, nombre, sexo, fecha_nacimiento, seccion, anio_registro, vigencia, cic, identificador_ciudadano", "100 %", "Ninguno en 58"),
    ("apellido_paterno", "98.3 %", "1: apellido compuesto («MARTINEZ CELIS» → «MARTINEZ»), confianza media, código 211"),
    ("curp", "98.3 %", "1: Ñ leída como N, confianza media, código 210 (fue a revisión)"),
    ("clave_elector", "98.2 %", "1: C leída como G, confianza baja, código 210 (fue a revisión)"),
    ("codigo_postal", "98.2 %", "1: no se extrajo, confianza media, código 212"),
    ("domicilio", "80.7 %", "11: una letra cambiada en el nombre de la calle o colonia (INEONAVIT, QUIRANTE, MENESSES...)"),
], [6.0, 1.8, 9.4], mono_cols=(0,))
st += caja("Los 3 errores en datos de identidad salieron con código <b>210 o 211</b> y confianza media o baja: ninguno se "
           "entregó como 200. Por eso <b>codigo == 200 sirve para aprobar en automático</b>: en la muestra, cuando la API dijo "
           "«todo cuadró», todos los datos de identidad eran correctos.", color=VERDE, fondo=colors.HexColor("#EAF4EA"),
           titulo="Lo que esto significa para la integración")
st.append(H2("11.1 Limitaciones"))
st += LI(["<b>Domicilio:</b> es el único campo con errores en confianza alta (9 de 11). Calle y colonia no tienen contra qué "
          "verificarse (solo el código postal). Si se compara contra la solicitud, hacerlo por palabras con tolerancia de "
          "un carácter.",
          "<b>Apellidos compuestos</b> (MARTINEZ CELIS, DE LA CERDA): el modelo de visión puede quedarse con la primera "
          "palabra; la MRZ sí trae el apellido completo y la comparación del plan lo detecta.",
          "<b>Muestra:</b> 58 credenciales dan intervalos anchos. 0 errores de 58 acota la exactitud real por arriba de "
          "~94 %; 100 % en confianza alta sobre 628 campos acota el error por debajo de ~0.5 %.",
          "Escaneos muy borrosos, sellos sobre la CURP o la MRZ y credenciales giradas bajan la precisión; la API lo "
          "refleja con 210, 211 o 212 y con confianza media o baja, no con un 200.",
          "Procesa 2 credenciales a la vez; las demás esperan turno (sección 7.3)."])

# ------------------------------------------------------------------ 12
st.append(H1("12. Preguntas frecuentes"))
faqs = [
    ("Recibo HTTP 200 pero no hay datos.", f"Revisa {m('codigo')}: probablemente es 220, 221, 222, 223 o 224. Esos códigos viajan con HTTP 200."),
    ("¿Por qué falta una llave en frente?", "Porque ese dato no se pudo leer o ese modelo de credencial no lo imprime (ver no_impresos). Usa acceso seguro (.get, ??, TryGetProperty)."),
    ("¿Puedo mandar anverso y reverso en archivos separados?", "No. La API recibe un PDF por petición; une ambas caras en un solo PDF. Sin reverso no hay MRZ."),
    ("¿Puedo mandar una imagen JPG o PNG?", "No en el servidor de Santander (SOLO_PDF=true): responde 400. Conviértela a PDF antes de enviarla."),
    ("El PDF trae otros documentos además de la INE.", "La API solo analiza las primeras 4 páginas y ubica la credencial por sus anclas. Si la INE viene después de la página 4, envía solo sus páginas."),
    ("Me responde 401.", "Falta el header X-API-Key, tiene otro nombre o la clave es incorrecta. Las mayúsculas del nombre del header no importan; el valor sí."),
    ("Me responde 422.", "El archivo no viaja en el campo file o la petición no es multipart/form-data. El mensaje indica qué faltó."),
    ("No envío datos de la solicitud y cumple sale false.", "Es lo esperado: sin esos datos no se pueden responder las validaciones de coincidencia (NO_VERIFICADO). La extracción en frente sigue siendo válida."),
    ("¿codigo 200 significa que cumple el plan?", "No necesariamente. codigo habla de la extracción; validacion_plan.cumple dice si coincide con la solicitud y está vigente."),
    ("La CURP salió NO pero la capturada es correcta.", "La API tolera hasta 2 confusiones típicas del OCR si la CURP capturada cuadra con la fecha y el sexo. Si aun así no coincide, revisa la imagen: puede haber un sello encima."),
    ("¿Qué es campos_reconstruidos?", "Campos (CURP o clave) que no se leyeron completos y se derivaron de otros datos validados. Por configuración el documento va a revisión."),
    ("¿Cuánto cuesta cada documento?", "Nada: todo el procesamiento es open source y corre en el servidor de la empresa."),
]
for q, a in faqs:
    st.append(KeepTogether([P(f"<b>{q}</b>", "p"), P(a, "nota"), Spacer(1, 4)]))

# ------------------------------------------------------------------ 13
st.append(H1("13. Prompt para implementar con IA"))
st.append(P("Copia este prompt en un asistente de IA (Claude, ChatGPT, Copilot...) e indica el lenguaje o framework. "
            "Contiene el contrato completo para generar un cliente correcto. También está en el archivo "
            f"{m('docs/PROMPT_IA.md')} del proyecto, más fácil de copiar."))
st += code(PROMPT, "Prompt")

# ------------------------------------------------------------------ 14
st.append(KeepTogether([H1("14. Glosario")] + tabla(["Término", "Significado"], [
    ("INE", "Instituto Nacional Electoral (México), emisor de la credencial para votar."),
    ("MRZ", "Zona de lectura mecánica: las 3 líneas con caracteres «&lt;» al pie del reverso. Formato ICAO TD1."),
    ("Dígito de control / verificador", "Número calculado a partir de un dato (MRZ, CURP) que permite detectar errores de lectura."),
    ("CIC", "Código de Identificación de Credencial (9 dígitos, en la MRZ). También llamado folio."),
    ("Identificador del ciudadano", "Número de 9 dígitos (OCR) impreso en la MRZ."),
    ("CURP", "Clave Única de Registro de Población (18 caracteres, con dígito verificador)."),
    ("Clave de elector", "18 caracteres: iniciales del nombre, fecha de nacimiento, estado, sexo y consecutivo."),
    ("Anverso / reverso", "Frente y vuelta de la credencial."),
    ("PaddleOCR", "Motor OCR open source que lee el texto de la página y ubica anverso y reverso."),
    ("Qwen3-VL", "Modelo de visión y lenguaje open source (8 B parámetros) que lee el anverso y devuelve un JSON."),
    ("Plan de atención", "«Plan de atención - Validación documental» de Santander: las 7 preguntas SÍ/NO de la sección INE."),
], [4.0, 13.2])))

doc = Doc(str(SALIDA), pagesize=letter, title="API OCR de Credencial para Votar INE - Guía de integración",
          author="Alexis Eduardo Juárez Jaimes", subject="Documentación para desarrolladores", leftMargin=2.1 * cm,
          rightMargin=2.1 * cm, topMargin=2.0 * cm, bottomMargin=2.0 * cm)
doc.multiBuild(st)
print("ok", SALIDA)
