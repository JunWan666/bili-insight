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
  gap: 20px;
  margin-bottom: 18px;
}

.page-eyebrow {
  margin: 0 0 5px;
  color: var(--brand);
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1 { margin: 0; font-size: 32px; line-height: 1.16; letter-spacing: 0; }
.description { max-width: 760px; margin: 6px 0 0; color: var(--text-secondary); line-height: 1.5; }
.page-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }

@media (max-width: 767px) {
  .page-header { display: block; margin-bottom: 18px; }
  h1 { font-size: 26px; }
  .page-actions { justify-content: flex-start; margin-top: 16px; }
}
</style>
