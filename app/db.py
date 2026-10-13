"""Persistencia de resultados en PostgreSQL (o SQLite en pruebas).

Es opcional: si DATABASE_URL no está definida, la API funciona sin guardar nada.
Nunca se guardan las imágenes, sólo los datos extraídos.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

log = logging.getLogger(__name__)

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Extraccion(Base):
    __tablename__ = "extracciones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
                                             index=True)
    archivo: Mapped[str | None] = mapped_column(String(255))
    estado: Mapped[str] = mapped_column(String(16), index=True)  # ok | revisar | error | revisado
    curp: Mapped[str | None] = mapped_column(String(18), index=True)
    clave_elector: Mapped[str | None] = mapped_column(String(18), index=True)
    nombre_completo: Mapped[str | None] = mapped_column(String(255))
    campos_a_revisar: Mapped[list] = mapped_column(JsonType, default=list)
    tiempo_ms: Mapped[int | None] = mapped_column(Integer)
    modelo_vlm: Mapped[str | None] = mapped_column(String(100))
    resultado: Mapped[dict] = mapped_column(JsonType)  # JSON completo que devolvió la API
    # Corrección humana (cuando un documento se revisa a mano)
    datos_corregidos: Mapped[dict | None] = mapped_column(JsonType)
    revisado_por: Mapped[str | None] = mapped_column(String(100))
    revisado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self, retries: int = 30) -> None:
        """Crea las tablas; reintenta mientras la base de datos termina de arrancar."""
        for attempt in range(retries):
            try:
                async with self.engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                return
            except Exception as e:  # noqa: BLE001
                if attempt == retries - 1:
                    raise
                log.info("Esperando la base de datos (%s)...", type(e).__name__)
                await asyncio.sleep(2)

    async def close(self) -> None:
        await self.engine.dispose()

    async def save(self, result: dict) -> str:
        d = result.get("datos") or {}
        row = Extraccion(
            archivo=(result.get("archivo") or "")[:255] or None,
            estado=result["estado"],
            curp=d.get("curp") if d.get("curp") and len(d["curp"]) == 18 else None,
            clave_elector=d.get("clave_elector") if d.get("clave_elector") and len(d["clave_elector"]) == 18 else None,
            nombre_completo=((d.get("nombre") or {}).get("nombre_completo") or "")[:255] or None,
            campos_a_revisar=result.get("campos_a_revisar") or [],
            tiempo_ms=(result.get("meta") or {}).get("tiempo_total_ms"),
            modelo_vlm=(result.get("meta") or {}).get("modelo_vlm"),
            resultado=result,
        )
        async with self.session() as s:
            s.add(row)
            await s.commit()
        return row.id

    async def get(self, id_: str) -> Extraccion | None:
        async with self.session() as s:
            return await s.get(Extraccion, id_)

    async def list(self, estado: str | None = None, curp: str | None = None, q: str | None = None,
                   limit: int = 50, offset: int = 0) -> tuple[int, list[Extraccion]]:
        stmt = select(Extraccion)
        if estado:
            stmt = stmt.where(Extraccion.estado == estado)
        if curp:
            stmt = stmt.where(Extraccion.curp == curp.upper())
        if q:
            like = f"%{q.upper()}%"
            stmt = stmt.where(Extraccion.nombre_completo.ilike(like) | Extraccion.archivo.ilike(like))
        async with self.session() as s:
            total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = (await s.scalars(stmt.order_by(Extraccion.creado.desc()).limit(limit).offset(offset))).all()
        return total or 0, list(rows)

    async def review(self, id_: str, datos: dict, revisado_por: str | None) -> Extraccion | None:
        async with self.session() as s:
            row = await s.get(Extraccion, id_)
            if row is None:
                return None
            row.datos_corregidos = datos
            row.revisado_por = revisado_por
            row.revisado_en = datetime.now(timezone.utc)
            row.estado = "revisado"
            await s.commit()
            return row
