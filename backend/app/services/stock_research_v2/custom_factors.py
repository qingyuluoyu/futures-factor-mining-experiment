import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, inspect, select, text, true

from app.models.stock_custom_factor import CustomFactorBase, CustomFactorSchemaVersion, StockCustomFactor, StockCustomFactorMutation, StockCustomFactorVersion
from app.services.stock_research_jobs import JobError, iso_datetime
from app.services.stock_research_v2.expressions import METHOD_VERSION, compile_expression

SCHEMA_VERSION = "20261023_017_stock_custom_factors"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "migrations" / f"{SCHEMA_VERSION}.sql"
FIELD_SPECS = (
    {"id": "close", "unit": "CNY/share", "availability": "统一复权锚点收盘价；仅使用信号日及之前；未认证数据不进入回测"},
    {"id": "pe_ttm", "unit": "ratio", "availability": "历史时点每日估值；非正PE排除"},
    {"id": "pb", "unit": "ratio", "availability": "历史时点每日估值；非正PB排除"},
    {"id": "revenue_yoy", "unit": "percent", "availability": "公告可得后；仅有公告日期时保守下一交易日"},
    {"id": "netprofit_yoy", "unit": "percent", "availability": "公告可得后；修订不得覆盖历史披露"},
    {"id": "roe", "unit": "percent", "availability": "公告可得后；财报口径随数据版本固定"},
    {"id": "gross_margin", "unit": "percent", "availability": "公告可得后；财报口径随数据版本固定"},
    {"id": "turnover", "unit": "percent", "availability": "交易日收盘后的真实换手率"},
    {"id": "marketcap", "unit": "CNY", "availability": "交易日历史市值，源单位必须先规范化"},
)
ALLOWED_FIELDS = {field["id"] for field in FIELD_SPECS}


async def custom_factors_ready(session):
    names = [table.name for table in CustomFactorBase.metadata.sorted_tables]
    if session.get_bind().dialect.name == "postgresql":
        for name in names:
            if not await session.scalar(text("SELECT to_regclass(quote_ident(current_schema()) || '.' || :name)"), {"name": name}):
                return False
    elif not all(inspect(session.get_bind()).has_table(name) for name in names):
        return False
    checksum = await session.scalar(select(CustomFactorSchemaVersion.checksum).where(CustomFactorSchemaVersion.version == SCHEMA_VERSION))
    if checksum != hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest():
        return False
    for model in (StockCustomFactor, StockCustomFactorVersion, StockCustomFactorMutation):
        await session.execute(select(model).limit(0))
    return True


def compiled_view(compiled):
    return {"expression": compiled.expression, "expression_version": compiled.version, "formula_version": METHOD_VERSION, "fields": list(compiled.fields), "lookback": compiled.lookback}


class CustomFactorService:
    def __init__(self, session, owner_id: UUID, mode: str):
        if mode not in ("virtual", "live"):
            raise JobError("invalid_mode", "当前登录盘无效", 409)
        self.session, self.owner_id, self.mode = session, owner_id, mode

    async def require_ready(self):
        if not await custom_factors_ready(self.session):
            raise JobError("custom_factors_unavailable", "自定义因子尚未部署或迁移版本不匹配；未写入定义", 503)

    def scope(self):
        return (StockCustomFactor.owner_id == self.owner_id, StockCustomFactor.trading_mode == self.mode)

    async def head(self, identifier):
        row = await self.session.scalar(select(StockCustomFactor).where(StockCustomFactor.id == identifier, *self.scope()))
        if row is None:
            raise JobError("factor_not_found", "自定义因子不存在", 404)
        return row

    def view(self, head, version):
        return {"id": str(head.id), "revision": version.revision, "latest_revision": head.latest_revision, "name": version.name,
            "expression": version.expression, "expression_version": version.expression_version, "formula_version": version.formula_version,
            "fields": version.fields_json, "lookback": version.lookback, "direction": version.direction, "description": version.description,
            "disabled": head.disabled, "created_at": iso_datetime(version.created_at)}

    async def get(self, identifier, revision):
        await self.require_ready()
        head = await self.head(identifier)
        version = await self.session.scalar(select(StockCustomFactorVersion).where(StockCustomFactorVersion.factor_id == identifier, StockCustomFactorVersion.revision == revision))
        if version is None:
            raise JobError("factor_version_not_found", "自定义因子版本不存在", 404)
        return self.view(head, version)

    async def list(self, *, limit, offset, identifier=None):
        await self.require_ready()
        version, head = StockCustomFactorVersion, StockCustomFactor
        query = select(head.id.label("factor_id"), version.revision).join(version, version.factor_id == head.id).where(*self.scope())
        if identifier is not None:
            await self.head(identifier)
            query = query.where(head.id == identifier)
        else:
            query = query.where(version.revision == head.latest_revision)
        scoped = query.cte("custom_scope")
        count = select(func.count().label("total")).select_from(scoped).cte("custom_count")
        page = select(scoped).order_by(scoped.c.factor_id, scoped.c.revision.desc()).offset(offset).limit(limit).cte("custom_page")
        rows = (await self.session.execute(select(head, version, count.c.total).select_from(count).outerjoin(page, true()).outerjoin(
            head, head.id == page.c.factor_id).outerjoin(version, (version.factor_id == page.c.factor_id) & (version.revision == page.c.revision)).order_by(page.c.factor_id, page.c.revision.desc()))).all()
        items = [self.view(record, revision) for record, revision, total in rows if record is not None]
        total = rows[0][2]
        return {"items": items, "total": total, "has_more": offset + len(items) < total}

    async def mutate(self, body, *, request_id, identifier=None, disable=False):
        await self.require_ready()
        payload = {"operation": "disable" if disable else "edit" if identifier else "create", "id": str(identifier) if identifier else None, **body.model_dump(mode="json", exclude={"idempotency_key"})}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if self.session.get_bind().dialect.name == "postgresql":
            lock = int.from_bytes(hashlib.sha256(f"stock-custom-factor:{self.owner_id}:{self.mode}".encode()).digest()[:8], "big", signed=True)
            await self.session.execute(text("SET LOCAL lock_timeout='3s'"))
            await self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
        existing = await self.session.scalar(select(StockCustomFactorMutation).where(StockCustomFactorMutation.owner_id == self.owner_id,
            StockCustomFactorMutation.trading_mode == self.mode, StockCustomFactorMutation.idempotency_key == body.idempotency_key))
        if existing is not None:
            if existing.request_hash != digest:
                raise JobError("idempotency_conflict", "幂等键已用于不同操作或参数；未覆盖原定义", 409)
            return existing.result_json
        if identifier is None:
            head = StockCustomFactor(id=uuid4(), owner_id=self.owner_id, trading_mode=self.mode, latest_revision=1, disabled=False)
            self.session.add(head)
            await self.session.flush()
        else:
            head = await self.head(identifier)
            if head.latest_revision != body.expected_revision:
                raise JobError("factor_revision_conflict", "因子已更新，请刷新后基于明确版本编辑", 409)
            if head.disabled and not disable:
                raise JobError("factor_disabled", "因子已停用；历史版本保留但不再产生新版本", 409)
        if disable:
            head.disabled = True
        else:
            compiled = compile_expression(body.expression, allowed_fields=ALLOWED_FIELDS)
            if identifier is not None:
                head.latest_revision += 1
            self.session.add(StockCustomFactorVersion(factor_id=head.id, revision=head.latest_revision, name=body.name,
                expression=compiled.expression, expression_version=compiled.version, formula_version=METHOD_VERSION, fields_json=list(compiled.fields),
                lookback=compiled.lookback, direction=body.direction, description=body.description))
        await self.session.flush()
        version = await self.session.scalar(select(StockCustomFactorVersion).where(StockCustomFactorVersion.factor_id == head.id, StockCustomFactorVersion.revision == head.latest_revision))
        result = self.view(head, version)
        self.session.add(StockCustomFactorMutation(owner_id=self.owner_id, trading_mode=self.mode, idempotency_key=body.idempotency_key,
            request_hash=digest, request_id=request_id, result_json=result))
        await self.session.flush()
        return result

    async def resolve(self, *, id, revision, expression_version, allow_disabled=False):
        record = await self.get(id, revision)
        if record["disabled"] and not allow_disabled:
            raise JobError("factor_disabled", "因子已停用，不允许新选用；已保存研究仍可引用原版本", 409)
        compiled = compile_expression(record["expression"], allowed_fields=ALLOWED_FIELDS)
        if record["formula_version"] != METHOD_VERSION or expression_version != compiled.version or record["expression_version"] != compiled.version or record["fields"] != list(compiled.fields) or record["lookback"] != compiled.lookback:
            raise JobError("factor_version_conflict", "自定义因子版本或依赖元数据不匹配", 409)
        return compiled
