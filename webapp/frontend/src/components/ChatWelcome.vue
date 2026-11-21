<template>
  <section class="welcome-state" aria-labelledby="welcome-heading">
    <div class="welcome-symbol"><HeartPulse :size="22" /></div>
    <div class="welcome-kicker">HEALTH KNOWLEDGE & SUPPORT</div>
    <h1 id="welcome-heading">你好，想了解什么健康知识？</h1>
    <p>可以聊聊睡眠习惯、压力调节或日常健康知识。</p>
    <div class="prompt-grid">
      <button v-for="prompt in prompts" :key="prompt.title" class="prompt-item" type="button" @click="emit('choose', prompt.content)">
        <span class="prompt-icon" :class="prompt.tone"><component :is="prompt.icon" :size="17" /></span>
        <span class="prompt-copy"><strong>{{ prompt.title }}</strong><span>{{ prompt.content }}</span></span>
        <ArrowUpRight class="prompt-arrow" :size="16" />
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ArrowUpRight, Brain, HeartPulse, MoonStar, MessagesSquare, Wind } from 'lucide-vue-next'

const emit = defineEmits<{ choose: [content: string] }>()
const prompts = [
  { title: '改善睡眠习惯', content: '最近总在睡前刷手机，怎样逐步改善睡眠习惯？', icon: MoonStar, tone: 'tone-mint' },
  { title: '缓解学习压力', content: '备考时容易紧张，有哪些简单的放松方法？', icon: Wind, tone: 'tone-sky' },
  { title: '了解情绪变化', content: '怎样区分偶尔心情低落和需要寻求帮助的情况？', icon: Brain, tone: 'tone-coral' },
  { title: '与他人沟通', content: '心情不好时，我该怎样向家人表达自己的感受？', icon: MessagesSquare, tone: 'tone-lilac' },
]
</script>
