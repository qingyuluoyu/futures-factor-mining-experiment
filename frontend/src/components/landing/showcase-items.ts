/**
 * 首页核心功能轮播数据
 */

export interface ShowcaseItem {
  id: string
  tag: string
  title: string
  subtitle: string
  points: string[]
  image: string
  alt: string
}

export const SHOWCASE_ITEMS: ShowcaseItem[] = [
  {
    id: "kline",
    tag: "K 线 · 多空信号",
    title: "K 线图上直接标注多空",
    subtitle:
      "在行情 K 线上叠加多空标记与关键位提示，买卖时机一眼可辨，减少对着数字猜方向。",
    points: [
      "红多 / 绿空标记贴近 K 线，进出点更直观",
      "自选合约 + 多周期切换，适配日内与波段",
      "指标参数可调，辅助确认趋势与转折",
    ],
    image: "/showcase/08-market.png",
    alt: "行情页 K 线与多空信号",
  },
  {
    id: "ai-float",
    tag: "AI 浮动分析",
    title: "浮窗助手即时解读品种",
    subtitle:
      "右下角 AI 交易助手可随时展开：指定合约与周期后，自动调取行情与指标，输出支撑阻力与操作建议。",
    points: [
      "调用快照 / 均线 / MACD / 布林等工具再下结论",
      "给出关键价位表与多空倾向，附止损参考",
      "浮窗可拖动缩放，边看 K 线边咨询",
    ],
    image: "/showcase/11-ai-float.png",
    alt: "AI 浮动助手分析螺纹钢走势",
  },
  {
    id: "ai-free",
    tag: "AI 自由交易",
    title: "大模型自主决策开平仓",
    subtitle:
      "绑定自有模型 API，按 K 线周期阅读行情上下文，在资金仓与风控约束下自主交易。",
    points: [
      "激进 / 稳健 / 保守风格与最长持仓天数",
      "资金仓额度 + 使用比例，控制单策略风险",
      "决策流水与成交可追溯，开盘自动恢复",
    ],
    image: "/showcase/06-ai-trading.png",
    alt: "AI 交易收益对比与总收益柱",
  },
  {
    id: "ai-quant",
    tag: "AI 量化策略",
    title: "规则策略与 AI 同场竞技",
    subtitle:
      "双均线、N 日突破等量化任务与 AI 任务共用看板：曲线对比 + 一任务一柱总收益。",
    points: [
      "九宫格任务卡片，持仓浮盈实时刷新",
      "横向总收益柱，任务间盈亏横向可比",
      "回测报告验证参数后再上线运行",
    ],
    image: "/showcase/07-ai-tasks.png",
    alt: "AI / 量化任务列表九宫格",
  },
  {
    id: "trade",
    tag: "交易终端",
    title: "下单、持仓与盘口一体",
    subtitle:
      "专业交易台：盘口、委托、持仓与行情联动，实盘 / 虚拟盘登录即隔离。",
    points: [
      "快速下单与确认流程，适配短线操作",
      "持仓浮盈按最新价重算，不含手续费串扰",
      "站内消息 + 价格预警，关键节点不错过",
    ],
    image: "/showcase/09-trading.png",
    alt: "交易下单与盘口界面",
  },
  {
    id: "backtest",
    tag: "策略回测",
    title: "上线前用历史 K 线验一遍",
    subtitle:
      "对 AI / 量化参数做历史回放，看收益曲线与关键指标，再决定是否实盘运行。",
    points: [
      "多周期历史数据回测",
      "报告指标清晰，便于对比参数",
      "与在线任务配置一脉相承",
    ],
    image: "/showcase/10-backtest.png",
    alt: "策略回测配置页",
  },
  {
    id: "dashboard",
    tag: "总览看板",
    title: "资产、信号与 AI 一屏掌控",
    subtitle:
      "登录后工作台汇总资产、持仓与 AI 运行概况，从总览一键进入各业务模块。",
    points: [
      "资产与持仓摘要",
      "信号广播与 AI 交易入口",
      "暗色专业终端体验",
    ],
    image: "/showcase/05-dashboard.png",
    alt: "系统总览看板",
  },
]

export const SHOWCASE_AUTO_MS = 5000
