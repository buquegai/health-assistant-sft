<template>
  <article class="message-row" :class="`message-${message.role}`" :aria-label="message.role === 'user' ? '你的消息' : '健康助手的回答'">
    <div v-if="message.role === 'assistant'" class="assistant-badge" aria-hidden="true"><HeartPulse :size="16" /></div>
    <div class="message-body">
      <div class="message-label">{{ message.role === 'user' ? '你' : '健康助手' }}</div>
      <div v-if="message.role === 'user'" class="user-bubble">{{ message.content }}</div>
      <div v-else class="assistant-content">
        <div v-if="!message.content && message.status === 'generating'" class="thinking-label"><span class="thinking-mark"></span>正在思考…</div>
        <div v-else-if="!message.content && message.status !== 'generating'" class="empty-answer">没有生成可显示的回答。</div>
        <div v-else class="markdown-body" v-html="safeMarkdown(message.content)"></div>
        <p v-if="message.history_trimmed" class="context-note"><Info :size="14" />本次回答参考了最近的对话。</p>
        <p v-if="message.status === 'stopped'" class="message-status status-stopped"><CircleStop :size="14" />已停止生成</p>
        <p v-else-if="message.status === 'failed'" class="message-status status-failed"><CircleAlert :size="14" />{{ failureMessage }}</p>
        <p v-else-if="message.finish_reason === 'length'" class="message-status status-length"><Info :size="14" />回答达到长度上限，可继续提问。</p>
        <div v-if="message.status !== 'generating'" class="message-actions">
          <button v-if="message.content" class="text-action" type="button" @click="emit('copy', message.content)"><Copy :size="14" />复制</button>
          <button v-if="canRetry" class="text-action retry-action" type="button" @click="emit('retry', message)"><RotateCcw :size="14" />重新生成</button>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import { CircleAlert, CircleStop, Copy, HeartPulse, Info, RotateCcw } from 'lucide-vue-next'
import type { ChatMessage } from '../types'

const props = defineProps<{ message: ChatMessage; canRetry: boolean }>()
const emit = defineEmits<{
  copy: [content: string]
  retry: [message: ChatMessage]
}>()
const failureMessage = computed(() => {
  const messages: Record<string, string> = {
    SERVER_RESTARTED: '上次回答中断，可重新生成。',
    MODEL_BUSY: '模型正在处理其他请求，请稍后重试。',
    MODEL_TIMEOUT: '等待回答超时，请稍后重试。',
    MODEL_UNAVAILABLE: '模型服务暂时不可用，请稍后重试。',
    MODEL_CANCEL_UNCONFIRMED: '模型仍在停止，暂时无法接受新问题。',
  }
  return messages[props.message.error_code ?? ''] ?? '回答中断，请重试。'
})

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})
markdown.renderer.rules.link_open = (tokens, index, options, _env, self) => {
  const token = tokens[index]
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer')
  return self.renderToken(tokens, index, options)
}
markdown.renderer.rules.image = (tokens, index) => {
  const alt = tokens[index].content
  return alt ? `<span class="image-alt">[图片：${markdown.utils.escapeHtml(alt)}]</span>` : ''
}

function safeMarkdown(content: string) {
  return DOMPurify.sanitize(markdown.render(content), {
    ALLOWED_TAGS: ['p', 'br', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'strong', 'em', 'del', 'blockquote', 'pre', 'code', 'a', 'hr', 'span'],
    ALLOWED_ATTR: ['href', 'target', 'rel', 'class'],
  })
}

defineExpose({ message: props.message })
</script>
