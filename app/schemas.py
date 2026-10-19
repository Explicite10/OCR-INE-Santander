"""Modelos de la respuesta JSON de la API."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Confianza = Literal["alta", "media", "baja"]
Resultado = Literal["ok", "falla", "no_aplica"]


class Nombre(BaseModel):
    apellido_paterno: str | None = None
    apellido_materno: str | None = None
    nombres: str | None = None
    nombre_completo: str | None = None


class Domicilio(BaseModel):
    lineas: list[str] = Field(default_factory=list, description="Renglones tal como aparecen impresos")
    calle: str | None = None
    colonia: str | None = None
    codigo_postal: str | None = None
    municipio: str | None = None
    estado: str | None = None
    estado_abreviatura: str | None = None
    completo: str | None = None


class Vigencia(BaseModel):
    desde: str | None = None
    hasta: str | None = None
    vigente: bool | None = None


class DatosINE(BaseModel):
    nombre: Nombre = Field(default_factory=Nombre)
    sexo: str | None = None
    fecha_nacimiento: str | None = Field(None, description="AAAA-MM-DD")
    curp: str | None = None
    clave_elector: str | None = None
    domicilio: Domicilio = Field(default_factory=Domicilio)
    seccion: str | None = None
    anio_registro: str | None = None
    anio_registro_sufijo: str | None = Field(None, description="2 dígitos que siguen al año de registro")
    vigencia: Vigencia = Field(default_factory=Vigencia)
    # Sólo en credenciales con campos ESTADO / MUNICIPIO / LOCALIDAD / EMISIÓN
    estado_clave: str | None = None
    municipio_clave: str | None = None
    localidad: str | None = None
    emision: str | None = None
    # Reverso
    cic: str | None = Field(None, description="Código de Identificación de la Credencial (9 dígitos)")
    identificador_ciudadano: str | None = Field(None, description="Últimos 9 dígitos de la línea 1 de la MRZ")
    numero_ocr: str | None = Field(None, description="13 dígitos: sección + identificador")
    mrz: list[str] = Field(default_factory=list)
    modelo_credencial: str | None = None


class Validacion(BaseModel):
    regla: str
    resultado: Resultado
    detalle: str | None = None


class Meta(BaseModel):
    tiempo_total_ms: int
    tiempos_ms: dict[str, int] = Field(default_factory=dict)
    modelo_vlm: str | None = None
    paginas: int = 0
    rotado: bool = False
    recorte_anverso: str | None = None
    recorte_reverso: str | None = None
    mrz_fuente: str | None = None
    senales_ine: list[str] = Field(default_factory=list, description="Indicios de que es una INE (OCR)")
    caracteres_leidos: int = Field(0, description="Letras y dígitos que leyó el OCR en todo el documento")
    categoria: Literal["ine", "otro", "hoja_en_blanco"] | None = None
    documento_detectado: str | None = Field(None, description="Si no es INE: qué parece (pasaporte, licencia...)")


class ResultadoINE(BaseModel):
    id: str | None = Field(None, description="Id en la base de datos (si está activa)")
    archivo: str | None = None
    estado: Literal["ok", "revisar", "error", "rechazado"] = Field(
        description="rechazado = el documento no parece una credencial INE mexicana (ver `error`)")
    datos: DatosINE = Field(default_factory=DatosINE)
    confianza: dict[str, Confianza] = Field(default_factory=dict)
    campos_a_revisar: list[str] = Field(default_factory=list)
    campos_reconstruidos: list[str] = Field(
        default_factory=list, description="Campos ilegibles que se derivaron de otros datos validados")
    validaciones: list[Validacion] = Field(default_factory=list)
    advertencias: list[str] = Field(default_factory=list)
    error: str | None = None
    error_codigo: int | None = Field(None, description="222 corrupto · 223 con contraseña · 224 demasiadas páginas")
    meta: Meta | None = None
    recortes: dict[str, str] | None = Field(None, description="PNG en base64 (sólo si se pide)")


class ExtraccionResumen(BaseModel):
    id: str
    creado: datetime
    archivo: str | None = None
    estado: str
    curp: str | None = None
    clave_elector: str | None = None
    nombre_completo: str | None = None
    campos_a_revisar: list[str] = Field(default_factory=list)
    tiempo_ms: int | None = None


class ListaExtracciones(BaseModel):
    total: int
    elementos: list[ExtraccionResumen]


class ExtraccionDetalle(ExtraccionResumen):
    resultado: dict[str, Any]
    datos_corregidos: dict[str, Any] | None = None
    revisado_por: str | None = None
    revisado_en: datetime | None = None


class Revision(BaseModel):
    datos: dict[str, Any] = Field(..., description="Datos corregidos (misma forma que 'datos' del resultado)")
    revisado_por: str | None = None
