"""Private, immutable experiment history; GET never evaluates or certifies holdouts.

Transactions belong to the caller, matching ArchiveService. Registration prepares one
bounded row containing all folds, then flushes once; the caller commits or rolls back.
"""
import hashlib
import json
import logging
import math
from pathlib import Path
import re
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, true
from sqlalchemy.exc import SQLAlchemyError

from app.models.stock_ml_registry import MLRegistryBase, MLRegistrySchemaVersion, StockMLExperiment
from app.models.stock_research_dataset import StockResearchDataset, StockResearchArtifact
from app.schemas.stock_ml import ModelManifest, TimeSplit, TrainingParameters
from app.schemas.stock_ml_experiment import WalkForwardSpec
from app.schemas.stock_ml_registry import ModelSummary, ModelDetail, ModelPage, ModelReport
from app.schemas.stock_research_v2 import DatasetRef, Scope, SHA256, equity_identifier
from app.services.stock_research_jobs import JobError, iso_datetime, utc_now
from app.services.stock_research_v2.archive import ArchiveService, descriptor
from app.services.stock_research_v2.artifacts import ArtifactError

SCHEMA_VERSION = '20261025_019_stock_ml_registry'
SCHEMA_PATH = Path(__file__).resolve().parents[3] / 'migrations' / f'{SCHEMA_VERSION}.sql'
REPORT_KIND = 'stock-walk-forward-calculation-v1'
REPORT_FIELDS = {'parameters','feature_versions','scope','dataset','spec','method_version','parameter_freeze_sha256','status','input_status',
    'holdout_registry_verified','score_semantics','top_n','folds','evaluation','portfolio_backtest','model_api_calls','limitations'}
FOLD_FIELDS = {'method_version','fold_number','mode','split','warmup_dates','session_dates','purged_samples','test_role','sample_sha256',
    'distributions','holdout_registry_verified','fold_id','model','predictions','prediction_sha256','selected_candidates','score_semantics','saved_model'}
DETAIL_BYTES = 512 * 1024
logger = logging.getLogger('qihuo.stock_ml_registry')
LIMITATIONS = [
    '仅登记私有计算历史；来源、日历、股票池及经济标签资格仍未认证。',
    '最终测试封存与消费审计未接通，holdout_registry_verified始终为false；不能据sample SHA认定未见测试。',
    '排序分数不是概率或预期收益；特征重要性不表示因果，未取得生产ML或可实盘资格。',
]


class ModelRegistryService:
    def __init__(self, archive: ArchiveService):
        if not isinstance(archive, ArchiveService):
            raise JobError('invalid_ml_registry_context','需要当前用户/盘的数据归档服务',422)
        self.archive, self.session, self.scope = archive, archive.session, archive.scope

    def ownership(self):
        return (StockMLExperiment.owner_id == self.scope.owner_id, StockMLExperiment.trading_mode == self.scope.trading_mode)

    async def available(self):
        # No optional ML or Arrow import, filesystem writes, evaluation or DDL.
        try:
            await self.archive.require_ready()
            names = [table.name for table in MLRegistryBase.metadata.sorted_tables]
            if self.session.get_bind().dialect.name == 'postgresql':
                for name in names:
                    if await self.session.scalar(text('SELECT to_regclass(quote_ident(current_schema()) || \'.\' || :name)'), {'name':name}) is None:
                        return False
            else:
                literals=','.join("'"+name+"'" for name in names)
                if await self.session.scalar(text(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ({literals})")) != len(names):
                    return False
            checksum = await self.session.scalar(select(MLRegistrySchemaVersion.checksum).where(MLRegistrySchemaVersion.version==SCHEMA_VERSION))
            if checksum != hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest():
                return False
            for model in (StockMLExperiment,MLRegistrySchemaVersion):
                await self.session.execute(select(model).limit(0))
            return True
        except (JobError,SQLAlchemyError,OSError):
            return False

    async def require_ready(self):
        if not await self.available():
            raise JobError('ml_registry_unavailable','股票模型历史019或数据015未迁移/完整性不匹配',503)

    async def _row(self, identifier):
        if not isinstance(identifier,UUID):
            raise JobError('invalid_ml_model_id','模型历史ID无效',422)
        await self.require_ready()
        row=await self.session.scalar(select(StockMLExperiment).where(StockMLExperiment.id==identifier,*self.ownership()))
        if row is None:
            raise JobError('ml_model_not_found','私有模型历史不存在',404)
        return row

    def _detail(self,row):
        try:
            if len(row.detail_json.encode('utf8'))>DETAIL_BYTES:
                raise ValueError()
            content=json.loads(row.detail_json)
            detail=ModelDetail.model_validate({**content,'state':row.state})
            if (detail.id!=row.id or detail.report_sha256!=row.report_sha256 or detail.dataset.dataset_id!=row.dataset_id
                    or detail.dataset.data_version!=row.data_version or detail.fold_count!=row.fold_count or detail.validation_status!=row.validation_status
                    or detail.created_at.isoformat()!=iso_datetime(row.created_at)
                    or any(f.model.scope!=self.scope for f in detail.folds)):
                raise ValueError()
        except (ValueError,TypeError,KeyError,AttributeError):
            raise JobError('ml_registry_integrity_failed','模型历史元数据不一致',409) from None
        return detail

    async def _authorize_datasets(self,rows):
        if not rows:
            return
        identifiers={row.dataset_id for row in rows}
        found=(await self.session.execute(select(StockResearchDataset,StockResearchArtifact).join(
            StockResearchArtifact,StockResearchDataset.manifest_artifact_id==StockResearchArtifact.id).where(
            StockResearchDataset.id.in_(identifiers),*self.archive.ownership()))).all()
        owned={dataset.id:(dataset,artifact) for dataset,artifact in found}
        for row in rows:
            if row.dataset_id not in owned:
                raise JobError('ml_model_not_found','模型固定数据集不属于当前用户/盘',404)
            dataset,artifact=owned[row.dataset_id]
            if dataset.data_version!=row.data_version or artifact.sha256!=row.data_version or artifact.purpose!='dataset' or artifact.state!='ready' or dataset.qualification!='observation_only':
                raise JobError('ml_registry_integrity_failed','模型固定数据版本/资格/登记状态发生变化',409)

    async def get(self,identifier):
        row=await self._row(identifier)
        await self._authorize_datasets([row])
        return self._detail(row)

    async def list(self,*,limit=20,offset=0):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=100000:
            raise JobError('invalid_pagination','模型历史分页超出范围',422)
        await self.require_ready()
        scoped=select(StockMLExperiment.id,StockMLExperiment.created_at).where(*self.ownership()).cte('owned_ml')
        count=select(func.count().label('total')).select_from(scoped).cte('ml_count')
        page=select(scoped).order_by(scoped.c.created_at.desc(),scoped.c.id).limit(limit).offset(offset).cte('ml_page')
        rows=(await self.session.execute(select(StockMLExperiment,count.c.total).select_from(count).outerjoin(page,true()).outerjoin(
            StockMLExperiment,StockMLExperiment.id==page.c.id).order_by(page.c.created_at.desc(),page.c.id))).all()
        await self._authorize_datasets([row for row,total in rows if row is not None])
        items=[]
        for row,total in rows:
            if row is not None:
                detail=self._detail(row)
                items.append(ModelSummary.model_validate(detail.model_dump(include=set(ModelSummary.model_fields))))
        total=rows[0][1]
        return ModelPage(items=items,total=total,has_more=offset+len(items)<total)

    def _document(self,digest,size=None):
        store=self.archive.require_store()
        try:
            document=store.read_json(digest)
            if set(document)!={'kind','report'} or document['kind']!=REPORT_KIND:
                raise ValueError()
            if size is not None and descriptor(document,self.archive.store.max_bytes)[1]!=size:
                raise ValueError()
            return document
        except (ArtifactError,ValueError,TypeError,KeyError):
            raise JobError('ml_registry_integrity_failed','模型报告缺失或内容指纹不一致',409) from None

    async def read_report(self,identifier):
        row=await self._row(identifier)
        await self._authorize_datasets([row])
        detail=self._detail(row)
        return self._registered_report(row,detail,interactive=True)

    def _registered_report(self,row,detail,*,interactive):
        if row.state!='ready':
            raise JobError('ml_registry_quarantined','隔离模型报告不可读取',409)
        if interactive and row.report_byte_size>8*1024*1024:
            raise JobError('model_report_requires_export','报告超过8MiB交互预览预算，须后续分区导出；未展开完整候选',409)
        document=self._document(row.report_sha256,row.report_byte_size)
        try:
            report=document['report']
            if report['scope']!=self.scope.model_dump(mode='json') or report['dataset']!=detail.dataset.model_dump(mode='json'):
                raise ValueError()
            if report['holdout_registry_verified'] is not False or report['parameters']!=detail.parameters.model_dump(mode='json'):
                raise ValueError()
            if [{'fold_id':f['fold_id'],'split':f['split'],'model':f['model']} for f in report['folds']] != [f.model_dump(mode='json') for f in detail.folds]:
                raise ValueError()
        except (ValueError,TypeError,KeyError):
            raise JobError('ml_registry_integrity_failed','模型报告与私有登记不一致',409) from None
        return ModelReport.model_validate(document)

    async def register_experiment(self,*,experiment_sha256,idempotency_key,request_id):
        if not isinstance(experiment_sha256,str) or not re.fullmatch(SHA256,experiment_sha256):
            raise JobError('invalid_ml_report_sha256','模型报告指纹无效',422)
        # Same owner/mode advisory lock as archive; no new DB connection or lock order.
        await self.archive.lock(idempotency_key,request_id)
        await self.require_ready()
        existing=await self.session.scalar(select(StockMLExperiment).where(*self.ownership(),StockMLExperiment.idempotency_key==idempotency_key))
        if existing is not None:
            if existing.report_sha256!=experiment_sha256:
                raise JobError('idempotency_conflict','相同幂等键已登记不同实验内容',409)
            detail=await self.get(existing.id)
            self._registered_report(existing,detail,interactive=False)
            logger.info('request_id=%s user_id=%s mode=%s model_id=%s operation=ml_registered stage=idempotent_verified model_calls=0',request_id,self.scope.owner_id,self.scope.trading_mode,existing.id)
            return detail
        document=self._document(experiment_sha256)
        report=document['report']
        try:
            if Scope.model_validate(report['scope'])!=self.scope:
                raise JobError('ml_model_not_found','实验不属于当前用户/盘',404)
            reference=DatasetRef.model_validate(report['dataset'])
        except JobError:
            raise
        except (ValueError,TypeError,KeyError):
            raise JobError('ml_registry_integrity_failed','模型实验身份无效',409) from None
        parent=await self.archive.read_dataset(reference)
        # Native parsing/version/SHA verification is deliberately lazy and registration-only.
        folds,parameters,status=validate_experiment(self.archive,report,reference,parent)
        identifier,created=uuid4(),utc_now()
        detail=ModelDetail(id=identifier,dataset=reference,state='ready',validation_status=status,created_at=created,fold_count=len(folds),
            feature_names=folds[0]['model'].feature_order,parameters=parameters,folds=folds,report_sha256=experiment_sha256,limitations=LIMITATIONS)
        payload=detail.model_dump(mode='json')
        descriptor(payload,DETAIL_BYTES)
        encoded=json.dumps(payload,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        row=StockMLExperiment(id=identifier,owner_id=self.scope.owner_id,trading_mode=self.scope.trading_mode,idempotency_key=idempotency_key,request_id=request_id,
            dataset_id=reference.dataset_id,data_version=reference.data_version,report_sha256=experiment_sha256,
            report_byte_size=descriptor(document,self.archive.store.max_bytes)[1],state='ready',validation_status=status,fold_count=len(folds),detail_json=encoded,created_at=created)
        self.session.add(row)
        await self.session.flush()
        logger.info('request_id=%s user_id=%s mode=%s model_id=%s operation=ml_registered stage=prepared_not_committed folds=%s model_calls=0',request_id,self.scope.owner_id,self.scope.trading_mode,identifier,len(folds))
        return detail


def validate_experiment(archive,report,reference,parent):
    try:
        import pandas as pd
        from app.services.stock_research_v2.research_experiment import load_experiment_model
        from app.services.stock_research_v2.ml_dataset import frame_fingerprint
        if not isinstance(report,dict) or set(report)!=REPORT_FIELDS:
            raise ValueError()
        parameters=TrainingParameters.model_validate(report['parameters'])
        spec=WalkForwardSpec.model_validate(report['spec'])
        if (report['method_version']!=REPORT_KIND or report['holdout_registry_verified'] is not False
                or report['status']!='exploratory_not_certified' or report['evaluation'] is not None
                or report['model_api_calls']!=0 or report['score_semantics']!='ranking_score_not_probability_or_expected_return'):
            raise ValueError()
        if not parent['start']<=str(spec.sessions[0])<=str(spec.sessions[-1])<=parent['end']:
            raise ValueError()
        frozen={name:report[name] for name in ['parameters','feature_versions','scope','dataset','spec','method_version']}
        if descriptor(frozen,4*1024*1024)[0]!=report['parameter_freeze_sha256']:
            raise ValueError()
        if not isinstance(report['folds'],list) or len(report['folds'])!=spec.fold_count or sum(len(f['predictions']) for f in report['folds'])>2_000_000:
            raise ValueError()
        folds=[]
        for n,fold in enumerate(report['folds']):
            if set(fold)!=FOLD_FIELDS:
                raise ValueError()
            model=ModelManifest.model_validate(fold['model'])
            split=TimeSplit.model_validate(fold['split'])
            train_end=spec.warmup_sessions+spec.train_sessions+n*spec.test_sessions
            train_start=spec.warmup_sessions if spec.mode=='expanding' else train_end-spec.train_sessions
            validation_end=train_end+spec.validation_sessions
            expected=TimeSplit(train_start=spec.sessions[train_start],train_end=spec.sessions[train_end-1],validation_start=spec.sessions[train_end],
                validation_end=spec.sessions[validation_end-1],test_start=spec.sessions[validation_end],test_end=spec.sessions[validation_end+spec.test_sessions-1])
            if fold['holdout_registry_verified'] is not False or fold['test_role']!=('terminal_holdout' if n==spec.fold_count-1 else 'walk_forward_oos'):
                raise ValueError()
            if split!=expected or model.scope!=archive.scope or model.dataset!=reference or model.parameters!=parameters or model.feature_versions!=report['feature_versions'] or model.training_knowledge_cutoff>=split.test_start:
                raise ValueError()
            manifest={key:value for key,value in fold.items() if key not in {'fold_id','model','predictions','prediction_sha256','selected_candidates','score_semantics','saved_model'}}
            if descriptor(manifest,4*1024*1024)[0]!=fold['fold_id']:
                raise ValueError()
            restored=load_experiment_model(archive.require_store(),fold['saved_model']['sha256'],scope=archive.scope,dataset=reference)
            if restored.manifest!=model:
                raise ValueError()
            validate_native_ranker(restored)
            stored=fold['saved_model']
            actual=archive.store.read_json(stored['sha256'])
            if stored['relative_path']!=stored['sha256']+'.json' or descriptor(actual,archive.store.max_bytes)[1]!=stored['byte_size']:
                raise ValueError()
            records=fold['predictions']
            if not records:
                raise ValueError()
            for record in records:
                if set(record)!={'decision_date','instrument_id','score','reason'}:
                    raise ValueError()
                equity_identifier(record['instrument_id'])
                day=pd.Timestamp(record['decision_date']).date()
                if str(day)!=record['decision_date'] or not split.test_start<=day<=split.test_end or record['instrument_id'] not in parent['instruments']:
                    raise ValueError()
                score=record['score']
                if score is not None and (type(score) not in (int,float) or not math.isfinite(score)):
                    raise ValueError()
                if record['reason'] not in (None,'missing_all_features') or (score is None)!=(record['reason']=='missing_all_features'):
                    raise ValueError()
            prediction=pd.DataFrame(records)
            prediction['decision_date']=pd.to_datetime(prediction.decision_date)
            expected_sessions=spec.sessions[validation_end:validation_end+spec.test_sessions]
            if sorted(set(prediction.decision_date.dt.date))!=expected_sessions or fold['session_dates']['test']!=[str(day) for day in expected_sessions]:
                raise ValueError()
            if prediction.duplicated(['decision_date','instrument_id']).any() or frame_fingerprint(prediction,['decision_date','instrument_id','score','reason'])!=fold['prediction_sha256']:
                raise ValueError()
            folds.append({'fold_id':fold['fold_id'],'split':split,'model':model})
        status=folds[0]['model'].validation_status
        if reference.qualification=='observation_only' and status!='qa_only':
            raise ValueError()
        return folds,parameters,status
    except JobError:
        raise
    except (ValueError,TypeError,KeyError,AttributeError,IndexError,ArtifactError):
        raise JobError('ml_registry_integrity_failed','实验冻结参数、原生模型、预测或时间范围不一致',409) from None


def validate_native_ranker(restored):
    """Parse only bounded internal registration inputs; never used by list/get.

    The stored native text is the best-validation prefix already selected during
    training. A reloaded Booster does not retain Python's best_iteration marker,
    so current_iteration/num_trees must equal the persisted trees_used count.
    """
    from app.services.stock_research_v2.ml_train import engine, MAX_MODEL_BYTES
    model=restored.manifest
    if not 0<len(restored.model_text.encode('utf8'))<=MAX_MODEL_BYTES:
        raise JobError('ml_registry_integrity_failed','原生排序模型超过登记大小预算',409)
    lgb,_=engine()
    try:
        # LightGBM 4.7 ignores params= when loading model_str; validate the
        # actual persisted thread budget instead. No training/predict is run here.
        native=lgb.Booster(model_str=restored.model_text)
        names=[f'f{index:03d}' for index in range(len(model.feature_order))]
        if (native.params.get('num_threads')!=2 or native.num_feature()!=len(names) or native.feature_name()!=names
                or native.num_model_per_iteration()!=1 or native.num_trees()!=model.trees_used
                or native.current_iteration()!=model.trees_used
                or native.dump_model(num_iteration=1).get('objective')!='lambdarank'):
            raise ValueError()
    except (lgb.basic.LightGBMError,ValueError,TypeError,KeyError,AttributeError):
        raise JobError('ml_registry_integrity_failed','原生排序模型解析、特征、目标或树数量与登记不一致',409) from None
