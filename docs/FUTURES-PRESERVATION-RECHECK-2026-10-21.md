# 期货保护专项复核

## 结论边界

本轮只读验证，没有修改任何生产代码、配置、数据结构或用户业务状态，没有重启服务、提交/建分支或新增产品模型/上游调用。可以确认冻结后期货/共享核心源码未出现新的改动、所测核心回归通过；不能承诺整个开发历史从未修改期货，更不能把源码未变或测试绿说成所有期货功能端到端成功。

## 三组实际验证

1. **保护文件**：运行`backend/.venv/Scripts/python.exe -X utf8 scripts/check-stock-b23-preservation.py verify logs/futures-preservation-b25-final.json`，1018保护文件通过，无新增/删除/改动。另逐文件SHA256对比最初B15原894文件：无删除，12处变化均为股票API/组件/股票研究工作台及保护检查脚本，不是期货交易/资金/订单/AI任务/回测/共享认证/网关生产逻辑；完整路径清单在`logs/futures-assurance-b15-original-files.json`。检查所有工作台调用方确认FactorResearchWorkspace仅股票StockResearchEntry调用，没有将股票修改直接挂进期货研究。
2. **核心行为**：真实运行15个现有测试文件，194通过、0失败、0跳过，272条既有datetime.utcnow弃用警告。覆盖自定义模拟资金、流水/撮合、盘隔离、交易时间/日历、AI任务多周期和规则、AI工具确认安全、回测账户/信号/分段/止损/合成边界，以及未接通GP不得伪成功的保护。日志`logs/futures-assurance-20261021-tests.log`。这些包含内存/SQLite/边界替身，不等于本轮真实PG、本人登录下单或真实付费供应商验收。
3. **正常运行只读HTTP**：登录页、后端health、normal-runtime、行情桥均200；原main正常生命周期及scheduler运行，桥ok/redis=true。`logs/futures-assurance-20261021-http.json`明确保留health细节：两盘均有67个报价键，但示例rb2610无样本价、桥心跳ok=false，不能据缓存键或health200认定所有合约价格新鲜、所有渠道正常。

实际核心测试命令：

```powershell
backend/.venv/Scripts/python.exe -X utf8 -m pytest backend/tests/test_paper_custom_funds.py backend/tests/test_paper_matching_funds.py backend/tests/test_trading_mode.py backend/tests/test_trading_hours.py backend/tests/test_trading_calendar.py backend/tests/test_ai_trading_mode_isolation.py backend/tests/test_ai_trading_multitf.py backend/tests/test_ai_trading_rules_edit.py backend/tests/test_ai_chat_execution_safety.py backend/tests/test_backtest_account.py backend/tests/test_backtest_runner.py backend/tests/test_backtest_signals.py backend/tests/test_backtest_segments.py backend/tests/test_backtest_synthetic.py backend/tests/test_gp_capability_guard.py -q -rs
```

## 明确未通过的整体承诺

- 超级因子GP checkpoint worker当前false，未接通的新建/恢复入口由现有能力门禁拒绝；不能说超级因子现在能完整运行。
- 模型不能自确认交易，聊天写工具仍需S04持久确认/幂等闭环，现保护没有移除。普通AI对话历史本人成功一次不代表所有自动交易/供应商限流已验收。
- 行情桥心跳、样本合约缺价及启动记录中VVTR同步告警仍须单独核查，不凭健康摘要认证金融数据。
- 本轮未做本人浏览器逐页、实际订单/撤单/平仓/回测创建、真实PG资金事务、付费模型、生产构建或发布回退人工验收。没有以真实业务写入冒充无副作用检查。

## 成本、兼容与人工下一步

本轮只新增本记录及生成验证日志，无生产迁移/金融计算方法/接口变化，新增产品模型/来源请求0，public写0，无服务回退操作需要执行。继续冻结期货核心；若发现真实期货故障，应单独列兼容/回退工作包，不借A股整改直接改动。

下一三包仅为待验收工作：①本人页面登录后逐页读取/市场与盘隔离、资金账本可见性；②授权的隔离PG及模拟订单/撤单/平仓/回测完整生命周期；③单独处理GP未接通、行情时效和AI确认等已知缺口，并在明确范围/预算下执行模型验证。涉及本人模拟资金变化或恢复收费AI任务须事先确认；不需要在聊天发送密码或密钥。整体仍部分完成。
