<template>
  <main class="auth-shell">
    <section class="auth-card" aria-labelledby="auth-title">
      <div class="brand-mark" aria-hidden="true"><HeartPulse :size="20" /></div>
      <div class="brand-name">健康助手</div>
      <h1 id="auth-title">{{ isRegister ? '创建你的账号' : '登录，继续你的对话' }}</h1>
      <p class="auth-subtitle">{{ isRegister ? '保存对话记录，随时继续交流。' : '学习健康知识，聊聊日常压力与生活习惯。' }}</p>

      <div v-if="expired && !isRegister" class="notice notice-warn" role="status">登录已过期，请重新登录。</div>
      <div v-if="formError" class="notice notice-error" role="alert">{{ formError }}</div>

      <form class="auth-form" @submit.prevent="submit">
        <div class="field-group">
          <label for="username">用户名</label>
          <input
            id="username"
            v-model="username"
            name="username"
            autocomplete="username"
            inputmode="text"
            placeholder="请输入用户名"
            maxlength="24"
            :aria-invalid="Boolean(errors.username)"
            aria-describedby="username-help username-error"
            @blur="validateUsername"
          />
          <small v-if="isRegister" id="username-help" class="field-help">3～24 位英文字母、数字或下划线，不区分大小写</small>
          <small v-if="errors.username" id="username-error" class="field-error">{{ errors.username }}</small>
        </div>

        <div class="field-group">
          <label for="password">密码</label>
          <div class="password-control">
            <input
              id="password"
              v-model="password"
              :type="showPassword ? 'text' : 'password'"
              name="password"
              :autocomplete="isRegister ? 'new-password' : 'current-password'"
              :placeholder="isRegister ? '请设置密码' : '请输入密码'"
              :aria-invalid="Boolean(errors.password)"
              aria-describedby="password-help password-error"
              @blur="validatePassword"
            />
            <button class="icon-button password-toggle" type="button" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
              <EyeOff v-if="showPassword" :size="18" />
              <Eye v-else :size="18" />
            </button>
          </div>
          <small v-if="isRegister" id="password-help" class="field-help">8～64 个字符，至少包含字母和数字</small>
          <small v-if="errors.password" id="password-error" class="field-error">{{ errors.password }}</small>
        </div>

        <div v-if="isRegister" class="field-group">
          <label for="password-confirm">确认密码</label>
          <div class="password-control">
            <input
              id="password-confirm"
              v-model="confirmation"
              :type="showConfirmation ? 'text' : 'password'"
              name="password-confirm"
              autocomplete="new-password"
              placeholder="请再次输入密码"
              :aria-invalid="Boolean(errors.confirmation)"
              aria-describedby="confirmation-error"
              @blur="validateConfirmation"
            />
            <button class="icon-button password-toggle" type="button" :aria-label="showConfirmation ? '隐藏确认密码' : '显示确认密码'" @click="showConfirmation = !showConfirmation">
              <EyeOff v-if="showConfirmation" :size="18" />
              <Eye v-else :size="18" />
            </button>
          </div>
          <small v-if="errors.confirmation" id="confirmation-error" class="field-error">{{ errors.confirmation }}</small>
        </div>

        <button class="button button-primary auth-submit" type="submit" :disabled="submitting">
          {{ submitting ? (isRegister ? '注册中…' : '登录中…') : (isRegister ? '注册并开始对话' : '登录') }}
        </button>
      </form>

      <p class="auth-switch">
        <template v-if="isRegister">已有账号？<RouterLink to="/login">去登录</RouterLink></template>
        <template v-else>还没有账号？<RouterLink to="/register">去注册</RouterLink></template>
      </p>
      <p class="health-disclaimer">回答由 AI 生成，仅供健康知识参考，不能替代专业诊疗。</p>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { Eye, EyeOff, HeartPulse } from 'lucide-vue-next'
import { api, ApiError } from '../api'
import { currentUser } from '../auth'
import type { User } from '../types'

const route = useRoute()
const router = useRouter()
const isRegister = computed(() => route.path === '/register')
const expired = computed(() => route.query.expired === '1')
const username = ref('')
const password = ref('')
const confirmation = ref('')
const showPassword = ref(false)
const showConfirmation = ref(false)
const submitting = ref(false)
const formError = ref('')
const errors = reactive({ username: '', password: '', confirmation: '' })

watch(() => route.path, () => {
  formError.value = ''
  errors.username = ''
  errors.password = ''
  errors.confirmation = ''
})

function validateUsername() {
  const normalized = username.value.trim()
  errors.username = !normalized
    ? '请输入用户名'
    : !/^[A-Za-z0-9_]{3,24}$/.test(normalized)
      ? '用户名需为 3～24 位英文字母、数字或下划线'
      : ''
  return !errors.username
}

function validatePassword() {
  if (!password.value) errors.password = '请输入密码'
  else if (isRegister.value && (Array.from(password.value).length < 8 || Array.from(password.value).length > 64)) errors.password = '密码需为 8～64 个字符'
  else if (isRegister.value && (!/[A-Za-z]/.test(password.value) || !/[0-9]/.test(password.value))) errors.password = '密码至少包含一个英文字母和一个数字'
  else errors.password = ''
  return !errors.password
}

function validateConfirmation() {
  errors.confirmation = !confirmation.value
    ? '请再次输入密码'
    : confirmation.value !== password.value
      ? '两次输入的密码不一致'
      : ''
  return !errors.confirmation
}

async function submit() {
  formError.value = ''
  const valid = validateUsername() && validatePassword() && (!isRegister.value || validateConfirmation())
  if (!valid) return
  submitting.value = true
  try {
    const path = isRegister.value ? '/api/auth/register' : '/api/auth/login'
    const result = await api.post<{ user: User }>(path, {
      username: username.value.trim(),
      password: password.value,
    })
    currentUser.value = result.user
    password.value = ''
    confirmation.value = ''
    await router.replace('/chat')
  } catch (error) {
    if (error instanceof ApiError) {
      if (error.code === 'USERNAME_TAKEN') errors.username = error.message
      else if (error.code === 'INVALID_USERNAME') errors.username = error.message
      else if (error.code === 'INVALID_PASSWORD') errors.password = error.message
      else formError.value = error.message
    } else {
      formError.value = '暂时无法连接服务，请稍后重试。'
    }
  } finally {
    submitting.value = false
  }
}
</script>
