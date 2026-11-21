<template>
  <aside class="sidebar" :class="{ 'sidebar-open': open }" aria-label="会话导航">
    <div class="sidebar-top">
      <div class="sidebar-brand-row">
        <div class="brand-mark brand-mark-small" aria-hidden="true"><HeartPulse :size="18" /></div>
        <div>
          <div class="sidebar-brand">健康助手</div>
          <div class="sidebar-tagline">健康知识与情绪支持</div>
        </div>
        <button class="icon-button sidebar-close" type="button" aria-label="关闭会话栏" @click="emit('close')"><X :size="19" /></button>
      </div>
      <button class="button button-new" type="button" :disabled="generating" :title="generating ? '请先停止当前回答' : '新建对话'" @click="emit('new')">
        <Plus :size="17" stroke-width="2.2" /> <span>新建对话</span>
      </button>
    </div>

    <div class="conversation-section">
      <div class="section-heading"><span>历史对话</span><span v-if="loading" class="loading-dot" aria-label="正在加载"></span></div>
      <div v-if="loadError" class="sidebar-state sidebar-error">
        <p>历史对话加载失败</p>
        <button class="text-button" type="button" @click="emit('reload')">重新加载</button>
      </div>
      <div v-else-if="!loading && conversations.length === 0" class="sidebar-state">还没有对话，从一个问题开始吧</div>
      <ul v-else class="conversation-list">
        <li v-for="conversation in conversations" :key="conversation.id" class="conversation-row" :class="{ selected: conversation.id === activeId }">
          <button class="conversation-select" type="button" :disabled="generating" :title="conversation.title" @click="emit('select', conversation.id)">
            <MessageSquareText :size="16" />
            <span class="conversation-title">{{ conversation.title }}</span>
            <span class="conversation-time">{{ relativeTime(conversation.updated_at) }}</span>
          </button>
          <button class="icon-button conversation-delete" type="button" :disabled="generating" :aria-label="`删除对话：${conversation.title}`" title="删除对话" @click="emit('delete', conversation)">
            <Trash2 :size="15" />
          </button>
        </li>
      </ul>
      <button v-if="nextCursor" class="load-more" type="button" :disabled="loadingMore" @click="emit('more')">
        {{ loadingMore ? '加载中…' : '加载更多' }}
      </button>
    </div>

    <div class="sidebar-bottom">
      <div class="user-avatar" aria-hidden="true">{{ username.slice(0, 1).toUpperCase() }}</div>
      <div class="user-details"><span class="user-name">{{ username }}</span><span class="user-caption">个人空间</span></div>
      <button class="icon-button logout-button" type="button" :disabled="loggingOut" aria-label="退出登录" title="退出登录" @click="emit('logout')">
        <LogOut :size="18" />
      </button>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { HeartPulse, LogOut, MessageSquareText, Plus, Trash2, X } from 'lucide-vue-next'
import type { Conversation } from '../types'

defineProps<{
  open: boolean
  username: string
  conversations: Conversation[]
  activeId: string | null
  loading: boolean
  loadingMore: boolean
  loadError: boolean
  nextCursor: string | null
  generating: boolean
  loggingOut: boolean
}>()
const emit = defineEmits<{
  close: []
  new: []
  reload: []
  more: []
  select: [id: string]
  delete: [conversation: Conversation]
  logout: []
}>()

function relativeTime(raw: string) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(raw).getTime()) / 60000))
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  return days < 7 ? `${days} 天前` : new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(new Date(raw))
}
</script>
