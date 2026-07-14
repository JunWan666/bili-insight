<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { BarChart, LineChart } from 'echarts/charts'
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsType } from 'echarts/core'
import type { MediaStream } from '@/types/api'
import { formatBitrate, formatBytes } from '@/utils/format'

const props = defineProps<{ streams: MediaStream[] }>()
const container = ref<HTMLDivElement | null>(null)
echarts.use([BarChart, LineChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

let chart: EChartsType | null = null
let observer: ResizeObserver | null = null

const summary = computed(() => {
  if (!props.streams.length) return '没有可用于比较的视频流数据。'
  const highest = [...props.streams].sort((a, b) => (b.height ?? 0) - (a.height ?? 0) || (b.bitrate ?? 0) - (a.bitrate ?? 0))[0]
  const smallest = [...props.streams].sort((a, b) => (a.estimatedSize ?? Number.MAX_VALUE) - (b.estimatedSize ?? Number.MAX_VALUE))[0]
  return `共比较 ${props.streams.length} 个视频流。最高规格为 ${highest?.qualityLabel ?? '暂无'} ${highest?.codec ?? ''}，码率 ${formatBitrate(highest?.bitrate)}；预估体积最小的是 ${smallest?.qualityLabel ?? '暂无'} ${smallest?.codec ?? ''}，约 ${formatBytes(smallest?.estimatedSize)}。`
})

function render(): void {
  if (!container.value || !props.streams.length) return
  chart ??= echarts.init(container.value)
  const compact = container.value.clientWidth < 560
  const labels = props.streams.map((item) => `${item.qualityLabel}\n${item.codec}`)
  chart.setOption({
    animationDuration: 350,
    color: ['#5268df', '#e68a58'],
    aria: { enabled: true, description: summary.value },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      formatter(params: unknown) {
        const entries = Array.isArray(params) ? params as Array<{ dataIndex: number; marker: string; seriesName: string; value: number }> : []
        const stream = props.streams[entries[0]?.dataIndex ?? 0]
        return [
          `${stream?.qualityLabel ?? ''} · ${stream?.codec ?? ''}`,
          ...entries.map((entry) => `${entry.seriesName}：${entry.seriesName === '视频码率' ? `${entry.value.toFixed(2)} Mbps` : `${entry.value.toFixed(1)} MB`}`),
        ].join('\n')
      },
    },
    legend: { top: 0, textStyle: { color: '#73798a' } },
    grid: { left: compact ? 42 : 58, right: compact ? 38 : 58, top: 48, bottom: compact ? 80 : 65 },
    xAxis: { type: 'category', data: labels, axisLabel: { interval: 0, rotate: compact ? 35 : 0, fontSize: compact ? 9 : 11, color: '#858b9a' }, axisTick: { show: false } },
    yAxis: [
      { type: 'value', name: 'Mbps', nameTextStyle: { color: '#858b9a' }, axisLabel: { color: '#858b9a' }, splitLine: { lineStyle: { color: '#e8eaf0' } } },
      { type: 'value', name: 'MB', nameTextStyle: { color: '#858b9a' }, axisLabel: { color: '#858b9a' }, splitLine: { show: false } },
    ],
    series: [
      { name: '视频码率', type: 'bar', barMaxWidth: 34, data: props.streams.map((item) => (item.bitrate ?? 0) / 1_000_000), itemStyle: { borderRadius: [6, 6, 0, 0] } },
      { name: '预估体积', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 8, data: props.streams.map((item) => (item.estimatedSize ?? 0) / 1024 / 1024) },
    ],
  }, true)
}

onMounted(() => {
  void nextTick(render)
  if (container.value) {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(container.value)
  }
})
watch(() => props.streams, () => void nextTick(render), { deep: true })
onBeforeUnmount(() => { observer?.disconnect(); chart?.dispose() })
</script>

<template>
  <div class="chart-wrapper">
    <div v-if="streams.length" ref="container" class="chart" role="img" :aria-label="summary" />
    <el-empty v-else :image-size="80" description="暂无媒体流图表数据" />
    <p class="chart-summary"><strong>文字摘要：</strong>{{ summary }}</p>
  </div>
</template>

<style scoped>
.chart { width: 100%; height: 360px; }
.chart-summary { margin: 12px 0 0; padding: 13px 15px; border-radius: 11px; background: var(--surface-muted); color: var(--text-secondary); font-size: 12px; line-height: 1.65; }
@media (max-width: 767px) { .chart { height: 315px; } }
</style>
