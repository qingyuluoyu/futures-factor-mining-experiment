"""P4 LLM 反馈闭环测试 —— 提示词携带仓位语义/种子文本/上轮冠军反馈"""

from __future__ import annotations

from app.services.factor_lab.llm.llm_factor import _build_messages
from app.services.factor_lab.search import Champion


def _champ(text: str, comp: float, ann: float, to: float) -> Champion:
    return Champion(
        tokens=[0],
        text=text,
        metrics={"ann_ret": ann, "avg_turnover": to},
        composite=comp,
    )


def test_prompt_contains_position_semantics():
    msgs = _build_messages("rb", "1d", 6, None, None, None)
    system = msgs[0]["content"]
    assert "正值=做多" in system and "负值=做空" in system
    assert "恒正" in system  # 恒正结构会被校验淘汰的提示


def test_prompt_seed_rendered_as_text():
    msgs = _build_messages("rb", "1d", 6, [[0]], None, None)
    assert "可参考种子公式" in msgs[1]["content"]
    # 首轮之后不再重复种子（避免每轮都围绕种子收敛）
    msgs2 = _build_messages("rb", "1d", 6, [[0]], None, None, prev_champions=[])
    assert "可参考种子公式" in msgs2[1]["content"]  # 空 champions 仍算首轮形态


def test_prompt_feedback_champions():
    champs = [_champ("公式A", 1.2, 0.15, 0.08), _champ("公式B", 0.9, 0.1, 0.12)]
    msgs = _build_messages("rb", "1d", 6, None, None, None, prev_champions=champs)
    user = msgs[1]["content"]
    assert "公式A" in user and "公式B" in user
    assert "低相关" in user
    assert "年化15%" in user  # ann 0.15 → 15%
