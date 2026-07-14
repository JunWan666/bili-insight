<script setup lang="ts">
defineProps<{
  title: string
  description?: string
  eyebrow?: string
}>()
</script>

<template>
  <header class="page-header">
    <div>
      <p v-if="eyebrow" class="page-eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <p v-if="description" class="description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.page-eyebrow {
  margin: 0 0 7px;
  color: var(--brand);
  font-size: 12px;
  font-weight: 750;
  letter-spacing: .12em;
  text-transform: uppercase;
}

h1 { margin: 0; font-size: clamp(26px, 3vw, 38px); line-height: 1.16; letter-spacing: -.035em; }
.description { max-width: 700px; margin: 9px 0 0; color: var(--text-secondary); line-height: 1.65; }
.page-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px; }

@media (max-width: 767px) {
  .page-header { display: block; margin-bottom: 18px; }
  .page-actions { justify-content: flex-start; margin-top: 16px; }
}
</style>
