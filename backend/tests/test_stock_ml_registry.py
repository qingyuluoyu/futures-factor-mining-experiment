import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from app.models.stock_ml_registry import MLRegistryBase, MLRegistrySchemaVersion, StockMLExperiment
from app.schemas.stock_research_v2 import DatasetBuild
from app.services.stock_research_v2.model_registry import ModelRegistryService, SCHEMA_PATH, SCHEMA_VERSION
from app.services.stock_research_jobs import JobError
from tests.test_stock_research_archive_v2 import prepare, service, page
from tests.test_stock_research_job_service import job_database


async def prepare_registry(factory):
    await prepare(factory)
    async with factory() as session:
        MLRegistryBase.metadata.create_all(session.get_bind())
        session.add(MLRegistrySchemaVersion(version=SCHEMA_VERSION, checksum=hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()))
        await session.commit()


async def experiment(archive):
    from tests.test_stock_ml_dataset import dataset
    from tests.test_stock_ml_split import plan
    from app.services.stock_research_v2.ml_predict import run_walk_forward_experiment
    samples, data, _, _ = dataset()
    source = await archive.archive_page(page(), idempotency_key=uuid4(), request_id='a'*32)
    ref = await archive.build_dataset(DatasetBuild(source_ids=[source.source_id],start=data['close'].index[0].date(),warmup_start=data['close'].index[0].date(),end=data['close'].index[-1].date(),pool_id='qa',pool_version='d'*64,instruments=list(data['close'].columns)),idempotency_key=uuid4(),request_id='b'*32)
    return run_walk_forward_experiment(samples, plan(data,fold_count=2),scope=archive.scope,dataset=ref,feature_versions={'quality':'b'*64,'value':'c'*64},qa_only=True,parameters={'n_estimators':10,'max_depth':3,'num_leaves':7,'min_child_samples':5},store=archive.store)


@pytest.mark.asyncio
async def test_native_register_idempotency_private_history_and_read_is_not_evaluation(tmp_path):
    async with job_database() as (factory,owner,other):
        await prepare_registry(factory)
        async with factory() as session:
            archive=service(session,owner,tmp_path/'store')
            registry=ModelRegistryService(archive)
            assert await registry.available()
            result=await experiment(archive)
            key=uuid4()
            detail=await registry.register_experiment(experiment_sha256=result['saved_report']['sha256'],idempotency_key=key,request_id='c'*32)
            await session.commit()
            assert detail.fold_count==2 and detail.validation_status=='qa_only' and detail.holdout_registry_verified is False
            assert detail.folds[0].model.feature_gain and detail.folds[0].model.runtime['lightgbm']=='4.7.0'
            again=await registry.register_experiment(experiment_sha256=result['saved_report']['sha256'],idempotency_key=key,request_id='d'*32)
            assert again==detail
            await session.commit()
            session.statements.clear()
            assert (await registry.list()).items[0].id==detail.id
            assert (await registry.list(offset=100)).total==1
            assert await registry.get(detail.id)==detail
            document=await registry.read_report(detail.id)
            assert document.kind=='stock-walk-forward-calculation-v1' and document.report['evaluation'] is None
            assert not any(statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for statement in session.statements)
            for identity,mode in [(other,'virtual'),(owner,'live')]:
                denied=ModelRegistryService(service(session,identity,tmp_path/'store',mode))
                assert (await denied.list()).total==0
                for method in [denied.get,denied.read_report]:
                    with pytest.raises(JobError) as error: await method(detail.id)
                    assert error.value.status==404
            for values in [{'limit':101},{'offset':100001},{'limit':True}]:
                with pytest.raises(JobError): await registry.list(**values)
            bad=archive.store.read_json(result['saved_report']['sha256'])
            bad['report']['top_n']=21
            changed=archive.store.put_json(bad)
            with pytest.raises(JobError) as conflict:
                await registry.register_experiment(experiment_sha256=changed.sha256,idempotency_key=key,request_id='e'*32)
            assert conflict.value.code=='idempotency_conflict'
            await session.rollback()
            row=await session.scalar(select(StockMLExperiment).where(StockMLExperiment.id==detail.id))
            row.state='quarantined'; await session.commit()
            assert (await registry.list()).items[0].state=='quarantined'
            assert (await registry.get(detail.id)).state=='quarantined'
            with pytest.raises(JobError) as quarantined: await registry.read_report(detail.id)
            assert quarantined.value.code=='ml_registry_quarantined'


@pytest.mark.asyncio
async def test_missing_schema_and_checksum_are_fail_closed(tmp_path):
    async with job_database() as (factory,owner,_):
        async with factory() as session:
            registry=ModelRegistryService(service(session,owner,tmp_path/'store'))
            assert await registry.available() is False
            with pytest.raises(JobError): await registry.list()
        await prepare_registry(factory)
        async with factory() as session:
            version=await session.scalar(select(MLRegistrySchemaVersion))
            version.checksum='0'*64; await session.commit()
            assert await ModelRegistryService(service(session,owner,tmp_path/'store')).available() is False

from copy import deepcopy


@pytest.mark.asyncio
async def test_registration_rejects_forged_native_predictions_and_holdout_without_partial_row(tmp_path):
    async with job_database() as (factory,owner,other):
        await prepare_registry(factory)
        async with factory() as session:
            archive=service(session,owner,tmp_path/'store')
            registry=ModelRegistryService(archive)
            result=await experiment(archive)
            original=archive.store.read_json(result['saved_report']['sha256'])
            variants=[]
            bad=deepcopy(original); bad['report']['holdout_registry_verified']=True; variants.append(bad)
            bad=deepcopy(original); bad['report']['native_model_text']='must-not-return-native-text'; variants.append(bad)
            bad=deepcopy(original); bad['report']['folds'][1]['predictions'][0]['score']+=1; variants.append(bad)
            bad=deepcopy(original); bad['report']['folds'][1]['model']['model_sha256']='f'*64; variants.append(bad)
            bad=deepcopy(original); bad['report']['folds'][1]['saved_model']['byte_size']+=1; variants.append(bad)
            bad=deepcopy(original); bad['report']['folds'][1]['split']['test_start']='2024-01-01'; variants.append(bad)
            for document in variants:
                digest=archive.store.put_json(document).sha256
                with pytest.raises(JobError) as rejected:
                    await registry.register_experiment(experiment_sha256=digest,idempotency_key=uuid4(),request_id='e'*32)
                assert rejected.value.code=='ml_registry_integrity_failed'
                assert await session.scalar(select(func.count()).select_from(StockMLExperiment))==0
            bad=deepcopy(original); bad['report']['scope']['owner_id']=str(other)
            with pytest.raises(JobError) as rejected:
                await registry.register_experiment(experiment_sha256=archive.store.put_json(bad).sha256,idempotency_key=uuid4(),request_id='e'*32)
            assert rejected.value.status==404
            await session.commit()
            created=await registry.register_experiment(experiment_sha256=result['saved_report']['sha256'],idempotency_key=uuid4(),request_id='f'*32)
            await session.rollback()
            assert (await registry.list()).total==0
            with pytest.raises(JobError) as absent: await registry.read_report(created.id)
            assert absent.value.status==404


@pytest.mark.asyncio
async def test_metadata_read_is_batched_without_source_file_or_optional_engine_and_rechecks_dataset_owner(tmp_path,monkeypatch):
    import builtins
    from app.models.stock_research_dataset import StockResearchArtifact,StockResearchDataset
    async with job_database() as (factory,owner,other):
        await prepare_registry(factory)
        async with factory() as session:
            archive=service(session,owner,tmp_path/'store'); registry=ModelRegistryService(archive)
            result=await experiment(archive)
            for _ in range(3):
                detail=await registry.register_experiment(experiment_sha256=result['saved_report']['sha256'],idempotency_key=uuid4(),request_id='c'*32)
            await session.commit()
            original_import=builtins.__import__
            def guard_import(name,*args,**kwargs):
                assert name not in {'lightgbm','pyarrow'}, 'metadata must not require native engines'
                return original_import(name,*args,**kwargs)
            def guard_read(*args,**kwargs):
                raise AssertionError('metadata must not scan source/model files')
            monkeypatch.setattr(builtins,'__import__',guard_import)
            monkeypatch.setattr(archive.store,'read_json',guard_read)
            session.statements.clear()
            assert len((await registry.list()).items)==3
            authorization_queries=[query for query in session.statements if 'JOIN stock_research_artifacts' in query]
            assert len(authorization_queries)==1
            assert await registry.get(detail.id)==detail
            parent=await session.scalar(select(StockResearchDataset).where(StockResearchDataset.id==detail.dataset.dataset_id))
            artifact=await session.scalar(select(StockResearchArtifact).where(StockResearchArtifact.id==parent.manifest_artifact_id))
            artifact.owner_id=other; await session.commit()
            for method in [registry.get,registry.read_report]:
                with pytest.raises(JobError) as denied: await method(detail.id)
                assert denied.value.status==404

@pytest.mark.asyncio
async def test_report_preview_rejects_registered_oversize_before_file_read(tmp_path,monkeypatch):
    async with job_database() as (factory,owner,_):
        await prepare_registry(factory)
        async with factory() as session:
            archive=service(session,owner,tmp_path/'store'); registry=ModelRegistryService(archive)
            result=await experiment(archive)
            detail=await registry.register_experiment(experiment_sha256=result['saved_report']['sha256'],idempotency_key=uuid4(),request_id='a'*32)
            row=await session.scalar(select(StockMLExperiment).where(StockMLExperiment.id==detail.id))
            row.report_byte_size=8*1024*1024+1; await session.commit()
            def fail_read(*args,**kwargs): raise AssertionError('must reject before filesystem read')
            monkeypatch.setattr(archive.store,'read_json',fail_read)
            assert await registry.get(detail.id)==detail
            with pytest.raises(JobError) as too_large: await registry.read_report(detail.id)
            assert too_large.value.code=='model_report_requires_export'

@pytest.mark.asyncio
async def test_rehashed_fold_cannot_self_certify_holdout_or_omit_explicit_test_sessions(tmp_path):
    from app.services.stock_research_v2.archive import descriptor
    async with job_database() as (factory,owner,_):
        await prepare_registry(factory)
        async with factory() as session:
            archive=service(session,owner,tmp_path/'store'); registry=ModelRegistryService(archive)
            result=await experiment(archive)
            original=archive.store.read_json(result['saved_report']['sha256'])
            forged=deepcopy(original)
            fold=forged['report']['folds'][0]; fold['holdout_registry_verified']=True
            exclude={'fold_id','model','predictions','prediction_sha256','selected_candidates','score_semantics','saved_model'}
            fold['fold_id']=descriptor({key:value for key,value in fold.items() if key not in exclude},4*1024*1024)[0]
            missing=deepcopy(original)
            lost=missing['report']['folds'][0]['predictions'][0]['decision_date']
            missing['report']['folds'][0]['predictions']=[r for r in missing['report']['folds'][0]['predictions'] if r['decision_date']!=lost]
            import pandas as pd
            from app.services.stock_research_v2.ml_dataset import frame_fingerprint
            frame=pd.DataFrame(missing['report']['folds'][0]['predictions']); frame['decision_date']=pd.to_datetime(frame.decision_date)
            missing['report']['folds'][0]['prediction_sha256']=frame_fingerprint(frame,['decision_date','instrument_id','score','reason'])
            for document in [forged,missing]:
                with pytest.raises(JobError) as failed:
                    await registry.register_experiment(experiment_sha256=archive.store.put_json(document).sha256,idempotency_key=uuid4(),request_id='a'*32)
                assert failed.value.code=='ml_registry_integrity_failed'

@pytest.mark.asyncio
async def test_large_internal_registration_remains_idempotent_with_preview_closed(tmp_path):
    from app.services.stock_research_v2.artifacts import ArtifactStore
    async with job_database() as (factory,owner,_):
        await prepare_registry(factory)
        async with factory() as session:
            archive=service(session,owner,tmp_path/'store')
            archive.store=ArtifactStore(tmp_path/'store',max_bytes=16*1024*1024)
            registry=ModelRegistryService(archive); result=await experiment(archive)
            document=archive.store.read_json(result['saved_report']['sha256'])
            document['report']['limitations'].append('qa-only-big-report-'+'x'*(9*1024*1024))
            digest=archive.store.put_json(document).sha256
            key=uuid4()
            detail=await registry.register_experiment(experiment_sha256=digest,idempotency_key=key,request_id='a'*32)
            await session.commit()
            again=await registry.register_experiment(experiment_sha256=digest,idempotency_key=key,request_id='b'*32)
            assert again==detail
            with pytest.raises(JobError) as too_large: await registry.read_report(detail.id)
            assert too_large.value.code=='model_report_requires_export'

@pytest.mark.asyncio
async def test_native_parser_rejects_self_consistent_forged_text_names_and_tree_count(tmp_path):
    from dataclasses import asdict
    async with job_database() as (factory,owner,_):
        await prepare_registry(factory)
        async with factory() as session:
            archive=service(session,owner,tmp_path/'store'); registry=ModelRegistryService(archive)
            result=await experiment(archive)
            original=archive.store.read_json(result['saved_report']['sha256'])
            native=archive.store.read_json(original['report']['folds'][0]['saved_model']['sha256'])
            for change in ['not-a-lightgbm-model','names','tree_count','threads','objective']:
                forged=deepcopy(original); model=deepcopy(native)
                if change=='not-a-lightgbm-model':
                    model['model']['model_text']=change
                elif change=='names':
                    assert 'feature_names=f000 f001' in model['model']['model_text']
                    model['model']['model_text']=model['model']['model_text'].replace('feature_names=f000 f001','feature_names=bad0 bad1')
                elif change=='objective':
                    assert 'objective=lambdarank' in model['model']['model_text']
                    model['model']['model_text']=model['model']['model_text'].replace('objective=lambdarank','objective=regression')
                elif change=='threads':
                    assert '[num_threads: 2]' in model['model']['model_text']
                    model['model']['model_text']=model['model']['model_text'].replace('[num_threads: 2]','[num_threads: 8]')
                else:
                    model['model']['manifest']['trees_used']+=1
                model['model']['manifest']['model_sha256']=hashlib.sha256(model['model']['model_text'].encode()).hexdigest()
                forged['report']['folds'][0]['model']=deepcopy(model['model']['manifest'])
                forged['report']['folds'][0]['saved_model']=asdict(archive.store.put_json(model))
                digest=archive.store.put_json(forged).sha256
                with pytest.raises(JobError) as error:
                    await registry.register_experiment(experiment_sha256=digest,idempotency_key=uuid4(),request_id='a'*32)
                assert error.value.code=='ml_registry_integrity_failed' and error.value.status==409
                assert 'not-a-lightgbm-model' not in str(error.value) and 'bad0' not in str(error.value)
                assert await session.scalar(select(func.count()).select_from(StockMLExperiment))==0
            # Confirm the saved best-validation prefix in the actual API, not a fake score.
            from app.services.stock_research_v2.ml_train import engine
            lgb,_=engine()
            parsed=lgb.Booster(model_str=native['model']['model_text'])
            assert parsed.params['num_threads']==2
            manifest=native['model']['manifest']
            assert parsed.num_feature()==len(manifest['feature_order'])==2
            assert parsed.feature_name()==['f000','f001']
            assert parsed.current_iteration()==parsed.num_trees()==manifest['trees_used']
            assert parsed.dump_model(num_iteration=1)['objective']=='lambdarank'
