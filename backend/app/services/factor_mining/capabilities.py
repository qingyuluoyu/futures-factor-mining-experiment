from app.services.factor_lab.checkpoint import VERSION


def checkpoint_worker_connected() -> bool:
    return False


def checkpoint_capability() -> dict:
    return {"accepting": checkpoint_worker_connected(), "exact_resume_available": False,
            "checkpoint_kernel_version": VERSION,
            "reason": "生产任务执行器尚未接通检查点、固定输入和登录盘隔离；旧任务接口暂不开放，记录保留且不推断历史所属盘。"}
