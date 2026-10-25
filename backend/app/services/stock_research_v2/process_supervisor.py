import asyncio
import math
from subprocess import Popen

from pydantic import ValidationError

from app.schemas.stock_research_v2 import DatasetRef
from app.schemas.stock_columnar import ColumnarRef
from app.services.stock_research_jobs import JobError
from app.services.stock_research_worker import JobStopped, ProcessExecutionError, stop_process, supervise_process


async def supervise_dataset_process(process: Popen, *, heartbeat, timeout_seconds: float, columnar=False) -> DatasetRef | ColumnarRef | None:
    async def renew():
        try:
            return await heartbeat() is True
        except Exception:
            raise JobError("worker_failed", "观察执行器续租失败，未保存完成结果", 500) from None

    async def reject_snapshot(manifest):
        raise JobError("dataset_invalid", "观察执行器不接受额外控制消息", 422)

    try:
        if type(columnar) is not bool:
            raise JobError("dataset_invalid", "观察执行器模式必须明确", 422)
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
            raise JobError("invalid_execution_budget", "观察任务运行时间预算无效", 422)
        payload = await supervise_process(process, heartbeat=renew, snapshot=reject_snapshot, timeout_seconds=timeout_seconds, max_message_bytes=4096,
            timeout_message="观察任务超时，子进程已终止，不自动重试")
        reference = ColumnarRef.model_validate(payload) if columnar else DatasetRef.model_validate(payload)
        fixed = reference.dataset if columnar else reference
        if fixed.qualification != "observation_only":
            raise JobError("dataset_invalid", "观察任务不能声明研究或可成交资格", 422)
        return reference
    except JobStopped:
        return None
    except ProcessExecutionError as error:
        if error.code == "execution_timeout":
            raise JobError("execution_timeout", "观察任务超时，子进程已终止，不自动重试", 504) from None
        if error.code in {"worker_protocol", "result_too_large"}:
            raise JobError("dataset_invalid", "观察执行器返回格式或大小无效", 422) from None
        raise JobError("worker_failed", "观察执行器失败，未保存完成结果", 500) from None
    except ValidationError:
        raise JobError("dataset_invalid", "观察数据引用未通过结构验证", 422) from None
    except JobError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception:
        raise JobError("worker_failed", "观察执行器失败，未保存完成结果", 500) from None
    finally:
        await stop_process(process)
        if process.stdout:
            process.stdout.close()
        if process.stdin:
            process.stdin.close()
