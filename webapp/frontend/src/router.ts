import { createRouter, createWebHistory } from 'vue-router'
import { authReady, currentUser, loadCurrentUser } from './auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/login', component: () => import('./views/AuthView.vue'), meta: { guestOnly: true } },
    { path: '/register', component: () => import('./views/AuthView.vue'), meta: { guestOnly: true } },
    { path: '/chat', component: () => import('./views/ChatView.vue'), meta: { requiresAuth: true } },
    { path: '/chat/:conversationId', component: () => import('./views/ChatView.vue'), meta: { requiresAuth: true } },
    { path: '/:pathMatch(.*)*', redirect: '/chat' },
  ],
})

router.beforeEach(async (to) => {
  if (!authReady.value) await loadCurrentUser()
  if (to.meta.requiresAuth && !currentUser.value) {
    return { path: '/login', query: to.query.expired ? { expired: '1' } : undefined }
  }
  if (to.meta.guestOnly && currentUser.value) return '/chat'
  return true
})
