<template>
  <form class="composer-shell" @submit.prevent="emit('send')">
    <textarea
      ref="input"
      :value="modelValue"
      rows="2"
      :readonly="locked"
      aria-label="输入你的问题"
      placeholder="输入你的问题，例如：怎样调整睡前习惯？"
      @input="onInput"
      @keydown="onKeydown"
    ></textarea>
    <div class="composer-toolbar">
      <div class="composer-meta">
        <span :class="{ 'count-over': count > 2000 }">{{ count }} / 2000</span>
        <span class="keyboard-hint">Enter 发送，Shift + Enter 换行</span>
        <span v-if="count > 2000" class="limit-hint">最多输入 2000 个字符</span>
        <span v-if="sendError" class="send-error">{{ sendError }}</span>
      </div>
      <button v-if="generating" class="button button-stop" type="button" :disabled="stopping" @click="emit('stop')">
        <Square v-if="!stopping" :size="15" fill="currentColor" />
        <LoaderCircle v-else :size="15" class="spin" />
        {{ stopping ? '正在停止…' : '停止生成' }}
      </button>
      <button v-else class="button button-send" type="submit" :disabled="locked || !modelValue.trim() || count > 2000">
        <LoaderCircle v-if="submitting" :size="15" class="spin" />
        <Send v-else :size="15" />
        {{ submitting ? '发送中…' : '发送' }}
      </button>
    </div>
  </form>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { LoaderCircle, Send, Square } from 'lucide-vue-next'

const props = defineProps<{
  modelValue: string
  locked: boolean
  generating: boolean
  submitting: boolean
  stopping: boolean
  sendError: string
}>()
const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: []
  stop: []
}>()
const input = ref<HTMLTextAreaElement | null>(null)
const count = computed(() => Array.from(props.modelValue).length)

function resize() {
  if (!input.value) return
  input.value.style.height = 'auto'
  input.value.style.height = `${Math.min(input.value.scrollHeight, 160)}px`
  input.value.style.overflowY = input.value.scrollHeight > 160 ? 'auto' : 'hidden'
}

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
  nextTick(resize)
}

function onKeydown(event: KeyboardEvent) {
  const mobile = window.matchMedia('(max-width: 767px)').matches
  if (!mobile && event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    emit('send')
  }
}

function focus() {
  input.value?.focus()
}

watch(() => props.modelValue, () => nextTick(resize))
watch(() => props.locked, (locked) => {
  if (!locked) nextTick(resize)
})

defineExpose({ focus })
</script>
