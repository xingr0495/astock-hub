import { useEffect, useRef, useState } from "react"
/** 看板格式化工具（口径对齐 tickflow；个股 change_pct 为小数，指数为百分数）。 */

function finite(v: number | null | undefined): number | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null
  return v
}

/** A 股惯例：强势=红，弱=绿。 */
export function scoreColor(v: number): string {
  if (v >= 70) return "#F04438"
  if (v >= 55) return "#FB923C"
  if (v >= 45) return "#F59E0B"
  if (v >= 30) return "#84CC16"
  return "#12B76A"
}

export function fmtPrice(v: number | null | undefined, digits = 2): string {
  const x = finite(v)
  return x == null ? "—" : x.toFixed(digits)
}

/** 指数涨跌幅：百分数直接显示。 */
export function fmtIndexPct(v: number | null | undefined): string {
  const x = finite(v)
  if (x == null) return "—"
  return `${x >= 0 ? "+" : ""}${x.toFixed(2)}%`
}

/** 个股涨跌幅：小数 ×100。 */
export function fmtStockPct(v: number | null | undefined): string {
  const x = finite(v)
  if (x == null) return "—"
  return `${x >= 0 ? "+" : ""}${(x * 100).toFixed(2)}%`
}

/** 涨跌 class（只看符号，指数/个股通用）。 */
export function pctClass(v: number | null | undefined): string {
  const x = finite(v)
  if (x == null || x === 0) return "text-slate-400"
  return x > 0 ? "text-rose-400" : "text-emerald-400"
}

/** 成交额大数：亿 / 万。 */
export function fmtBigNum(v: number | null | undefined): string {
  const x = finite(v)
  if (x == null) return "—"
  if (x >= 1e8) return `${(x / 1e8).toFixed(2)}亿`
  if (x >= 1e4) return `${(x / 1e4).toFixed(1)}万`
  return x.toFixed(0)
}

/* ---- 数值变化闪烁（第3期）：返回 'flash-up'/'flash-dn'/'' ---- */

export function useFlash(value: string | number | undefined): string {
  const [cls, setCls] = useState("")
  const prev = useRef<string | number | undefined>(value)
  useEffect(() => {
    if (value === undefined || value === null) return
    const before = prev.current
    prev.current = value
    if (before === undefined || before === value) return
    const up = Number(value) >= Number(before)
    setCls(up ? "flash-up" : "flash-dn")
    const t = setTimeout(() => setCls(""), 850)
    return () => clearTimeout(t)
  }, [value])
  return cls
}
