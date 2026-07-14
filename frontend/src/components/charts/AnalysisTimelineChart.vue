<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { BarChart, LineChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, MarkAreaComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsType } from 'echarts/core'
import { formatDuration } from '@/utils/format'

export interface AnalysisTimelinePoint {
  timestampSeconds: number
  value: number
}

export interface AnalysisTimelineSeries {
  name: string
  points: AnalysisTimelinePoint[]
}

export interface AnalysisTimelineInterval {
  startSeconds: number
  endSeconds: number
  label: string
}

const props = withDefaults(defineProps<{
  series: AnalysisTimelineSeries[]
  intervals?: AnalysisTimelineInterval[]
  variant?: 'line' | 'bar'
  yUnit: string
  summary: string
}>(), {
  intervals: () => [],
  variant: 'line',
})

echarts.use([
  LineChart,
  BarChart,
  AriaComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  TooltipComponent,
  CanvasRenderer,
])

const container = ref<HTMLDivElement | null>(null)
const hasData = computed(() => props.series.some((item) => item.points.length > 0) || props.intervals.length > 0)
let chart: EChartsType | null = null
let observer: ResizeObserver | null = null

function samplePoints(points: AnalysisTimelinePoint[], compact: boolean): AnalysisTimelinePoint[] {
  const maximum = compact ? 42 : 160
  if (points.length <= maximum) return points
  const step = Math.ceil(points.length / maximum)
  const sampled = points.filter((_, index) => index % step === 0)
  const finalPoint = points.at(-1)
  if (finalPoint && sampled.at(-1) !== finalPoint) sampled.push(finalPoint)
  return sampled
}

function tooltipText(value: unknown): string {
  const entries = Array.isArray(value)
    ? value as Array<{ axisValue?: number; seriesName?: string; value?: [number, number] }>
    : []
  const timestamp = Number(entries[0]?.axisValue ?? entries[0]?.value?.[0] ?? 0)
  const lines = [`时间：${formatDuration(timestamp)}`]
  for (const entry of entries) {
    const pointValue = Number(entry.value?.[1])
    if (Number.isFinite(pointValue)) {
      lines.push(`${entry.seriesName ?? '数据'}：${pointValue.toFixed(2)}${props.yUnit ? ` ${props.yUnit}` : ''}`)
    }
  }
  return lines.join('\n')
}

function render(): void {
  if (!container.value || !hasData.value) {
    chart?.dispose()
    chart = null
    return
  }
  chart ??= echarts.init(container.value)
  const compact = container.value.clientWidth < 560
  const visibleSeries = props.series.filter((item) => item.points.length > 0)
  const fallbackSeries: AnalysisTimelineSeries[] = visibleSeries.length
    ? visibleSeries
    : [{
        name: '区间',
        points: props.intervals.flatMap((item) => [
          { timestampSeconds: item.startSeconds, value: 0 },
          { timestampSeconds: item.endSeconds, value: 0 },
        ]),
      }]

  chart.setOption({
    animationDuration: 280,
    color: ['#5268df', '#e67a52', '#1b9c78', '#a061d1'],
    aria: { enabled: true, description: props.summary },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      confine: true,
      formatter: tooltipText,
    },
    legend: {
      top: 0,
      type: compact ? 'scroll' : 'plain',
      textStyle: { color: '#73798a', fontSize: compact ? 10 : 12 },
    },
    grid: {
      left: compact ? 48 : 64,
      right: compact ? 18 : 30,
      top: 48,
      bottom: compact ? 46 : 50,
      containLabel: false,
    },
    xAxis: {
      type: 'value',
      name: '时间',
      min: 0,
      nameTextStyle: { color: '#858b9a' },
      axisLabel: {
        color: '#858b9a',
        hideOverlap: true,
        formatter: (value: number) => formatDuration(value),
      },
      splitLine: { lineStyle: { color: '#e8eaf0' } },
    },
    yAxis: {
      type: 'value',
      name: props.yUnit,
      nameTextStyle: { color: '#858b9a' },
      axisLabel: { color: '#858b9a' },
      splitLine: { lineStyle: { color: '#e8eaf0' } },
    },
    series: fallbackSeries.map((item, index) => ({
      name: item.name,
      type: props.variant,
      showSymbol: props.variant === 'line' && samplePoints(item.points, compact).length < 30,
      symbolSize: compact ? 5 : 7,
      smooth: props.variant === 'line' ? 0.2 : false,
      barMaxWidth: compact ? 14 : 24,
      data: samplePoints(item.points, compact).map((point) => [point.timestampSeconds, point.value]),
      lineStyle: visibleSeries.length ? undefined : { opacity: 0 },
      itemStyle: visibleSeries.length ? undefined : { opacity: 0 },
      markArea: index === 0 && props.intervals.length
        ? {
            silent: true,
            itemStyle: { color: 'rgba(200, 66, 66, .12)' },
            label: { show: !compact, color: '#a84a45', fontSize: 10 },
            data: props.intervals.map((interval) => [
              { name: interval.label, xAxis: interval.startSeconds },
              { xAxis: interval.endSeconds },
            ]),
          }
        : undefined,
    })),
  }, true)
}

onMounted(() => {
  void nextTick(render)
  if (container.value) {
    observer = new ResizeObserver(() => {
      chart?.resize()
      render()
    })
    observer.observe(container.value)
  }
})

watch(
  () => [props.series, props.intervals, props.variant, props.yUnit, props.summary],
  () => void nextTick(render),
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div v-if="hasData" class="analysis-chart">
    <div ref="container" class="chart-canvas" role="img" :aria-label="summary" />
    <p class="chart-summary"><strong>文字摘要：</strong>{{ summary }}</p>
  </div>
</template>

<style scoped>
.analysis-chart { min-width: 0; }
.chart-canvas { width: 100%; height: 310px; }
.chart-summary { margin: 10px 0 0; padding: 12px 14px; border-radius: 10px; background: var(--surface-muted); color: var(--text-secondary); font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; }
@media (max-width: 767px) {
  .chart-canvas { height: 265px; }
  .chart-summary { font-size: 11px; }
}
</style>
