# 闻苑品牌更新交付（2026-10-19）

## 状态、设计与数据流

本轮三个工作包W01标识、W02入口统一、W03验证均完成代码与本地验证。仅品牌UI更新，不代表整个金融系统完成或生产上线。

产品标题精确为“闻苑”。Logo为原创代码绘制的矢量方印：墨绿色底、白色门庭与展开书页、淡金色行情折线，呼应“闻”的求知与“苑”的汇聚。无外部图源、字体或远端请求；32/44/56px界面共用SVG，favicon同源SVG，Apple180px PNG由同一矢量导出。

`lib/brand.ts`统一名称/副标题/描述→Metadata/导航/认证入口；`BrandLogo`统一图标，独立可访问名称或装饰性空alt。更新首页页脚和登录注册弹窗，注册页不再将旧后台system_name误当品牌。既有数据库管理设置、自定义实例名、浏览器存储键和历史截图保留，不做数据回填。

窄屏真实浏览器初次验收发现旧顶栏隐藏标题且状态竖排；补反例后让品牌始终可见且不换行，窄屏隐藏次要状态和盘徽标，仍保留市场切换、搜索/主题/消息/用户操作。没有修改股票看板或整站响应式架构。

## 实际文件

- 新 `frontend/public/wenyuan-mark.svg`、`frontend/public/wenyuan-apple.png`
- 新 `frontend/src/lib/brand.ts`、`frontend/src/components/common/brand-logo.tsx`、`brand-logo.test.mjs`
- `frontend/src/app/layout.tsx`、`frontend/src/app/page.tsx`
- `frontend/src/app/(auth)/login/page.tsx`、`frontend/src/app/(auth)/register/page.tsx`
- `frontend/src/components/layout/top-navbar.tsx`
- `frontend/src/components/landing/landing-header.tsx`、`frontend/src/components/landing/auth/auth-dialog.tsx`
- `frontend/src/components/landing/auth/registration-forms.test.mjs`：VM夹具增加真实品牌常量/组件的编译加载，保留全部注册和会话断言。
- 子计划、本记录及主计划检查点；`logs/wenyuan-logo-preview.png`、`logs/wenyuan-dashboard.png`为渲染/实际UI验收图。

## 实际命令与结果

1. 前端目录 `node --test src/components/common/brand-logo.test.mjs`：初始缺标识/常量/组件与旧名称反例失败（`logs/wenyuan-brand-red.log`），实施后4项通过；追加窄屏标题反例先失败（`mobile-red.log`）后通过。
2. PNG导出初次 `import sharp from 'sharp'`失败：pnpm未在顶层暴露该包，不安装新依赖；改用已锁定本地包 `./node_modules/.pnpm/sharp@0.34.5/node_modules/sharp/lib/index.js`。实际命令 `node --input-type=module -e "import sharp from './node_modules/.pnpm/sharp@0.34.5/node_modules/sharp/lib/index.js'; await sharp('public/wenyuan-mark.svg').resize(180,180).png().toFile('public/wenyuan-apple.png'); await sharp('public/wenyuan-mark.svg').resize(256,256).png().toFile('../logs/wenyuan-logo-preview.png')"` 退出0；已打开256px图检查几何/配色。
3. 前端目录 `node --import ../logs/node-alias-register.mjs --test (Get-ChildItem -Recurse src -Filter *.test.mjs | ForEach-Object FullName)`：初次5项注册页测试因VM未提供新组件导入失败，不是产品注册业务失败；扩展夹具加载真实标识后，最终**420 passed、0 failed/skip**，`logs/wenyuan-brand-node-final.log`。没有删除、跳过或放宽注册断言。
4. 前端目录 `node node_modules/typescript/bin/tsc --noEmit --incremental false`：**退出0**，`logs/wenyuan-brand-tsc-final.log`。
5. PowerShell `Invoke-WebRequest` 对 `/`、`/login`、`/register`、`/dashboard` 检查HTTP200、title精确“闻苑”、含本地Logo路径；SVG/PNG资源200且MIME分别image/svg+xml与image/png，`logs/wenyuan-brand-http.json`。最后附带的旧品牌`rg`搜索未命中，返回1表示无匹配，不是HTTP失败。
6. 当前IAB已登录A股工作台：AX标题/可见品牌“闻苑”，截图确认Logo、名称、市场切换和导航均在；小视口约418px。修窄屏前后真实截图检查，无市场切换/登录/注册/交易提交、无凭据读取。最终图 `logs/wenyuan-dashboard.png`。当前看板原有盘后同步失败状态保持原样，未用品牌修改掩盖。

未执行：新的完整发布构建、生产部署、桌面大屏和认证弹窗的全套浏览器交互、后端/PG回归（本轮未改后端/DB/金融/权限，按低风险UI验证分层）。此前聊天批次的全量后端研究任务单项失败仍在原记录，不被本次前端绿替代。

## 兼容、成本、回退、人工路径

无数据库迁移、无依赖升级、无后端重启、无新付费模型/行情或图像生成调用；前端开发服务热更新。所有账户/资金/行情来源/方法版本/会话/接口不变。

回退为恢复本批前端品牌导入、资源及Metadata；保留原存储键，不需要清理会话或回滚数据。品牌资产是本地公开静态文件，不含用户信息。上线前如需商标注册或品牌独占性仍需独立核查，本轮不提供商标无冲突保证。

人工验证：刷新当前工作台→左上“闻苑”及门庭书页图标；浏览器标签/收藏图标同品牌。打开首页、登录、注册入口均应同标识；窄屏标题不再隐藏或竖排。浏览器若缓存旧favicon，可重新打开标签页，不要清空登录凭据。

下一轮三包仍按原产品优先级：①本人登录场景的有界AI回复与速率核对；②真实期货数据/GP检查点演化；③独立期货研究与现有研究任务排序反例审查。品牌需求本轮不扩大为这些金融功能的伪完成。
