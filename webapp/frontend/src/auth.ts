import { ref } from 'vue'
import type { User } from './types'
import { api } from './api'

export const currentUser = ref<User | null>(null)
export const authReady = ref(false)

export async function loadCurrentUser(): Promise<User | null> {
  try {
    const result = await api.get<{ user: User }>('/api/auth/me')
    currentUser.value = result.user
  } catch {
    currentUser.value = null
  } finally {
    authReady.value = true
  }
  return currentUser.value
}

export function clearCurrentUser() {
  currentUser.value = null
  authReady.value = true
}
