<script setup lang="ts">
import * as echarts from 'echarts/core'
import { BarChart, LineChart, ScatterChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import type { EChartsCoreOption } from 'echarts/core'

echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])
const props = defineProps<{ option: EChartsCoreOption; label: string }>()
const target = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null
onMounted(() => {
  if (target.value) {
    chart = echarts.init(target.value)
    chart.setOption(props.option)
  }
})
watch(
  () => props.option,
  (option) => chart?.setOption(option, true),
  { deep: true },
)
useResizeObserver(target, () => chart?.resize())
onBeforeUnmount(() => chart?.dispose())
</script>

<template><div ref="target" class="research-chart" role="img" :aria-label="label" /></template>
