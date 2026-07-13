<script setup lang="ts">
defineProps<{ tone?: 'good' | 'warn' | 'bad' | 'info' | 'neutral' }>()
</script>

<template>
  <span class="badge" :class="`badge--${tone ?? 'neutral'}`"><slot /></span>
</template>
