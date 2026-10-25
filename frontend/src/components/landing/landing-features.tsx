"use client"

const FEATURES = [
  {
    title: "K 线多空时机",
    desc: "行情图叠加多空标记与关键位，帮助投资者更明确买卖时机，而不是只看数字涨跌。",
    tag: "Signal",
  },
  {
    title: "AI 浮动分析",
    desc: "右下角浮窗助手：点开即可分析当前品种，输出支撑阻力、趋势研判与操作建议。",
    tag: "Assist",
  },
  {
    title: "AI 自由交易",
    desc: "绑定自有大模型，按周期自主决策开平仓；资金仓、风格与持仓天数约束风险。",
    tag: "AI",
  },
  {
    title: "AI 量化策略",
    desc: "双均线、N 日突破等规则策略与 AI 同场：收益曲线 + 横向总收益柱对比。",
    tag: "Quant",
  },
  {
    title: "实盘 / 虚拟盘",
    desc: "登录即选盘：实盘对接行情通道，虚拟盘 7×24，周末也能验证策略。",
    tag: "Market",
  },
  {
    title: "回测与预警",
    desc: "历史回测验证参数；站内消息与价格预警，关键节点不错过。",
    tag: "Risk",
  },
] as const

const STEPS = [
  { n: "01", title: "登录选盘", desc: "实盘或虚拟盘，JWT 绑定交易模式" },
  { n: "02", title: "配置模型", desc: "接入 Provider / 模型与资金仓" },
  { n: "03", title: "创建任务", desc: "AI 或量化策略，设定周期与风控" },
  { n: "04", title: "实时监督", desc: "曲线、总收益柱、持仓与决策流水" },
] as const

/** 产品介绍 + 交易闭环 */
export function LandingFeatures(): React.JSX.Element {
  return (
    <>
      <section id="features" className="relative py-14 md:py-20">
        <div className="mx-auto max-w-7xl px-4 md:px-6">
          <div className="flex items-end justify-between gap-4 mb-8 flex-wrap">
            <div>
              <p className="text-[11px] tracking-[0.2em] uppercase text-red-400/90 mb-2">
                Product
              </p>
              <h2 className="text-2xl md:text-3xl font-semibold text-[var(--text-primary)]">
                为专业交易打造的 AI 工作台
              </h2>
              <p className="mt-2 text-sm text-[var(--text-secondary)] max-w-xl">
                从行情、决策、下单到风控与复盘，一条链路完成；首页看板展示正在运行的策略收益。
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 md:gap-4">
            {FEATURES.map((f) => (
              <article
                key={f.title}
                className="group relative rounded-2xl border border-[var(--border)] bg-gradient-to-b from-[var(--bg-tertiary)] to-transparent p-5 overflow-hidden hover:border-red-500/35 transition-colors duration-200"
              >
                <div className="absolute -right-6 -top-6 h-24 w-24 rounded-full bg-red-500/10 blur-2xl group-hover:bg-red-500/20" />
                <span className="inline-flex text-[10px] font-mono tracking-wider text-red-300/90 border border-red-500/25 rounded px-1.5 py-0.5 bg-red-500/10">
                  {f.tag}
                </span>
                <h3 className="mt-3 text-base font-semibold text-[var(--text-primary)]">
                  {f.title}
                </h3>
                <p className="mt-2 text-xs leading-relaxed text-[var(--text-secondary)]">
                  {f.desc}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="workflow" className="relative pb-16 md:pb-24">
        <div className="mx-auto max-w-7xl px-4 md:px-6">
          <p className="text-[11px] tracking-[0.2em] uppercase text-red-400/90 mb-2">
            Workflow
          </p>
          <h2 className="text-2xl md:text-3xl font-semibold text-[var(--text-primary)] mb-8">
            四步进入交易闭环
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {STEPS.map((s, i) => (
              <div
                key={s.n}
                className="relative rounded-2xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-5"
              >
                <span className="font-num text-3xl font-bold text-red-500/25">
                  {s.n}
                </span>
                <h3 className="mt-2 text-sm font-semibold text-[var(--text-primary)]">
                  {s.title}
                </h3>
                <p className="mt-1 text-xs text-[var(--text-muted)]">{s.desc}</p>
                {i < STEPS.length - 1 && (
                  <div className="hidden lg:block absolute top-1/2 -right-2 w-4 h-px bg-gradient-to-r from-red-500/50 to-transparent" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  )
}
