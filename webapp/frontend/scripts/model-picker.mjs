import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const baseUrl = process.env.APP_URL ?? 'http://localhost:5173'
const screenshotDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'screenshots')
const conversationId = '11111111-1111-4111-8111-111111111111'
const models = [
  { id: 'health-assistant', name: '健康助手（Qwen3-1.7B 微调版）' },
  { id: 'qwen3-base', name: 'Qwen3-1.7B 原始模型（未微调）' },
]
let conversation = null
let sentModelId = null
let sentCount = 0
let stopCount = 0
let messageAccepted = false
let modelChangeGate = null
const browser = await chromium.launch({ channel: 'msedge', headless: true })

try {
  await mkdir(screenshotDir, { recursive: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  page.setDefaultTimeout(15_000)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    const method = request.method()
    if (pathname === '/api/auth/me') {
      return route.fulfill({ json: { user: { id: 'test-user', username: 'model-picker' } } })
    }
    if (pathname === '/api/models') {
      return route.fulfill({ json: { items: models, default_model_id: models[0].id } })
    }
    if (pathname === '/api/conversations' && method === 'GET') {
      return route.fulfill({ json: { items: conversation ? [conversation] : [], next_cursor: null } })
    }
    if (pathname === '/api/conversations' && method === 'POST') {
      const body = request.postDataJSON()
      conversation = {
        id: conversationId,
        title: '新对话',
        model_id: body.model_id,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      }
      return route.fulfill({ status: 201, json: conversation })
    }
    if (pathname === `/api/conversations/${conversationId}` && method === 'GET') {
      return route.fulfill({ json: conversation })
    }
    if (pathname === `/api/conversations/${conversationId}/model` && method === 'PATCH') {
      if (modelChangeGate) await modelChangeGate
      conversation.model_id = request.postDataJSON().model_id
      return route.fulfill({ json: conversation })
    }
    if (pathname === `/api/conversations/${conversationId}/messages` && method === 'GET') {
      await new Promise((resolve) => setTimeout(resolve, 150))
      if (messageAccepted) {
        return route.fulfill({ json: { items: [
          { id: '22222222-2222-4222-8222-222222222222', conversation_id: conversationId, role: 'user', content: '模型切换验收', status: 'completed' },
          { id: '33333333-3333-4333-8333-333333333333', conversation_id: conversationId, role: 'assistant', content: '', status: 'generating' },
        ], next_cursor: null } })
      }
      return route.fulfill({ json: { items: [], next_cursor: null } })
    }
    if (pathname === `/api/conversations/${conversationId}/messages` && method === 'POST') {
      messageAccepted = true
      sentModelId = conversation.model_id
      sentCount += 1
      const userMessageId = `22222222-2222-4222-8222-${String(sentCount).padStart(12, '0')}`
      const assistantMessageId = `33333333-3333-4333-8333-${String(sentCount).padStart(12, '0')}`
      const body = [
        `event: meta\ndata: ${JSON.stringify({ conversation_id: conversationId, user_message_id: userMessageId, assistant_message_id: assistantMessageId, history_trimmed: false })}\n\n`,
        'event: delta\ndata: {"text":"受控模型选择器测试回答"}\n\n',
        `event: done\ndata: ${JSON.stringify({ status: 'completed', finish_reason: 'stop', assistant_message_id: assistantMessageId })}\n\n`,
      ].join('')
      return route.fulfill({ status: 200, headers: { 'Content-Type': 'text/event-stream' }, body })
    }
    if (pathname.startsWith('/api/messages/') && pathname.endsWith('/stop')) {
      stopCount += 1
      return route.fulfill({ status: 204 })
    }
    return route.fulfill({ status: 204 })
  })

  await page.goto(`${baseUrl}/chat`)
  const picker = page.getByRole('combobox', { name: '选择模型' })
  await picker.waitFor()
  assert.equal(await picker.locator('option').count(), 2)
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.screenshot({ path: path.join(screenshotDir, '11-model-picker-desktop-1440.png'), animations: 'disabled' })
  await picker.selectOption('qwen3-base')
  await page.getByRole('textbox', { name: '输入你的问题' }).fill('模型切换验收')
  await page.getByRole('button', { name: '发送' }).click()
  await page.getByText('受控模型选择器测试回答', { exact: true }).waitFor()
  assert.equal(sentModelId, 'qwen3-base')
  assert.equal(conversation.model_id, 'qwen3-base')
  assert.equal(stopCount, 0, 'The initial send must not be mistaken for an orphaned generation')

  await page.reload()
  await picker.waitFor()
  await page.waitForFunction(() => document.querySelector('.model-picker')?.value === 'qwen3-base')
  assert.equal(await picker.inputValue(), 'qwen3-base')
  await page.setViewportSize({ width: 320, height: 844 })
  const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }))
  assert(layout.document <= layout.viewport, 'The model picker must not cause horizontal overflow at 320px')
  await page.screenshot({ path: path.join(screenshotDir, '12-model-picker-mobile-320.png'), animations: 'disabled' })
  await page.setViewportSize({ width: 1440, height: 960 })
  await page.waitForFunction(() => !document.querySelector('.model-picker')?.disabled)
  const composer = page.getByRole('textbox', { name: '输入你的问题' })
  await composer.fill('模型切换保存期间不能发送')
  let releaseModelChange
  modelChangeGate = new Promise((resolve) => { releaseModelChange = resolve })
  const beforeSwitchSendCount = sentCount
  await picker.selectOption('health-assistant')
  assert(await page.getByRole('button', { name: '发送', exact: true }).isDisabled())
  await composer.press('Enter')
  assert.equal(sentCount, beforeSwitchSendCount, 'No message may be sent before model selection is saved')
  assert.equal(sentModelId, 'qwen3-base', 'Enter during model persistence must not send with the old model')
  releaseModelChange()
  await page.waitForFunction(() => !document.querySelector('.model-picker')?.disabled)
  assert.equal(conversation.model_id, 'health-assistant')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await page.waitForFunction(() => Boolean(document.querySelector('.message-assistant:last-of-type .markdown-body')))
  assert.equal(sentModelId, 'health-assistant')
  console.log('PASS 双模型选择、会话持久化、发送模型绑定、首条消息及切换保存竞态保护、刷新恢复与 320px 布局')
} finally {
  await browser.close()
}
