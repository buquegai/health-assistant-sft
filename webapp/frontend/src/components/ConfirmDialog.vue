<template>
  <dialog ref="dialog" class="confirm-dialog" :aria-labelledby="titleId" @cancel.prevent="emit('close')" @click="onBackdrop">
    <div class="dialog-content">
      <div class="dialog-icon"><AlertTriangle :size="20" /></div>
      <h2 :id="titleId">{{ title }}</h2>
      <p>{{ description }}</p>
      <div class="dialog-actions">
        <button ref="cancelButton" class="button button-quiet" type="button" @click="emit('close')">取消</button>
        <button class="button" :class="destructive ? 'button-danger' : 'button-primary'" type="button" @click="emit('confirm')">{{ confirmLabel }}</button>
      </div>
    </div>
  </dialog>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  description: string
  confirmLabel?: string
  destructive?: boolean
}>(), { confirmLabel: '确认', destructive: false })
const emit = defineEmits<{ close: []; confirm: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)
const titleId = `dialog-title-${Math.random().toString(36).slice(2)}`

watch(() => props.open, async (open) => {
  await nextTick()
  if (open && dialog.value && !dialog.value.open) {
    dialog.value.showModal()
    cancelButton.value?.focus()
  } else if (!open && dialog.value?.open) {
    dialog.value.close()
  }
})

function onBackdrop(event: MouseEvent) {
  if (event.target === dialog.value) emit('close')
}
</script>
