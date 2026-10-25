from datetime import datetime
from uuid import UUID as PythonUUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.stock_research_job import StockResearchJob


class BookmarkBase(DeclarativeBase):
    pass


class StockReportBookmark(BookmarkBase):
    __tablename__ = "stock_report_bookmarks"
    __table_args__ = (Index("ix_stock_report_bookmark_created", "created_at", "job_id"),)

    job_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), ForeignKey(StockResearchJob.__table__.c.id, ondelete="RESTRICT"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class StockReportBookmarkSchemaVersion(BookmarkBase):
    __tablename__ = "stock_report_bookmark_schema_versions"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
