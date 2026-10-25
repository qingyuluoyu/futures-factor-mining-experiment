"use client"

/**
 * 科技网格动态背景 —— 黑灰底 + 红色辉光扫描
 */

/** 全屏固定科技网格背景（pointer-events-none） */
export function TechGridBg(): React.JSX.Element {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-0 overflow-hidden"
    >
      {/* 深空底 */}
      <div className="absolute inset-0 bg-[var(--bg-primary)]" />

      {/* 径向红光 */}
      <div
        className="absolute inset-0 opacity-80"
        style={{
          background:
            "radial-gradient(ellipse 80% 50% at 50% -10%, rgba(220,38,38,0.22), transparent 55%), radial-gradient(ellipse 60% 40% at 90% 80%, rgba(239,68,68,0.08), transparent 50%), radial-gradient(ellipse 50% 35% at 10% 70%, rgba(120,120,130,0.12), transparent 50%)",
        }}
      />

      {/* 主网格 */}
      <div className="landing-grid absolute inset-0 opacity-[0.35]" />

      {/* 细密点阵 */}
      <div className="landing-dots absolute inset-0 opacity-40" />

      {/* 水平扫描线 */}
      <div className="landing-scan absolute left-0 right-0 h-24 opacity-40" />

      {/* 底部渐隐，避免与内容抢戏 */}
      <div className="absolute inset-x-0 bottom-0 h-40 bg-gradient-to-t from-[var(--bg-primary)] to-transparent" />
      <div className="absolute inset-x-0 top-0 h-28 bg-gradient-to-b from-[var(--bg-primary)]/90 to-transparent" />
    </div>
  )
}
