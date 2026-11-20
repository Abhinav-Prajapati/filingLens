import type { EChartsOption } from 'echarts'
import { fmtAxis, fmtChange, fmtCompact, fmtValue, isPctUnit } from '../lib/format'
import type { Tokens } from '../lib/theme'

/** A plotted series: identity (label + colour) and its points. Values may be null (gap). */
export interface S { key: string; label: string; color: string; points: { period: string; value: number | null }[] }

const EASE = 'cubicOut' as const
const DUR = 900

// ---- shared chrome ------------------------------------------------------------------------
function base(t: Tokens): EChartsOption {
  return {
    backgroundColor: 'transparent',
    textStyle: { fontFamily: t.sans, color: t.ink2 },
    animationDuration: DUR,
    animationEasing: EASE,
    animationDurationUpdate: 600,
    animationEasingUpdate: 'cubicInOut',
  }
}

function tooltip(t: Tokens, extra: Record<string, unknown> = {}) {
  return {
    backgroundColor: t.surface,
    borderColor: t.rule,
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: t.ink, fontFamily: t.sans, fontSize: 12 },
    extraCssText: `border-radius:${t.radius}px;box-shadow:${t.shadow};`,
    ...extra,
  }
}

const row = (color: string, name: string, value: string, shape: 'sq' | 'ln' = 'sq') =>
  `<div style="display:flex;align-items:center;gap:8px;justify-content:space-between;min-width:168px;line-height:2">` +
  `<span style="display:inline-flex;align-items:center;gap:8px"><i style="display:inline-block;width:${shape === 'ln' ? 12 : 8}px;height:${shape === 'ln' ? 2 : 8}px;border-radius:${shape === 'ln' ? 2 : 4}px;background:${color}"></i>${name}</span>` +
  `<b style="font-weight:600;font-variant-numeric:tabular-nums">${value}</b></div>`

const head = (t: Tokens, s: string) =>
  `<div style="font-size:12px;color:${t.muted};margin-bottom:4px">${s}</div>`

function catAxis(t: Tokens, data: string[]) {
  return {
    type: 'category' as const, data,
    axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false },
    axisLabel: { color: t.muted, fontFamily: t.mono, fontSize: 12, margin: 12 },
  }
}

function valAxis(t: Tokens, unit: string, extra: Record<string, unknown> = {}) {
  return {
    type: 'value' as const,
    splitNumber: 4,
    axisLabel: { color: t.muted, fontFamily: t.mono, fontSize: 12, formatter: (v: number) => fmtAxis(v, unit) },
    splitLine: { lineStyle: { color: t.grid, width: 1 } },
    axisLine: { show: false }, axisTick: { show: false },
    ...extra,
  }
}

const periodsOf = (series: S[]) => {
  const seen: string[] = []
  series.forEach((s) => s.points.forEach((p) => { if (!seen.includes(p.period)) seen.push(p.period) }))
  return seen
}

const valuesOn = (s: S, periods: string[]) => periods.map((p) => s.points.find((x) => x.period === p)?.value ?? null)

function axisTip(t: Tokens, unit: string, shape: 'sq' | 'ln') {
  return tooltip(t, {
    trigger: 'axis',
    axisPointer: shape === 'ln'
      ? { type: 'line', lineStyle: { color: t.axis, width: 1 } }
      : { type: 'shadow', shadowStyle: { color: t.dark ? 'rgba(255,255,255,0.04)' : 'rgba(0,0,0,0.035)' } },
    formatter: (ps: { axisValueLabel: string; color: string; seriesName: string; value: number | null }[]) =>
      head(t, ps[0]?.axisValueLabel ?? '') +
      ps.filter((p) => !String(p.seriesName).startsWith('_'))
        .map((p) => row(p.color, p.seriesName, fmtValue(p.value, unit), shape)).join(''),
  })
}

// ---- line / area --------------------------------------------------------------------------
export function lineOption(t: Tokens, series: S[], unit: string, opts: { area?: boolean; endLabels?: boolean } = {}): EChartsOption {
  const periods = periodsOf(series)
  const single = series.length === 1
  const area = opts.area ?? single
  return {
    ...base(t),
    grid: { left: 8, right: opts.endLabels !== false ? 24 + 7 * Math.max(6, ...series.map((s) => (single ? 9 : s.label.length))) : 16, top: 22, bottom: 8, containLabel: true },
    tooltip: axisTip(t, unit, 'ln'),
    xAxis: { ...catAxis(t, periods), boundaryGap: false },
    yAxis: valAxis(t, unit, { scale: !isPctUnit(unit) || !single }),
    series: series.map((s) => {
      const data = valuesOn(s, periods)
      const lastIdx = data.reduce<number>((acc, v, i) => (v != null ? i : acc), -1)
      return {
        type: 'line' as const, name: s.label, data, connectNulls: false,
        symbol: 'circle', symbolSize: 8, showSymbol: periods.length <= 16,
        lineStyle: { width: 2, color: s.color, cap: 'round', join: 'round' },
        itemStyle: { color: s.color, borderColor: t.surface, borderWidth: 2 },
        emphasis: { focus: 'series' as const, scale: 1.4 },
        areaStyle: area ? {
          color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
            { offset: 0, color: hexA(s.color, single ? 0.2 : 0.1) }, { offset: 1, color: hexA(s.color, 0) }] },
        } : undefined,
        endLabel: opts.endLabels !== false ? {
          show: true, color: t.ink2, fontFamily: t.mono, fontSize: 12,
          formatter: () => (single ? fmtCompact(data[lastIdx], unit) : s.label),
        } : undefined,
        labelLayout: { moveOverlap: 'shiftY' as const },
        animationDuration: 1300,
      }
    }),
  } as EChartsOption
}

// ---- bars (one or many series) --------------------------------------------------------------
export function barOption(t: Tokens, series: S[], unit: string): EChartsOption {
  const periods = periodsOf(series)
  const single = series.length === 1
  return {
    ...base(t),
    grid: { left: 8, right: 12, top: 26, bottom: 8, containLabel: true },
    tooltip: axisTip(t, unit, 'sq'),
    xAxis: catAxis(t, periods),
    yAxis: valAxis(t, unit),
    series: series.map((s, si) => ({
      type: 'bar' as const, name: s.label, data: valuesOn(s, periods),
      barMaxWidth: 24, barGap: '18%', barCategoryGap: single ? '45%' : '28%',
      itemStyle: { color: s.color, borderRadius: [4, 4, 0, 0] },
      emphasis: { focus: 'series' as const },
      label: single && periods.length <= 10 ? {
        show: true, position: 'top' as const, color: t.ink2, fontFamily: t.mono, fontSize: 12,
        formatter: (p: { value: number | null }) => fmtCompact(p.value, unit),
      } : undefined,
      animationDelay: (i: number) => i * 70 + si * 120,
    })),
  } as EChartsOption
}

// ---- change bars (diverging around zero) ------------------------------------------------------
export function changeOption(t: Tokens, periods: string[], values: (number | null)[], unit: '%' | 'pp', name: string): EChartsOption {
  return {
    ...base(t),
    grid: { left: 8, right: 12, top: 26, bottom: 8, containLabel: true },
    tooltip: tooltip(t, {
      trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(127,127,127,0.06)' } },
      formatter: (ps: { axisValueLabel: string; value: number | null; color: string }[]) =>
        head(t, ps[0].axisValueLabel) + row(ps[0].color as string, name, fmtChange(ps[0].value, unit)),
    }),
    xAxis: catAxis(t, periods),
    yAxis: valAxis(t, unit === 'pp' ? 'pp' : '%', { axisLabel: { color: t.muted, fontFamily: t.mono, fontSize: 12, formatter: (v: number) => `${v}${unit === 'pp' ? '' : '%'}` } }),
    series: [{
      type: 'bar', name, barMaxWidth: 24, barCategoryGap: '45%',
      data: values.map((v) => ({
        value: v,
        itemStyle: { color: (v ?? 0) >= 0 ? t.divPos : t.divNeg, borderRadius: (v ?? 0) >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] },
        label: { position: (v ?? 0) >= 0 ? 'top' : 'bottom' },
      })),
      label: {
        show: true, color: t.ink2, fontFamily: t.mono, fontSize: 12,
        position: 'top',
        formatter: (p: { value: number | null }) => (p.value == null ? '' : fmtChange(p.value, unit)),
      },
      markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: t.axis, width: 1, type: 'solid' }, data: [{ yAxis: 0 }] },
      animationDelay: (i: number) => i * 80,
    }],
  } as EChartsOption
}

// ---- waterfall / bridge ------------------------------------------------------------------------
export interface Step { label: string; value: number; kind: 'total' | 'delta' }

export function waterfallOption(t: Tokens, steps: Step[], unit: string, totalColor: string): EChartsOption {
  let run = 0
  const bars = steps.map((s) => {
    if (s.kind === 'total') { run = s.value; return { lo: 0, hi: s.value, s } }
    const lo = s.value >= 0 ? run : run + s.value
    const hi = s.value >= 0 ? run + s.value : run
    run += s.value
    return { lo, hi, s }
  })
  const minLo = Math.min(...bars.map((b) => b.lo))
  return {
    ...base(t),
    grid: { left: 8, right: 12, top: 28, bottom: 8, containLabel: true },
    tooltip: tooltip(t, {
      trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(127,127,127,0.06)' } },
      formatter: (ps: { dataIndex: number }[]) => {
        const b = bars[ps[0].dataIndex]
        const color = b.s.kind === 'total' ? totalColor : b.s.value >= 0 ? t.divPos : t.divNeg
        return head(t, b.s.label) + row(color, b.s.kind === 'total' ? 'Level' : 'Change',
          b.s.kind === 'total' ? fmtValue(b.s.value, unit) : `${b.s.value >= 0 ? '+' : '−'}${fmtValue(Math.abs(b.s.value), unit)}`)
      },
    }),
    xAxis: { ...catAxis(t, steps.map((s) => s.label)), axisLabel: { color: t.muted, fontFamily: t.mono, fontSize: 10, interval: 0, margin: 10, hideOverlap: false, width: 56, overflow: 'break' as const, lineHeight: 13 } },
    yAxis: valAxis(t, unit, { min: minLo < 0 ? undefined : (v: { min: number }) => Math.max(0, Math.floor(v.min * 0.92)) }),
    series: [
      { type: 'bar', stack: 'w', name: '_base', silent: true, itemStyle: { color: 'transparent' }, data: bars.map((b) => b.lo), barMaxWidth: 34, animation: false },
      {
        type: 'bar', stack: 'w', name: 'Bridge', barMaxWidth: 34, barCategoryGap: '30%',
        data: bars.map((b) => ({
          value: b.hi - b.lo,
          itemStyle: {
            color: b.s.kind === 'total' ? totalColor : b.s.value >= 0 ? t.divPos : t.divNeg,
            borderRadius: 4,
            opacity: b.s.kind === 'total' ? 1 : 0.92,
          },
        })),
        label: {
          show: true, position: 'top', color: t.ink2, fontFamily: t.mono, fontSize: 12,
          formatter: (p: { dataIndex: number }) => {
            const s = bars[p.dataIndex].s
            const short = (v: number) => fmtCompact(v, unit).replace(/ ?Cr$/, '')   // unit is in the title
            return s.kind === 'total' ? short(s.value) : `${s.value >= 0 ? '+' : '−'}${short(Math.abs(s.value))}`
          },
        },
        animationDelay: (i: number) => i * 140,
        animationDuration: 600,
      },
    ],
  } as EChartsOption
}

// ---- ranked horizontal bars -----------------------------------------------------------------
export function rankedOption(t: Tokens, items: { label: string; value: number | null; color: string; highlight?: boolean }[], unit: string): EChartsOption {
  const sorted = [...items].filter((i) => i.value != null).sort((a, b) => (a.value as number) - (b.value as number))
  return {
    ...base(t),
    grid: { left: 8, right: 70, top: 6, bottom: 4, containLabel: true },
    tooltip: tooltip(t, {
      trigger: 'item',
      formatter: (p: { name: string; value: number; color: string }) => row(p.color, p.name, fmtValue(p.value, unit)),
    }),
    xAxis: { type: 'value', show: false, max: (v: { max: number }) => v.max * 1.04 },
    yAxis: {
      type: 'category', data: sorted.map((i) => i.label), axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: t.ink2, fontSize: 12, fontFamily: t.mono },
    },
    series: [{
      type: 'bar', barMaxWidth: 20, barCategoryGap: '38%',
      data: sorted.map((i) => ({ value: i.value, name: i.label, itemStyle: { color: i.highlight === false ? hexA(i.color, 0.4) : i.color, borderRadius: [0, 4, 4, 0] } })),
      label: { show: true, position: 'right', color: t.ink, fontFamily: t.mono, fontSize: 12, formatter: (p: { value: number }) => fmtCompact(p.value, unit) },
      showBackground: true, backgroundStyle: { color: t.surface2, borderRadius: 4 },
      animationDelay: (i: number) => i * 90,
    }],
  } as EChartsOption
}

// ---- donut (parts of one whole) ---------------------------------------------------------------
export function donutOption(t: Tokens, items: { label: string; value: number; color: string }[], center: { value: string; label: string }): EChartsOption {
  const total = items.reduce((a, i) => a + i.value, 0)
  return {
    ...base(t),
    tooltip: tooltip(t, {
      trigger: 'item',
      formatter: (p: { name: string; value: number; color: string }) => row(p.color, p.name, `${((p.value / total) * 100).toFixed(1)}%`),
    }),
    title: {
      text: center.value, subtext: center.label, left: 'center', top: '40%',
      textStyle: { color: t.ink, fontSize: 22, fontWeight: 600, fontFamily: t.sans },
      subtextStyle: { color: t.muted, fontSize: 12, fontFamily: t.mono },
      itemGap: 4,
    },
    series: [{
      type: 'pie', radius: ['60%', '82%'], center: ['50%', '50%'], padAngle: 1.2,
      itemStyle: { borderRadius: 4, borderColor: t.surface, borderWidth: 2 },
      label: { show: false }, labelLine: { show: false },
      emphasis: { scale: true, scaleSize: 5 },
      data: items.map((i) => ({ name: i.label, value: i.value, itemStyle: { color: i.color } })),
      animationType: 'expansion', animationDuration: 1100,
    }],
  } as EChartsOption
}

// ---- radar ------------------------------------------------------------------------------------
export function radarOption(t: Tokens, axes: { name: string; max: number; min?: number }[], series: { label: string; color: string; values: (number | null)[] }[], unit: string): EChartsOption {
  return {
    ...base(t),
    tooltip: tooltip(t, {
      trigger: 'item',
      formatter: (p: { name: string; value: number[]; color: string }) =>
        head(t, p.name) + axes.map((a, i) => row(p.color, a.name, fmtValue(p.value[i], unit))).join(''),
    }),
    radar: {
      indicator: axes.map((a) => ({ name: a.name, max: a.max, min: a.min ?? 0 })),
      radius: '62%', center: ['50%', '52%'], splitNumber: 4, shape: 'polygon', nameGap: 10,
      axisName: { color: t.ink2, fontSize: 12, fontFamily: t.sans },
      splitLine: { lineStyle: { color: t.grid } },
      splitArea: { areaStyle: { color: ['transparent'] } },
      axisLine: { lineStyle: { color: t.grid } },
    },
    series: [{
      type: 'radar',
      data: series.map((s) => ({
        name: s.label, value: s.values.map((v) => v ?? 0),
        symbol: 'circle', symbolSize: 7,
        lineStyle: { width: 2, color: s.color },
        itemStyle: { color: s.color, borderColor: t.surface, borderWidth: 2 },
        areaStyle: { color: hexA(s.color, 0.1) },
      })),
      emphasis: { focus: 'self', areaStyle: { opacity: 0.4 } },
      animationDuration: 1200,
    }],
  } as EChartsOption
}

// ---- heatmap ------------------------------------------------------------------------------------
export function heatmapOption(t: Tokens, xs: string[], ys: string[], cells: [number, number, number | null][], opts: { diverging: boolean; unit: string; fmt: (v: number) => string }): EChartsOption {
  const vals = cells.map((c) => c[2]).filter((v): v is number => v != null)
  const abs = vals.map(Math.abs).sort((a, b) => a - b)
  // colour saturates at the 80th percentile so one outlier does not wash every other cell out
  const absMax = Math.max(1, abs[Math.floor((abs.length - 1) * 0.8)] ?? 1)
  const lo = Math.min(...vals), hi = Math.max(...vals)
  const clamp = (v: number) => (opts.diverging ? Math.max(-absMax, Math.min(absMax, v)) : v)
  return {
    ...base(t),
    grid: { left: 8, right: 12, top: 8, bottom: 40, containLabel: true },
    tooltip: tooltip(t, {
      trigger: 'item',
      formatter: (p: { value: [number, number, number | string, number | null]; color: string }) =>
        head(t, xs[p.value[0]]) + row(p.color, ys[p.value[1]], p.value[3] == null ? 'no data' : opts.fmt(p.value[3])),
    }),
    xAxis: { type: 'category', data: xs, splitArea: { show: false }, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t.muted, fontFamily: t.mono, fontSize: 12, interval: 0 } },
    yAxis: { type: 'category', data: ys, inverse: true, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t.ink2, fontSize: 12, interval: 0 } },
    visualMap: {
      type: 'continuous', orient: 'horizontal', left: 'center', bottom: 0, itemWidth: 10, itemHeight: 160,
      min: opts.diverging ? -absMax : lo, max: opts.diverging ? absMax : hi, calculable: false,
      text: [`${opts.diverging ? '≥ ' : ''}${opts.fmt(opts.diverging ? absMax : hi)}`, `${opts.diverging ? '≤ ' : ''}${opts.fmt(opts.diverging ? -absMax : lo)}`],
      dimension: 2,
      textStyle: { color: t.muted, fontFamily: t.mono, fontSize: 12 },
      inRange: { color: opts.diverging ? [t.divNeg, t.divMid, t.divPos] : [t.seqLo, t.seqHi] },
    },
    series: [{
      type: 'heatmap',
      data: cells.map((c) => {
        if (c[2] == null) return [c[0], c[1], '-', null]
        // saturated cells take white text, light ones ink (never the data colour)
        const strong = opts.diverging ? Math.abs(clamp(c[2])) / absMax > 0.55 : hi > lo && (c[2] - lo) / (hi - lo) > 0.55
        return { value: [c[0], c[1], clamp(c[2]), c[2]], label: { color: strong ? t.onSolid : t.ink, textBorderWidth: 0 } }
      }),
      itemStyle: { borderColor: t.surface, borderWidth: 3, borderRadius: 6 },
      label: {
        show: true, fontFamily: t.mono, fontSize: 12,
        formatter: (p: { value: [number, number, number | string, number | null] }) => (p.value[3] != null ? opts.fmt(p.value[3]) : '–'),
        color: t.ink,
      },
      emphasis: { itemStyle: { borderColor: t.ink, borderWidth: 1 } },
      animationDuration: 700,
    }],
  } as EChartsOption
}

// ---- slope (first vs last) -------------------------------------------------------------------
export function slopeOption(t: Tokens, series: S[], from: string, to: string, unit: string): EChartsOption {
  return {
    ...base(t),
    grid: { left: 110, right: 110, top: 24, bottom: 8, containLabel: false },
    tooltip: axisTip(t, unit, 'ln'),
    xAxis: { ...catAxis(t, [from, to]), boundaryGap: false, axisLine: { show: false }, position: 'top', axisLabel: { color: t.ink2, fontFamily: t.mono, fontSize: 12 } },
    yAxis: { type: 'value', scale: true, show: false },
    series: series.map((s) => {
      const a = s.points.find((p) => p.period === from)?.value ?? null
      const b = s.points.find((p) => p.period === to)?.value ?? null
      const lbl = (pos: 'left' | 'right') => ({
        show: true, position: pos, distance: 10, color: t.ink2, fontFamily: t.mono, fontSize: 12,
        formatter: () => (pos === 'left' ? `${s.label}  ${fmtCompact(a, unit)}` : `${fmtCompact(b, unit)}  ${pctDelta(a, b, unit)}`),
      })
      return {
        type: 'line' as const, name: s.label, symbol: 'circle', symbolSize: 10,
        lineStyle: { width: 2, color: s.color }, itemStyle: { color: s.color, borderColor: t.surface, borderWidth: 2 },
        data: [{ value: a, label: lbl('left') }, { value: b, label: lbl('right') }],
        labelLayout: { moveOverlap: 'shiftY' as const },
        emphasis: { focus: 'series' as const },
        animationDuration: 1100,
      }
    }),
  } as EChartsOption
}

function pctDelta(a: number | null, b: number | null, unit: string): string {
  if (a == null || b == null) return ''
  if (isPctUnit(unit)) return fmtChange(b - a, 'pp')
  return a ? fmtChange(((b - a) / Math.abs(a)) * 100) : ''
}

// ---- sparkline -----------------------------------------------------------------------------------
export function sparkOption(t: Tokens, points: { period: string; value: number | null }[], color: string, unit: string, highlight?: string): EChartsOption {
  const hi = highlight ? points.findIndex((p) => p.period === highlight) : points.length - 1
  return {
    ...base(t),
    grid: { left: 4, right: 4, top: 10, bottom: 20 },
    tooltip: tooltip(t, {
      trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: t.axis } },
      formatter: (ps: { axisValueLabel: string; value: number | null }[]) => head(t, ps[0].axisValueLabel) + row(color, 'Value', fmtValue(ps[0].value, unit), 'ln'),
    }),
    xAxis: { type: 'category', data: points.map((p) => p.period), boundaryGap: false, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: t.muted, fontFamily: t.mono, fontSize: 10, interval: (i: number) => i === 0 || i === points.length - 1, alignMinLabel: 'left', alignMaxLabel: 'right' } },
    yAxis: { type: 'value', scale: true, show: false },
    series: [{
      type: 'line', data: points.map((p, i) => ({
        value: p.value,
        symbolSize: i === hi ? 8 : 0,
        itemStyle: { color, borderColor: t.surface, borderWidth: 2 },
      })),
      symbol: 'circle', showAllSymbol: true, connectNulls: false,
      lineStyle: { width: 2, color }, areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: hexA(color, 0.18) }, { offset: 1, color: hexA(color, 0) }] } },
      animationDuration: 1100,
    }],
  } as EChartsOption
}

export function hexA(hex: string, a: number): string {
  const h = hex.replace('#', '')
  if (h.length !== 6) return hex
  const n = parseInt(h, 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}
