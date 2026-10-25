from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.stock_research_jobs import JobError
from tests.test_stock_study_inputs import archived_inputs
from tests.test_stock_ml_dataset import dataset
from tests.test_stock_ml_split import plan


def market(data, start, end):
    dates=[day.date() for day in data["close"].index]
    evidence={name:"qa-complete-not-live-certified" for name in ["calendar_reference","raw_prices_reference","status_and_limits_reference","corporate_actions_reference","delistings_reference","historical_universe_reference"]}
    evidence.update(coverage_start=str(start),coverage_end=str(end))
    bars=[]
    for day in dates:
        if not start <= day <= end: continue
        for code in data["close"].columns:
            close=Decimal(str(data["close"].loc[str(day),code])).quantize(Decimal('.01'))
            bars.append({"instrument_id":code,"trade_date":str(day),"open":str(close),"close":str(close),"high":str(close+Decimal('.01')),"low":str(close-Decimal('.01')),
                "volume":"1000000","volume_unit":"share","adj_factor":"1","status":"trading","up_limit":str(close+1),"down_limit":str(close-1),"evidence_reference":"qa-raw-bar-only"})
    return {"calendar":[str(day) for day in dates],"bars":bars,"evidence":evidence,"events":[]}


@pytest.mark.asyncio
async def test_real_ranker_candidates_use_same_dated_portfolio_ledger_and_future_status_does_not_reselect(tmp_path):
    from app.services.stock_research_v2.ml_predict import run_walk_forward_experiment
    from app.services.stock_research_v2.ml_portfolio import run_ml_portfolio
    from app.services.stock_research_v2.artifacts import ArtifactStore
    from app.schemas.stock_portfolio_v2 import PortfolioRequest
    async with archived_inputs(tmp_path) as (registry,receipt,calendar,universe,data,other):
        samples,_,_,_=dataset()
        args={"scope":registry.scope,"dataset":receipt.dataset,"feature_versions":{"quality":"b"*64,"value":"c"*64},"qa_only":True,
            "parameters":{"n_estimators":15,"max_depth":3,"num_leaves":7,"min_child_samples":5}}
        store=registry.archive.require_store()
        experiment=run_walk_forward_experiment(samples,plan(data,fold_count=2),**args,store=store)
        start=date.fromisoformat(experiment["folds"][0]["split"]["test_start"])
        end=data["close"].index[68].date()
        request=PortfolioRequest(scope=registry.scope,dataset=receipt.dataset,pool_version=universe.pool_version,start=start,end=end,top_n=5,rebalance="explicit",initial_cash="10000",qa_only=True)
        prices=market(data,start,end)
        result=await run_ml_portfolio(registry.archive,experiment["saved_report"]["sha256"],request,prices,request_id="a"*32)
        assert result["portfolio"]["status"] == "completed"
        assert result["portfolio"]["validation_status"] == "qa_only"
        assert result["portfolio"]["ledger"] and len(result["prediction_sources"])==2
        assert all(Decimal(row["cash"]) >= 0 for row in result["portfolio"]["daily"])
        assert store.read_json(result["saved_report"]["sha256"])["portfolio"] == result["portfolio"]
        selected=result["portfolio"]["targets"][0]["instrument_id"]
        first_execution=result["portfolio"]["targets"][0]["execution_date"]
        future=deepcopy(prices)
        for bar in future["bars"]:
            if bar["instrument_id"]==selected and bar["trade_date"]==first_execution:
                bar["status"]="suspended"
        rejected=await run_ml_portfolio(registry.archive,experiment["saved_report"]["sha256"],request,future,request_id="b"*32)
        assert [row for row in result["portfolio"]["targets"] if row["decision_date"]==str(start)] == [row for row in rejected["portfolio"]["targets"] if row["decision_date"]==str(start)]
        assert any(row["reason"]=="suspended" for row in rejected["portfolio"]["executions"])
        with pytest.raises(JobError):
            await run_ml_portfolio(registry.archive,experiment["saved_report"]["sha256"],request.model_copy(update={"scope":registry.scope.model_copy(update={"owner_id":other})}),prices,request_id="c"*32)
        bad=store.read_json(experiment["saved_report"]["sha256"])
        bad["report"]["folds"][0]["predictions"][0]["score"] += 3
        bad_ref=store.put_json(bad)
        with pytest.raises(JobError) as corrupted:
            await run_ml_portfolio(registry.archive,bad_ref.sha256,request,prices,request_id="d"*32)
        assert corrupted.value.code == "ml_portfolio_integrity_failed"
        with pytest.raises(JobError) as cap:
            await run_ml_portfolio(registry.archive,experiment["saved_report"]["sha256"],request.model_copy(update={"weight":"mktcap"}),prices,request_id="e"*32)
        assert cap.value.code == "ml_marketcap_required"
        with pytest.raises(JobError) as qa_gate:
            await run_ml_portfolio(registry.archive,experiment["saved_report"]["sha256"],request.model_copy(update={"qa_only":False}),prices,request_id="f"*32)
        assert qa_gate.value.code == "ml_model_not_qualified"
