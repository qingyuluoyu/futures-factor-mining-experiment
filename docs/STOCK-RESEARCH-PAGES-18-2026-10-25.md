# 执行18 P2：A股因子三页分工

状态：**已实现待验证**。页面入口与现有业务已接通，目标回归通过；未启动服务、构建、HTTP、PG或本人浏览器验收。总体完成状态由root结合P1/P3/P4和集成结果判定。本包不修改总计划、AGENTS或总接受清单。

## 实际文件与数据流

变更六个既有文件：

- `frontend/src/components/factor-research/stock-research-entry.tsx`及直接测试。
- `frontend/src/components/factor-research/factor-research-workspace.tsx`及直接测试。
- `frontend/src/app/(main)/factor-lab/page.tsx`：只在原股票JSX末尾追加`view="library"`。
- `frontend/src/app/(main)/backtest/page.tsx`：只在原股票JSX末尾追加`view="backtest"`。

新增`stock-factor-library.tsx`及直接测试。其它新增仅本说明、独立日志/备份/SHA/patch脚本。

三个业务工作包：

1. **目录与定义。** Entry新增类型化`view=library|analysis|backtest`。实验室使用独立StockFactorLibrary，读取原内置因子API，展示真实公式、方向、数据来源、方法/规则版本和缺失/排除规则，并挂原StockFactorEditor管理自定义不可变版本。目录错误/空数据分开，手动重试只重新读目录，卸载后晚响应不能回写。不挂研究任务、日期、费用或训练按钮。定义写入继续受原能力/权限/幂等契约控制。
2. **检验与组合。** Entry默认analysis，旧`initialTab`调用仍可决定默认专页；原factor-research路由无需改动。Workspace可选`view:ResearchKind`，专页只渲染本业务表单与报告，不渲染另一tab或编辑器；研究保留IC/IR/衰减/分组等既有分析，回测保留多因子/调仓/持股/权重/基点费用。未传view的旧Workspace继续保留双tab、草稿转换与编辑器兼容。专页之间使用明确链接导航，不把当前未保存草稿伪装成跨路由已保存参数。现有只读本人模型历史保留在因子研究入口，仍需显式打开且不开放训练。
3. **恢复边界。** 三页继续使用原任务服务；未改useResearchTask或`stock-research:${scope}`全局槽。另一kind的最近任务保留状态/取消/查询和全局活动保护，但不自动回填本页日期、预处理、因子或费用，不显示其结果或“恢复当前任务参数”按钮，并给正确页面链接。专页传root新增的`expectedKind`；Workspace自身再次拒绝错kind草稿。Entry的身份stamp和mount key含view，会话/页面职责变化时重建对应内容，避免旧已应用状态滞留。原未知提交幂等重试和并发保护未拆分。

第四包完成一次定向自审和目标验证：核对旧混合兼容、专页实际内容、正确/错误kind恢复、全局活动/不确定任务门禁、目录错误/重试/晚响应及共享路由字节边界。未新造金融计算核。

## 兼容、金融口径与回退

共享路由兼容设计已先写在`docs/superpowers/plans/2026-10-25-stock-pages-data-repair.md`。本包两个路由每个仅一处股票JSX新增属性；撤掉该字符串后，**完整文件字节与原备份完全相同**，包括所有期货分支、函数体、imports和认证待恢复逻辑。直接测试固定原SHA，无需依赖可被清理的日志目录。原市场分发测试未改。

金融方法、源数据、价格/财务/PIT、费用比例API、旧结果、数据库和权限协议均未改；无需迁移，未增加依赖。目录/定义/研究/模型能力读取沿原API，新增数据与模型外部调用0、费用0。原ML本地训练和固定版本自定义研究内部能力保留，尚未接到正式业务训练入口，本包不伪称交付训练产品。

原字节备份及SHA在`logs/stock-research-pages-18-before.json`与同前缀backup目录。`node logs/stock-research-pages-18-evidence.cjs`验证原备份、两个路由精确恢复及全局hook与执行17接受SHA一致，输出`...-evidence.json`和六份独立精确patch。回退应逐patch撤本包差异；只有确认没有后续用户修改时才用整文件备份，不回退root P4或其它agent改动，不删除业务数据。

## 实际验证命令与结果

工作目录`frontend`，红绿阶段实际执行（均`--experimental-strip-types --test`）：

```powershell
node --experimental-strip-types --test src/components/factor-research/factor-research-page.test.mjs src/components/factor-research/stock-research-entry.test.mjs
node --experimental-strip-types --test src/components/factor-research/factor-research-page.test.mjs src/components/factor-research/stock-research-entry.test.mjs src/components/factor-research/stock-factor-library.test.mjs
```

首红37通过/5失败：页面重复、错kind回填、入口无view、路由未追加view；补新library测试后37通过/8失败（新增三项因新组件尚不存在）。实现后45通过/0失败。原红例中错kind用例起初同时含两类参数，先修测试为有效单类参数，再在第二份红日志确认真实错误回填与预检变化，未用无效Schema遮蔽行为。

联合回归命令：

```powershell
node --experimental-strip-types --test src/components/factor-research/factor-research-page.test.mjs src/components/factor-research/stock-research-entry.test.mjs src/components/factor-research/stock-factor-library.test.mjs src/components/factor-research/research-restore-panel.test.mjs src/components/factor-research/use-research-task.test.mjs src/components/factor-research/stock-factor-editor.test.mjs src/components/factor-research/stock-model-library.test.mjs src/components/market/stock-screener-workspace.test.mjs src/components/layout/stock-navigation.test.mjs src/lib/stock-research-api.test.mjs
```

首轮129通过/1失败：既有市场测试要求原`StockResearchEntry initialTab=...`顺序；新属性调整为末尾追加后，**原断言未动，130通过/0失败/0跳过**，见`logs/stock-research-pages-18-targeted-final.log`。最后给library mount key显式追加view后，入口目标6/6再通过，见`...-entry-final.log`。红、首绿、兼容失败原日志均保留，未删/skip/放宽断言。TSC与全前端由root统一集成，本子包未自行声称通过。VM组件/接口回归不代替本人浏览器。

## 剩余风险、人工路径与下一轮

未执行发布构建、真实服务/HTTP/PG、定义写入真实数据库和本人操作；未应用任何迁移。原历史数据资格和完整ML任务接线仍待后续。服务维持停机。

人工路径：A股登录后，实验室应只见公式目录/定义版本；因子研究应只见检验参数与IC类结果；历史回测应只见组合/费用与回测报告。带另一kind最近任务分别进两专页，确认仅显示全局任务状态且本页参数未变，恢复链接导向正确路由；任务运行或提交未知时不能再次创建。历史恢复按P4分别进入相应专页，错入口不能应用。切期货验证原因子实验室及历史回测行为。目录失败可重试、无数据不冒充请求失败。

下一轮三包：root整合页面/恢复/日线/账本回归；在获准运行后完成上述真实身份浏览器及HTTP验收；继续既有固定数据、自定义研究与本地LightGBM正式任务/API/UI链，保持未接通能力关闭。
