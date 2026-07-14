import { onBeforeUnmount, onMounted, ref } from 'vue'

export function useMobile(breakpoint = 767) {
  const isMobile = ref(false)
  let media: MediaQueryList | null = null

  const update = (event?: MediaQueryListEvent): void => {
    isMobile.value = event?.matches ?? media?.matches ?? false
  }

  onMounted(() => {
    media = window.matchMedia(`(max-width: ${breakpoint}px)`)
    update()
    media.addEventListener('change', update)
  })

  onBeforeUnmount(() => media?.removeEventListener('change', update))
  return { isMobile }
}
