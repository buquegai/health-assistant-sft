<template>
  <main class="chat-shell">
    <div v-if="drawerOpen" class="drawer-backdrop" @click="drawerOpen = false"></div>
    <ChatSidebar
      :open="drawerOpen"
      :username="currentUser?.username ?? ''"
      :conversations="conversations"
      :active-id="conversationId"
      :loading="loadingConversations"
      :loading-more="loadingMoreConversations"
      :load-error="conversationLoadError"
      :next-cursor="conversationCursor"
      :generating="isGenerating"
      :logging-out="loggingOut"
      @close="drawerOpen = false"
      @new="newConversation"
      @reload="loadConversations(true)"
      @more="loadMoreConversations"
      @select="openConversation"
      @delete="askDelete"
      @logout="logout"
    />

    <section class="chat-main">
      <header class="chat-header">
        <button class="icon-button menu-button" type="button" aria-label="打开会话列表" @click="drawerOpen = true"><Menu :size="20" /></button>
        <div class="header-title-wrap">
          <span class="header-title">{{ activeTitle }}</span>
          <span v-if="conversationId" class="header-subtitle">对话记录已保存</span>
        </div>
        <div class="header-right">
          <label class="model-picker-label" for="chat-model-picker">模型</label>
          <select id="chat-model-picker" class="model-picker" aria-label="选择模型" :value="selectedModelId" :disabled="modelBusy || models.length === 0" @change="onModelPickerChange">
            <option v-for="model in models" :key="model.id" :value="model.id">{{ model.name }}</option>
          </select>
          <span class="privacy-indicator"><ShieldCheck :size="15" />仅自己可见</span>
        </div>
      </header>

      <div v-if="routeError" class="conversation-missing">
        <div class="missing-symbol"><MessageCircleOff :size="22" /></div>
        <h1>会话不存在或无法访问</h1>
        <button class="button button-primary" type="button" @click="newConversation">开始新对话</button>
      </div>

      <template v-else>
        <section ref="scrollArea" class="reading-area" aria-label="聊天内容" @scroll="onScroll">
          <div v-if="loadingMessages" class="message-loading" aria-label="正在加载聊天记录"><span></span><span></span><span></span></div>
          <ChatWelcome v-else-if="messages.length === 0" @choose="choosePrompt" />
          <div v-else class="message-stack" :class="{ 'messages-loading-more': loadingOlder }">
            <button v-if="messageCursor" class="older-messages" type="button" :disabled="loadingOlder" @click="loadOlderMessages">
              {{ loadingOlder ? '正在加载…' : '加载更早消息' }}
            </button>
            <ChatMessage
              v-for="message in messages"
              :key="message.id"
              :message="message"
              :can-retry="canRetry(message)"
              @copy="copyMessage"
              @retry="requestRetry"
            />
          </div>
          <button v-if="showJumpLatest && messages.length" class="jump-latest" type="button" @click="jumpToLatest">
            <ArrowDown :size="15" />回到最新
          </button>
          <div ref="bottomAnchor" class="bottom-anchor"></div>
        </section>

        <footer class="composer-area">
          <div class="composer-inner">
            <ChatComposer
              ref="composer"
              v-model="draft"
              :locked="composerLocked"
              :generating="isGenerating"
              :submitting="submitting"
              :stopping="stopping"
              :send-error="sendError"
              @send="sendMessage"
              @stop="stopGeneration"
            />
            <p class="health-disclaimer composer-disclaimer">回答由 AI 生成，仅供健康知识参考，不能替代专业诊疗。</p>
          </div>
        </footer>
      </template>
      <div class="toast-region" role="status" aria-live="polite">{{ toast }}</div>
    </section>

    <ConfirmDialog
      :open="Boolean(deleteTarget)"
      title="删除这段对话？"
      description="删除后，这段对话及消息将无法恢复。"
      confirm-label="删除"
      destructive
      @close="deleteTarget = null"
      @confirm="deleteConversation"
    />
    <ConfirmDialog
      :open="Boolean(retryTarget)"
      title="重新生成回答？"
      description="重新生成将替换这次未完成的回答。"
      confirm-label="重新生成"
      @close="retryTarget = null"
      @confirm="confirmRetry"
    />
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, Menu, MessageCircleOff, ShieldCheck } from 'lucide-vue-next'
import ChatComposer from '../components/ChatComposer.vue'
import ChatMessage from '../components/ChatMessage.vue'
import ChatSidebar from '../components/ChatSidebar.vue'
import ChatWelcome from '../components/ChatWelcome.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { api, ApiError, readEventStream } from '../api'
import { clearCurrentUser, currentUser } from '../auth'
import type { ChatMessage as ChatMessageData, Conversation, ModelOption, ModelList, Page } from '../types'

const route = useRoute()
const router = useRouter()
const drawerOpen = ref(false)
const conversations = ref<Conversation[]>([])
const models = ref<ModelOption[]>([])
const defaultModelId = ref('')
const selectedModelId = ref('')
const modelChanging = ref(false)
const conversationCursor = ref<string | null>(null)
const loadingConversations = ref(false)
const loadingMoreConversations = ref(false)
const conversationLoadError = ref(false)
const messages = ref<ChatMessageData[]>([])
const messageCursor = ref<number | null>(null)
const loadingMessages = ref(false)
const loadingOlder = ref(false)
const routeError = ref(false)
const draft = ref('')
const submitting = ref(false)
const stopping = ref(false)
const activeAssistantId = ref<string | null>(null)
const streamController = ref<AbortController | null>(null)
const sendError = ref('')
const deleteTarget = ref<Conversation | null>(null)
const retryTarget = ref<ChatMessageData | null>(null)
const loggingOut = ref(false)
const toast = ref('')
const showJumpLatest = ref(false)
const scrollArea = ref<HTMLElement | null>(null)
const bottomAnchor = ref<HTMLElement | null>(null)
const composer = ref<InstanceType<typeof ChatComposer> | null>(null)
let stickToBottom = true
let toastTimer = 0
let loadVersion = 0
let disposed = false
let sendingConversationId: string | null = null

const conversationId = computed(() => typeof route.params.conversationId === 'string' ? route.params.conversationId : null)
const activeConversation = computed(() => conversations.value.find((item) => item.id === conversationId.value))
const activeTitle = computed(() => activeConversation.value?.title || '健康助手')
const isGenerating = computed(() => Boolean(activeAssistantId.value))
const modelBusy = computed(() => modelChanging.value || loadingMessages.value || submitting.value || isGenerating.value || stopping.value)
const composerLocked = computed(() => modelBusy.value || !selectedModelId.value || models.value.length === 0)

onMounted(async () => {
  await Promise.all([loadModels(), loadConversations(true), loadRouteConversation()])
})

watch(conversationId, async (next, previous) => {
  if (next === previous && messages.value.length) return
  drawerOpen.value = false
  if (next === null) {
    ++loadVersion
    routeError.value = false
    messages.value = []
    messageCursor.value = null
    loadingMessages.value = false
    draft.value = ''
    selectedModelId.value = defaultModelId.value
    return
  }
  await loadRouteConversation()
})

watch(messages, async () => {
  if (stickToBottom) await nextTick(() => bottomAnchor.value?.scrollIntoView({ block: 'end', behavior: 'smooth' }))
}, { deep: true })

onBeforeUnmount(() => {
  disposed = true
  streamController.value?.abort()
  window.clearTimeout(toastTimer)
})

async function loadConversations(reset: boolean) {
  if (loadingConversations.value) return
  loadingConversations.value = true
  conversationLoadError.value = false
  try {
    const query = reset || !conversationCursor.value ? '' : `?cursor=${encodeURIComponent(conversationCursor.value)}`
    const page = await api.get<Page<Conversation>>(`/api/conversations${query}`)
    conversations.value = reset ? page.items : [...conversations.value, ...page.items]
    conversationCursor.value = page.next_cursor as string | null
  } catch (error) {
    conversationLoadError.value = true
    if (error instanceof ApiError && error.status === 401) await expireSession()
  } finally {
    loadingConversations.value = false
  }
}

async function loadModels() {
  try {
    const result = await api.get<ModelList>('/api/models')
    models.value = result.items
    defaultModelId.value = result.default_model_id
    if (!conversationId.value) selectedModelId.value = result.default_model_id
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) await expireSession()
    else sendError.value = error instanceof Error ? error.message : '模型列表加载失败，请刷新重试。'
  }
}

async function loadMoreConversations() {
  if (!conversationCursor.value || loadingMoreConversations.value) return
  loadingMoreConversations.value = true
  try {
    const page = await api.get<Page<Conversation>>(`/api/conversations?cursor=${encodeURIComponent(conversationCursor.value)}`)
    conversations.value = [...conversations.value, ...page.items]
    conversationCursor.value = page.next_cursor as string | null
  } catch {
    conversationLoadError.value = true
  } finally {
    loadingMoreConversations.value = false
  }
}

async function loadRouteConversation() {
  const id = conversationId.value
  if (!id) return
  if (sendingConversationId === id) return
  const version = ++loadVersion
  loadingMessages.value = true
  routeError.value = false
  messages.value = []
  messageCursor.value = null
  activeAssistantId.value = null
  try {
    const page = await api.get<Page<ChatMessageData>>(`/api/conversations/${id}/messages?limit=50`)
    if (version !== loadVersion || disposed) return
    messages.value = page.items
    const conversation = await api.get<Conversation>(`/api/conversations/${id}`)
    if (version !== loadVersion || disposed) return
    selectedModelId.value = conversation.model_id
    messageCursor.value = page.next_cursor as number | null
    stickToBottom = true
    await nextTick(() => bottomAnchor.value?.scrollIntoView({ block: 'end' }))
    const generatingMessage = [...page.items].reverse().find((message) => message.role === 'assistant' && message.status === 'generating')
    if (generatingMessage) {
      activeAssistantId.value = generatingMessage.id
      await cancelOrphanedStream(generatingMessage.id)
    }
  } catch (error) {
    if (version !== loadVersion || disposed) return
    routeError.value = true
    if (error instanceof ApiError && error.status === 401) await expireSession()
  } finally {
    if (version === loadVersion) loadingMessages.value = false
  }
}

async function cancelOrphanedStream(messageId: string) {
  try {
    await api.post(`/api/messages/${messageId}/stop`)
  } catch {
    // The stream may already have closed while this page was loading.
  }
  await refreshMessages()
  activeAssistantId.value = null
}

async function refreshMessages() {
  const id = conversationId.value
  if (!id) return
  try {
    const page = await api.get<Page<ChatMessageData>>(`/api/conversations/${id}/messages?limit=50`)
    messages.value = page.items
    messageCursor.value = page.next_cursor as number | null
  } catch {
    return
  }
}

async function loadOlderMessages() {
  const id = conversationId.value
  if (!id || !messageCursor.value || loadingOlder.value) return
  const area = scrollArea.value
  const oldHeight = area?.scrollHeight ?? 0
  const oldTop = area?.scrollTop ?? 0
  const wasStuckToBottom = stickToBottom
  stickToBottom = false
  loadingOlder.value = true
  try {
    const page = await api.get<Page<ChatMessageData>>(`/api/conversations/${id}/messages?before=${messageCursor.value}&limit=50`)
    messages.value = [...page.items, ...messages.value]
    messageCursor.value = page.next_cursor as number | null
    await nextTick()
    if (area) area.scrollTop = oldTop + (area.scrollHeight - oldHeight)
    stickToBottom = wasStuckToBottom
  } catch (error) {
    showToast(error instanceof Error ? error.message : '聊天记录加载失败。')
  } finally {
    if (!loadingOlder.value) stickToBottom = wasStuckToBottom
    loadingOlder.value = false
  }
}

async function newConversation() {
  if (modelChanging.value) return
  if (isGenerating.value || submitting.value) {
    sendError.value = '请先停止当前回答。'
    return
  }
  draft.value = ''
  sendError.value = ''
  messages.value = []
  routeError.value = false
  drawerOpen.value = false
  await router.push('/chat')
  stickToBottom = true
  await nextTick(() => composer.value?.focus())
}

function onModelPickerChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  void selectModel(value)
}

async function selectModel(modelId: string) {
  if (!modelId || modelBusy.value || modelId === selectedModelId.value) return
  const previous = selectedModelId.value
  modelChanging.value = true
  sendError.value = ''
  try {
    const id = conversationId.value
    if (id) {
      const updated = await api.patch<Conversation>(`/api/conversations/${id}/model`, { model_id: modelId })
      if (id !== conversationId.value || disposed) return
      const item = conversations.value.find((conversation) => conversation.id === id)
      if (item) item.model_id = updated.model_id
    }
    selectedModelId.value = modelId
    if (!id) showToast('新对话将使用所选模型')
  } catch (error) {
    selectedModelId.value = previous
    sendError.value = error instanceof Error ? error.message : '模型切换失败，请重试。'
    showToast(sendError.value)
  } finally {
    modelChanging.value = false
  }
}

async function openConversation(id: string) {
  if (modelChanging.value || isGenerating.value || submitting.value) return
  draft.value = ''
  sendError.value = ''
  await router.push(`/chat/${id}`)
}

function choosePrompt(content: string) {
  draft.value = content
  nextTick(() => composer.value?.focus())
}

async function sendMessage() {
  if (composerLocked.value || !draft.value.trim() || Array.from(draft.value.trim()).length > 2000) return
  const content = draft.value.trim()
  sendError.value = ''
  submitting.value = true
  let id = conversationId.value
  let accepted = false
  try {
    if (!id) {
      const created = await api.post<Conversation>('/api/conversations', { model_id: selectedModelId.value || defaultModelId.value })
      id = created.id
      sendingConversationId = id
      conversations.value = [created, ...conversations.value]
      routeError.value = false
      await router.replace(`/chat/${id}`)
    }
    const requestId = crypto.randomUUID()
    const controller = new AbortController()
    streamController.value = controller
    const response = await fetch(`/api/conversations/${id}/messages`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, client_request_id: requestId }),
      signal: controller.signal,
    })
    if (!response.ok) throw await responseApiError(response)
    for await (const event of readEventStream(response)) {
      if (disposed) return
      if (event.event === 'meta') {
        accepted = true
        draft.value = ''
        submitting.value = false
        const userMessage: ChatMessageData = {
          id: String(event.data.user_message_id), conversation_id: id, role: 'user', content, status: 'completed',
          reply_to_id: null, finish_reason: null, error_code: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
        }
        const assistantMessage: ChatMessageData = {
          id: String(event.data.assistant_message_id), conversation_id: id, role: 'assistant', content: '', status: 'generating',
          reply_to_id: userMessage.id, finish_reason: null, error_code: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
          history_trimmed: Boolean(event.data.history_trimmed),
        }
        messages.value.push(userMessage, assistantMessage)
        activeAssistantId.value = assistantMessage.id
        stickToBottom = true
      } else if (event.event === 'delta' && accepted) {
        const assistant = messages.value.find((item) => item.id === activeAssistantId.value)
        if (assistant) assistant.content += String(event.data.text ?? '')
      } else if (event.event === 'done' && accepted) {
        const assistant = messages.value.find((item) => item.id === String(event.data.assistant_message_id) || item.id === activeAssistantId.value)
        if (assistant) {
          assistant.status = event.data.status === 'stopped' ? 'stopped' : 'completed'
          assistant.finish_reason = String(event.data.finish_reason ?? 'stop')
        }
        activeAssistantId.value = null
      } else if (event.event === 'error' && accepted) {
        const assistant = messages.value.find((item) => item.id === activeAssistantId.value)
        if (assistant) {
          assistant.status = 'failed'
          assistant.error_code = String(event.data.code ?? 'MODEL_UNAVAILABLE')
        }
        activeAssistantId.value = null
      }
    }
    if (accepted && activeAssistantId.value) {
      await refreshMessages()
      activeAssistantId.value = null
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError' && disposed) return
    if (accepted) {
      await refreshMessages()
      activeAssistantId.value = null
    } else {
      sendError.value = error instanceof Error ? error.message : '暂时无法连接服务，请稍后重试。'
    }
    if (error instanceof ApiError && error.status === 401) await expireSession()
  } finally {
    if (sendingConversationId === id) sendingConversationId = null
    if (!disposed) {
      submitting.value = false
      stopping.value = false
      streamController.value = null
      if (!activeAssistantId.value) void loadConversations(true)
    }
  }
}

async function responseApiError(response: Response) {
  const result = await response.json().catch(() => null) as { error?: { code: string; message: string } } | null
  return new ApiError(response.status, result?.error ?? { code: 'REQUEST_FAILED', message: '暂时无法连接服务，请稍后重试。' }, response.headers.get('X-Request-ID'))
}

async function stopGeneration() {
  const id = activeAssistantId.value
  if (!id || stopping.value) return
  stopping.value = true
  sendError.value = ''
  try {
    await api.post(`/api/messages/${id}/stop`)
  } catch (error) {
    sendError.value = error instanceof Error ? error.message : '停止请求失败，请重试。'
    stopping.value = false
  }
}

function canRetry(message: ChatMessageData) {
  if (message.role !== 'assistant' || !['failed', 'stopped'].includes(message.status)) return false
  if (message.error_code === 'MODEL_CANCEL_UNCONFIRMED') return false
  const assistantMessages = messages.value.filter((item) => item.role === 'assistant')
  return assistantMessages.at(-1)?.id === message.id
}

function requestRetry(message: ChatMessageData) {
  if (message.content.trim()) retryTarget.value = message
  else void runRetry(message)
}

function confirmRetry() {
  const target = retryTarget.value
  retryTarget.value = null
  if (target) void runRetry(target)
}

async function runRetry(message: ChatMessageData) {
  if (composerLocked.value || !canRetry(message)) return
  submitting.value = true
  sendError.value = ''
  let accepted = false
  const controller = new AbortController()
  streamController.value = controller
  try {
    const response = await fetch(`/api/messages/${message.id}/retry`, {
      method: 'POST', credentials: 'same-origin', signal: controller.signal,
    })
    if (!response.ok) throw await responseApiError(response)
    for await (const event of readEventStream(response)) {
      if (disposed) return
      if (event.event === 'meta') {
        accepted = true
        submitting.value = false
        message.content = ''
        message.status = 'generating'
        message.finish_reason = null
        message.error_code = null
        message.history_trimmed = Boolean(event.data.history_trimmed)
        activeAssistantId.value = message.id
        stickToBottom = true
      } else if (event.event === 'delta' && accepted) {
        message.content += String(event.data.text ?? '')
      } else if (event.event === 'done' && accepted) {
        message.status = event.data.status === 'stopped' ? 'stopped' : 'completed'
        message.finish_reason = String(event.data.finish_reason ?? 'stop')
        activeAssistantId.value = null
      } else if (event.event === 'error' && accepted) {
        message.status = 'failed'
        message.error_code = String(event.data.code ?? 'MODEL_UNAVAILABLE')
        activeAssistantId.value = null
      }
    }
    if (accepted && activeAssistantId.value) {
      await refreshMessages()
      activeAssistantId.value = null
    }
  } catch (error) {
    if (accepted) {
      await refreshMessages()
      activeAssistantId.value = null
    } else {
      sendError.value = error instanceof Error ? error.message : '重新生成失败，请重试。'
    }
    if (error instanceof ApiError && error.status === 401) await expireSession()
  } finally {
    if (!disposed) {
      submitting.value = false
      stopping.value = false
      streamController.value = null
      if (!activeAssistantId.value) void loadConversations(true)
    }
  }
}

async function copyMessage(content: string) {
  try {
    await navigator.clipboard.writeText(content)
    showToast('已复制')
  } catch {
    showToast('复制失败，请选中文本手动复制。')
  }
}

function showToast(message: string) {
  toast.value = message
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 3200)
}

function onScroll() {
  const area = scrollArea.value
  if (!area) return
  stickToBottom = area.scrollHeight - area.scrollTop - area.clientHeight < 120
  showJumpLatest.value = !stickToBottom
}

function jumpToLatest() {
  stickToBottom = true
  showJumpLatest.value = false
  bottomAnchor.value?.scrollIntoView({ block: 'end', behavior: 'smooth' })
}

function askDelete(conversation: Conversation) {
  if (!isGenerating.value) deleteTarget.value = conversation
}

async function deleteConversation() {
  const target = deleteTarget.value
  deleteTarget.value = null
  if (!target) return
  try {
    await api.delete(`/api/conversations/${target.id}`)
    conversations.value = conversations.value.filter((item) => item.id !== target.id)
    if (conversationId.value === target.id) await router.replace('/chat')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '删除失败，请稍后重试。')
  }
}

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try {
    if (activeAssistantId.value) await api.post(`/api/messages/${activeAssistantId.value}/stop`).catch(() => undefined)
    await api.post('/api/auth/logout')
  } catch {
    // The server revokes the cookie session when reachable; the local workspace is cleared either way.
  } finally {
    streamController.value?.abort()
    messages.value = []
    conversations.value = []
    draft.value = ''
    activeAssistantId.value = null
    clearCurrentUser()
    loggingOut.value = false
    await router.replace('/login')
  }
}

async function expireSession() {
  clearCurrentUser()
  messages.value = []
  conversations.value = []
  streamController.value?.abort()
  await router.replace({ path: '/login', query: { expired: '1' } })
}
</script>
