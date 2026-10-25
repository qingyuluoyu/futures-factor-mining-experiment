"use client"

/**
 * 首页核心功能实拍 —— 轮播切换（大图 + 介绍）
 */

import Image from "next/image"
import { useCallback, useEffect, useState } from "react"
import { SHOWCASE_AUTO_MS, SHOWCASE_ITEMS } from "./showcase-items"

/** 核心功能轮播：大图 + 右侧介绍，自动切换 */
export function LandingShowcase(): React.JSX.Element {
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const total = SHOWCASE_ITEMS.length
  const active = SHOWCASE_ITEMS[index] ?? SHOWCASE_ITEMS[0]

  const go = useCallback(
    (next: number) => {
      const n = ((next % total) + total) % total
      setIndex(n)
    },
    [total],
  )

  useEffect(() => {
    if (paused) return
    const timer = setInterval(() => {
      setIndex((i) => (i + 1) % total)
    }, SHOWCASE_AUTO_MS)
    return () => clearInterval(timer)
  }, [paused, total])

  return (
    <section id="showcase" className="relative py-12 md:py-16">
      <div className="mx-auto max-w-7xl px-4 md:px-6">
        <div className="mb-8 max-w-2xl">
          <p className="text-[11px] tracking-[0.2em] uppercase text-red-400/90 mb-2">
            Core Features
          </p>
          <h2 className="text-2xl md:text-3xl font-semibold text-[var(--text-primary)]">
            核心功能实拍展示
          </h2>
          <p className="mt-2 text-sm text-[var(--text-secondary)] leading-relaxed">
            轮播切换真实页面：K 线多空时机、AI 浮动分析、自由交易与量化同场。
          </p>
        </div>

        <div className="flex flex-wrap gap-2 mb-6">
          {SHOWCASE_ITEMS.map((item, i) => {
            const on = i === index
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setIndex(i)}
                className={`rounded-full px-3 py-1.5 text-[11px] border transition-colors duration-200 ${
                  on
                    ? "border-red-500/50 bg-red-500/20 text-red-100"
                    : "border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-muted)] hover:border-[var(--text-muted)] hover:text-[var(--text-primary)]"
                }`}
              >
                {item.tag}
              </button>
            )
          })}
        </div>

        <div
          className="grid grid-cols-1 lg:grid-cols-[1.15fr_0.85fr] gap-4 lg:gap-6 items-stretch"
          onMouseEnter={() => setPaused(true)}
          onMouseLeave={() => setPaused(false)}
        >
          <div className="relative rounded-2xl border border-[var(--border)] bg-[var(--bg-tertiary)] overflow-hidden shadow-[0_0_40px_rgba(239,68,68,0.08)] group">
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-red-500/60 to-transparent z-10" />
            <div className="relative aspect-[16/10] bg-[var(--bg-secondary)]">
              <Image
                key={active.id}
                src={active.image}
                alt={active.alt}
                fill
                className="object-cover object-top"
                sizes="(max-width: 1024px) 100vw, 60vw"
                priority
              />
              <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/55 via-transparent to-black/20" />
            </div>
            <div className="absolute left-3 bottom-3 right-3 flex items-center justify-between gap-2 z-10">
              <span className="text-[10px] px-2 py-1 rounded-full border border-red-500/35 bg-[var(--bg-tertiary)] text-[var(--accent-up)] backdrop-blur-sm">
                {active.tag}
              </span>
              <span className="text-[10px] text-[var(--text-secondary)] font-num bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                {index + 1} / {total}
              </span>
            </div>

            <button
              type="button"
              aria-label="上一张"
              onClick={() => go(index - 1)}
              className="absolute left-2 top-1/2 -translate-y-1/2 z-20 h-9 w-9 rounded-full border border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] hover:border-red-400/40 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
            >
              ‹
            </button>
            <button
              type="button"
              aria-label="下一张"
              onClick={() => go(index + 1)}
              className="absolute right-2 top-1/2 -translate-y-1/2 z-20 h-9 w-9 rounded-full border border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] hover:border-red-400/40 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
            >
              ›
            </button>

            <div className="absolute bottom-0 inset-x-0 h-0.5 bg-[var(--border)] z-20">
              <div
                key={`${active.id}-bar`}
                className="h-full bg-red-500/80 landing-carousel-progress"
                style={{
                  animationDuration: `${SHOWCASE_AUTO_MS}ms`,
                  animationPlayState: paused ? "paused" : "running",
                }}
              />
            </div>
          </div>

          <div className="rounded-2xl border border-[var(--border)] bg-gradient-to-b from-[var(--bg-tertiary)] to-transparent p-5 md:p-6 flex flex-col">
            <h3 className="text-xl font-semibold text-[var(--text-primary)] leading-snug">
              {active.title}
            </h3>
            <p className="mt-3 text-sm text-[var(--text-secondary)] leading-relaxed">
              {active.subtitle}
            </p>
            <ul className="mt-5 space-y-2.5 flex-1">
              {active.points.map((p) => (
                <li
                  key={p}
                  className="flex items-start gap-2 text-xs text-[var(--text-primary)]/90"
                >
                  <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-red-400 shrink-0 shadow-[0_0_8px_rgba(248,113,113,0.8)]" />
                  <span className="leading-relaxed">{p}</span>
                </li>
              ))}
            </ul>
            <p className="mt-5 text-[11px] text-[var(--text-muted)] border-t border-[var(--border)] pt-3">
              登录后即可在对应模块完整体验；首页上方看板持续展示运行中任务的实时收益。
            </p>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-center gap-1.5">
          {SHOWCASE_ITEMS.map((item, i) => (
            <button
              key={item.id}
              type="button"
              aria-label={`切换到 ${item.tag}`}
              onClick={() => setIndex(i)}
              className={`h-1.5 rounded-full transition-all duration-200 ${
                i === index
                  ? "w-6 bg-red-400"
                  : "w-1.5 bg-[var(--text-muted)] hover:bg-[var(--text-secondary)]"
              }`}
            />
          ))}
        </div>

        <div className="mt-3 flex gap-2 overflow-x-auto landing-showcase-track pb-1">
          {SHOWCASE_ITEMS.map((item, i) => {
            const on = i === index
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setIndex(i)}
                className={`relative shrink-0 w-[112px] rounded-lg overflow-hidden border text-left transition-colors duration-200 ${
                  on
                    ? "border-red-500/50 ring-1 ring-red-500/30"
                    : "border-[var(--border)] hover:border-[var(--text-muted)]"
                }`}
              >
                <div className="relative aspect-[16/10] bg-[var(--bg-tertiary)]">
                  <Image
                    src={item.image}
                    alt={item.alt}
                    fill
                    className="object-cover object-top opacity-90"
                    sizes="112px"
                  />
                </div>
              </button>
            )
          })}
        </div>
      </div>
    </section>
  )
}
