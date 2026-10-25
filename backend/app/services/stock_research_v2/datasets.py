from pydantic import ValidationError

from app.schemas.stock_dataset_inspection import DatasetDescription
from app.services.stock_research_jobs import JobError
from app.services.stock_research_v2.archive import datasets_ready

MAX_DETAIL_BYTES = 512 * 1024


class DatasetCatalog:
    def __init__(self, archive):
        self.archive = archive

    async def availability(self):
        ready = await datasets_ready(self.archive.session)
        root_available = False
        if self.archive.store is not None:
            root = self.archive.store.root
            try:
                root_available = root.is_dir() and not root.is_symlink() and root.resolve() == root
            except OSError:
                root_available = False
        return {"catalog_enabled": ready, "detail_enabled": ready and root_available, "inspection_enabled": False, "research_enabled": False, "parquet_enabled": False,
            "reason": "只读元数据目录；完整来源检查、交易日历资格、Parquet与异步研究尚未接通" if ready else "固定数据登记表未迁移或校验和不匹配；未自动建表"}

    async def list(self, *, limit, offset):
        return await self.archive.list_datasets(limit=limit, offset=offset)

    async def describe(self, reference):
        manifest = await self.archive.describe_dataset(reference, max_manifest_bytes=MAX_DETAIL_BYTES)
        try:
            result = DatasetDescription.model_validate({"reference": reference, **{key: value for key, value in manifest.items() if key != "qualification"},
                "integrity": "manifest_verified_sources_not_checked", "research_enabled": False,
                "limitations": ["展示的是清单声明的研究区间与股票池，不是实际行情覆盖率。", "本请求只核对清单和来源登记，不读取全部价格文件或认证来源真实性。", "源文件、交易日历、公告时点、历史成分及执行资格须另行检查，当前不允许IC/回测。"]})
        except ValidationError:
            raise JobError("dataset_integrity_failed", "清单字段与固定数据集契约不匹配", 409) from None
        return result.model_dump(mode="json")
