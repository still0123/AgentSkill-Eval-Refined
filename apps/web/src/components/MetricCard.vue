<script setup lang="ts">
defineProps<{ label: string; value: string; detail?: string; accent?: boolean }>()
</script>

<template>
  <article class="metric-card" :class="{ 'metric-card--accent': accent }">
    <span>{{ label }}</span
    ><strong>{{ value }}</strong
    ><small v-if="detail">{{ detail }}</small>
  </article>
</template>
