<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { BarChart } from 'echarts/charts'
import { AriaComponent, GridComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsType } from 'echarts/core'
import type { AnalysisSpectrumBand } from '@/types/api'

const props = defineProps<{
  bands: AnalysisSpectrumBand[]
  summary: string
}>()

echarts.use([BarChart, AriaComponent, GridComponent, TooltipComponent, CanvasRenderer])

const container = ref<HTMLDivElement | null>(null)
let chart: EChartsType | null = null
let observer: ResizeObserver | null = null

function frequency(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)} kHz` : `${Math.round(value)} Hz`
}

function render(): void {
  if (!container.value || !props.bands.length) return
  chart ??= echarts.init(container.value)
  const compact = container.value.clientWidth < 560
  chart.setOption({
    animationDuration: 280,
    aria: { enabled: true, description: props.summary },
    color: ['#5268df'],
    tooltip: {
      trigger: 'axis',
      confine: true,
      formatter: (entries: unknown) => {
        const index = Number((Array.isArray(entries) ? entries[0]?.dataIndex : 0) ?? 0)
        const band = props.bands[index]
        if (!band) return ''
        return [
          band.label,
          `${frequency(band.minimumFrequencyHz)} – ${frequency(band.maximumFrequencyHz)}`,
          `相对强度 ${(band.relativeMagnitude * 100).toFixed(1)}%`,
          `频谱占比 ${(band.magnitudeShare * 100).toFixed(1)}%`,
        ].join('\n')
      },
    },
    grid: { left: compact ? 42 : 58, right: 18, top: 20, bottom: compact ? 54 : 48 },
    xAxis: {
      type: 'category',
      data: props.bands.map((band) => band.label),
      axisLabel: { color: '#858b9a', interval: 0, rotate: compact ? 24 : 0, fontSize: compact ? 9 : 11 },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      name: '相对强度 %',
      axisLabel: { color: '#858b9a' },
      splitLine: { lineStyle: { color: '#e8eaf0' } },
    },
    series: [{
      type: 'bar',
      barMaxWidth: compact ? 24 : 38,
      data: props.bands.map((band) => Number((band.relativeMagnitude * 100).toFixed(2))),
      itemStyle: { borderRadius: [5, 5, 0, 0] },
    }],
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

watch(() => [props.bands, props.summary], () => void nextTick(render), { deep: true })

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div v-if="bands.length" class="spectrum-chart">
    <div ref="container" class="chart-canvas" role="img" :aria-label="summary" />
    <p><strong>文字摘要：</strong>{{ summary }}</p>
  </div>
</template>

<style scoped>
.spectrum-chart { min-width: 0; }
.chart-canvas { width: 100%; height: 285px; }
.spectrum-chart p { margin: 10px 0 0; padding: 12px 14px; border-radius: 10px; background: var(--surface-muted); color: var(--text-secondary); font-size: 12px; line-height: 1.65; overflow-wrap: anywhere; }
@media (max-width: 767px) {
  .chart-canvas { height: 255px; }
  .spectrum-chart p { font-size: 11px; }
}
</style>
