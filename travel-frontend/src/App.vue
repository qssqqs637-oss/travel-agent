<script setup>
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { computed, nextTick, onMounted, ref, watch } from 'vue'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'
const SESSION_KEY = 'travel_session_id'
const MESSAGES_KEY = 'travel_messages'
const SESSION_PATTERN = /^[A-Za-z0-9_-]{8,64}$/
const MAX_STORED_MESSAGES = 40

function createSessionId() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID()
  }
  return `s-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

// sessionStorage 每个标签页一份：刷新保留，关闭标签页就没了
function loadSessionId() {
  let id = sessionStorage.getItem(SESSION_KEY)
  if (!id || !SESSION_PATTERN.test(id)) {
    id = createSessionId()
    sessionStorage.setItem(SESSION_KEY, id)
  }
  return id
}

function loadMessages() {
  try {
    const raw = JSON.parse(sessionStorage.getItem(MESSAGES_KEY) || '[]')
    return Array.isArray(raw) ? raw.slice(-MAX_STORED_MESSAGES) : []
  } catch {
    return []
  }
}

const sessionId = ref(loadSessionId())
const inputMessage = ref('')
const messages = ref(loadMessages())
const loading = ref(false)
const errorText = ref('')
const chatBox = ref(null)
const statusText = ref('正在规划行程…')
const copiedIndex = ref(-1)
const editingDayKey = ref('')

const hasStreamContent = computed(() => {
  const last = messages.value[messages.value.length - 1]
  return Boolean(last && last.type === 'assistant' && last.content)
})

function persistMessages(value) {
  try {
    // 只保留最近 40 条，避免 sessionStorage 5MB 配额被长对话撑爆
    const trimmed = value.slice(-MAX_STORED_MESSAGES)
    sessionStorage.setItem(MESSAGES_KEY, JSON.stringify(trimmed))
  } catch (err) {
    console.warn('消息缓存失败:', err)
  }
}

async function scrollToBottom() {
  await nextTick()
  if (chatBox.value) {
    chatBox.value.scrollTop = chatBox.value.scrollHeight
  }
}

watch(
  messages,
  (value) => {
    // 流式输出时每个 token 都会触发这里，先不落盘，
    // 等 loading 结束再统一写 sessionStorage，避免高频序列化。
    if (!loading.value) {
      persistMessages(value)
    }
    scrollToBottom()
  },
  { deep: true }
)

watch(loading, (value) => {
  scrollToBottom()
  if (!value) {
    persistMessages(messages.value)
  }
})

onMounted(scrollToBottom)

function pushMessage(type, content) {
  messages.value.push({ type, content })
}

function avatarOf(type) {
  if (type === 'user') return '👤'
  if (type === 'error') return '⚠️'
  return '🤖'
}

function normalizeTemperature(text) {
  // 2.7°C13.5°C / 2.7°C~13.5°C → 2.7°C ~ 13.5°C
  return text.replace(
    /(-?\d+(?:\.\d+)?\s*°C)\s*~?\s*(-?\d+(?:\.\d+)?\s*°C)/g,
    '$1 ~ $2'
  )
}

function renderMarkdown(content) {
  // marked 只负责 Markdown -> HTML，DOMPurify 负责去掉脚本和危险属性
  return DOMPurify.sanitize(marked.parse(normalizeTemperature(content || '')))
}

function itineraryToMarkdown(itinerary) {
  const title = `${itinerary.city || '旅行'} ${itinerary.days || ''} 天行程`.trim()
  const lines = [`# ${title}`, '']

  for (const day of itinerary.day_plans || []) {
    const head = [day.date, day.weekday, day.weather].filter(Boolean).join(' · ')
    lines.push(`## ${head}`)
    if (day.theme) {
      lines.push(`**${day.theme}**`)
    }
    for (const item of day.items || []) {
      const desc = item.description ? `，${item.description}` : ''
      const travel = item.travel_from_previous
        ? `（${item.travel_from_previous}）`
        : ''
      lines.push(`- ${item.time}：${item.title}${desc}${travel}`)
    }
    if (day.notes) {
      lines.push(`> ${day.notes}`)
    }
    lines.push('')
  }

  const budget = itinerary.budget
  if (budget?.per_person) {
    lines.push('## 预算参考')
    lines.push(`人均 ${budget.per_person} 元`)
    if (budget.total) {
      lines.push(`2 人合计 ${budget.total} 元`)
    }
    for (const item of budget.items || []) {
      lines.push(`- ${item.category}：${item.amount} 元`)
    }
    if (budget.note) {
      lines.push(`> ${budget.note}`)
    }
    lines.push('')
  }

  if (itinerary.tips?.length) {
    lines.push('## 实用提醒')
    for (const tip of itinerary.tips) {
      lines.push(`- ${tip}`)
    }
    lines.push('')
  }

  return lines.join('\n')
}

function messageMarkdown(message) {
  return message.itineraryEdited
    ? itineraryToMarkdown(message.itinerary)
    : (message.content || '')
}

async function copyItinerary(message, index) {
  try {
    await navigator.clipboard.writeText(messageMarkdown(message))
    copiedIndex.value = index
    setTimeout(() => {
      if (copiedIndex.value === index) {
        copiedIndex.value = -1
      }
    }, 1500)
  } catch (err) {
    console.error('复制失败:', err)
    errorText.value = '复制失败，请手动选择文字复制。'
  }
}

function downloadMarkdown(message) {
  const city = message.itinerary?.city || 'travel'
  const date = message.itinerary?.start_date || 'plan'
  const blob = new Blob([messageMarkdown(message)], {
    type: 'text/markdown;charset=utf-8'
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${city}-${date}.md`
  link.click()
  URL.revokeObjectURL(url)
}

function removeDayItem(message, dayIndex, itemIndex) {
  const day = message.itinerary?.day_plans?.[dayIndex]
  if (!day || !Array.isArray(day.items)) {
    return
  }
  day.items.splice(itemIndex, 1)
  message.itineraryEdited = true
}

async function regenerateDay(message, dayIndex, messageIndex) {
  if (editingDayKey.value) {
    return
  }

  const key = `${messageIndex}-${dayIndex}`
  const instruction = window.prompt('想怎么调整这一天？可以留空', '')
  if (instruction === null) {
    return
  }

  editingDayKey.value = key

  try {
    const response = await fetch(`${API_BASE}/itinerary/day`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        session_id: sessionId.value,
        day_index: dayIndex,
        instruction,
        itinerary: message.itinerary
      })
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const data = await response.json()
    message.itinerary = data.itinerary
    message.itineraryEdited = true
  } catch (err) {
    console.error('重排失败:', err)
    errorText.value = '这一天重排失败，请重试。'
  } finally {
    editingDayKey.value = ''
  }
}

function onEnter(event) {
  // 中文输入法按回车选词时不要触发发送
  if (event.isComposing) {
    return
  }
  sendMessage()
}

async function sendMessage() {
  const text = inputMessage.value.trim()
  if (!text || loading.value) {
    return
  }

  pushMessage('user', text)
  inputMessage.value = ''
  errorText.value = ''
  statusText.value = '正在规划行程…'
  loading.value = true

  let assistantIndex = -1
  let doneReceived = false

  function appendToken(token) {
    if (!token) {
      return
    }
    if (assistantIndex === -1) {
      messages.value.push({ type: 'assistant', content: '' })
      assistantIndex = messages.value.length - 1
    }
    messages.value[assistantIndex].content += token
  }

  try {
    const response = await fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        message: text,
        session_id: sessionId.value
      })
    })

    if (!response.ok || !response.body) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) {
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() || ''

      for (const block of blocks) {
        const line = block.trim()
        if (!line.startsWith('data:')) {
          continue
        }

        let payload
        try {
          payload = JSON.parse(line.slice(5).trim())
        } catch {
          continue
        }

        if (payload.type === 'status') {
          statusText.value = payload.text || statusText.value
        } else if (payload.type === 'token') {
          appendToken(payload.text)
        } else if (payload.type === 'done') {
          doneReceived = true
          if (assistantIndex === -1) {
            appendToken(payload.reply || '（服务没有返回内容）')
          }
          const assistant = messages.value[assistantIndex]
          if (assistant) {
            assistant.itinerary = payload.itinerary || null
          }
        } else if (payload.type === 'error') {
          throw new Error(payload.message || 'stream error')
        }
      }
    }

    if (!doneReceived) {
      throw new Error('stream ended unexpectedly')
    }
  } catch (err) {
    console.error('发送失败:', err)
    if (assistantIndex === -1) {
      pushMessage(
        'error',
        '请求失败，请确认后端已启动（127.0.0.1:8000）后重试。'
      )
    } else {
      messages.value[assistantIndex].content += '\n\n（生成中断）'
    }
  } finally {
    loading.value = false
  }
}

async function resetChat() {
  if (loading.value) {
    return
  }

  errorText.value = ''

  try {
    const response = await fetch(`${API_BASE}/reset`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ session_id: sessionId.value })
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    messages.value = []
    inputMessage.value = ''
    sessionStorage.removeItem(MESSAGES_KEY)
  } catch (err) {
    console.error('重置失败:', err)
    errorText.value = '重置失败，请稍后再试。'
  }
}
</script>

<template>
  <div class="chat-page">

    <!-- 顶部标题 -->
    <div class="header">
      <div class="header-text">
        <h1>AI 旅行规划助手</h1>
        <p>告诉我你的旅行需求，我来帮你规划行程</p>
      </div>
      <button class="reset-btn" :disabled="loading" @click="resetChat">
        新对话
      </button>
    </div>

    <!-- 聊天区域 -->
    <div ref="chatBox" class="chat-box">

      <!-- 欢迎语 -->
      <div v-if="messages.length === 0" class="welcome">
        <div class="welcome-icon">✈️</div>
        <h2>你好！</h2>
        <p>你可以告诉我：</p>
        <p>“我想去北京玩 3 天，10 月 26 号出发”</p>
      </div>

      <!-- 聊天消息 -->
      <div
        v-for="(message, index) in messages"
        :key="index"
        class="message-row"
        :class="message.type"
      >

        <!-- 头像 -->
        <div class="avatar">
          {{ avatarOf(message.type) }}
        </div>

        <!-- AI 消息：有结构化行程时渲染卡片，否则渲染 Markdown -->
        <template v-if="message.type === 'assistant'">
          <div
            v-if="message.itinerary && message.itinerary.day_plans && message.itinerary.day_plans.length"
            class="itinerary-card"
          >
            <div class="itinerary-header">
              <strong>{{ message.itinerary.city || '旅行行程' }}</strong>
              <span>
                {{ message.itinerary.start_date }}
                <template v-if="message.itinerary.days"> · {{ message.itinerary.days }} 天</template>
              </span>
            </div>

            <p v-if="message.itinerary.summary" class="itinerary-summary">
              {{ normalizeTemperature(message.itinerary.summary) }}
            </p>

            <div
              v-for="(day, dayIndex) in message.itinerary.day_plans"
              :key="dayIndex"
              class="day-card"
            >
              <div class="day-head">
                <span class="day-date">{{ day.date }}</span>
                <span v-if="day.weekday" class="day-weekday">{{ day.weekday }}</span>
                <span v-if="day.weather" class="day-weather">{{ normalizeTemperature(day.weather) }}</span>
                <button
                  class="day-edit-btn"
                  :disabled="editingDayKey === `${index}-${dayIndex}`"
                  @click="regenerateDay(message, dayIndex, index)"
                >
                  {{ editingDayKey === `${index}-${dayIndex}` ? '重排中…' : '重排这一天' }}
                </button>
              </div>

              <h3 v-if="day.theme">{{ day.theme }}</h3>

              <ul class="day-items">
                <li v-for="(item, itemIndex) in (day.items || [])" :key="itemIndex">
                  <span class="item-time">{{ item.time }}</span>
                  <span class="item-title">{{ item.title }}</span>
                  <span v-if="item.description" class="item-desc">{{ normalizeTemperature(item.description) }}</span>
                  <button
                    class="item-remove"
                    title="删除这个安排"
                    @click="removeDayItem(message, dayIndex, itemIndex)"
                  >
                    ×
                  </button>
                  <span v-if="item.travel_from_previous" class="item-travel">
                    {{ item.travel_from_previous }}
                  </span>
                </li>
              </ul>

              <p v-if="day.notes" class="day-notes">{{ normalizeTemperature(day.notes) }}</p>
            </div>

            <div v-if="message.itinerary.tips && message.itinerary.tips.length" class="tips">
              <h4>实用提醒</h4>
              <ul>
                <li v-for="(tip, tipIndex) in message.itinerary.tips" :key="tipIndex">{{ tip }}</li>
              </ul>
            </div>

            <div
              v-if="message.itinerary.budget && message.itinerary.budget.per_person"
              class="budget-card"
            >
              <h4>预算参考</h4>
              <div class="budget-main">
                人均 <strong>{{ message.itinerary.budget.per_person }}</strong> 元
                <template v-if="message.itinerary.budget.total">
                  · 2 人合计
                  <strong>{{ message.itinerary.budget.total }}</strong> 元
                </template>
              </div>
              <ul
                v-if="message.itinerary.budget.items && message.itinerary.budget.items.length"
                class="budget-items"
              >
                <li
                  v-for="(item, budgetIndex) in message.itinerary.budget.items"
                  :key="budgetIndex"
                >
                  <span>{{ item.category }}</span>
                  <span>{{ item.amount }} 元</span>
                </li>
              </ul>
              <p v-if="message.itinerary.budget.note" class="budget-note">
                {{ message.itinerary.budget.note }}
              </p>
            </div>

            <p v-if="message.itineraryEdited" class="edited-note">
              已手动调整，复制/下载会使用调整后的行程。
            </p>

            <div class="itinerary-actions">
              <button @click="copyItinerary(message, index)">
                {{ copiedIndex === index ? '已复制' : '复制行程' }}
              </button>
              <button @click="downloadMarkdown(message)">下载 Markdown</button>
            </div>

            <details v-if="!message.itineraryEdited" class="raw-guide">
              <summary>查看文字版</summary>
              <div class="message raw-message" v-html="renderMarkdown(message.content)"></div>
            </details>
          </div>

          <div
            v-else
            class="message"
            v-html="renderMarkdown(message.content)"
          ></div>
        </template>

        <div v-else class="message">
          {{ message.content }}
        </div>

      </div>

      <!-- 加载中 / 等待第一个 token -->
      <div v-if="loading && !hasStreamContent" class="message-row assistant">
        <div class="avatar">🤖</div>
        <div class="message typing-text">{{ statusText }}</div>
      </div>

      <div v-if="loading && hasStreamContent" class="stream-status">
        {{ statusText }}
      </div>

    </div>

    <!-- 重置失败等提示 -->
    <p v-if="errorText" class="error-tip">{{ errorText }}</p>

    <!-- 输入区域 -->
    <div class="input-box">
      <input
        v-model="inputMessage"
        type="text"
        placeholder="请输入你的旅行需求..."
        :disabled="loading"
        @keyup.enter="onEnter"
      />
      <button :disabled="loading" @click="sendMessage">
        {{ loading ? '思考中…' : '发送' }}
      </button>
    </div>

  </div>
</template>

<style>
* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
  background: #f5f7fa;
}

.chat-page {
  width: min(800px, calc(100% - 32px));
  margin: 40px auto;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}

.header-text h1 {
  margin: 0 0 8px;
  font-size: 28px;
}

.header-text p {
  margin: 0;
  color: #777;
  font-size: 14px;
}

.reset-btn {
  flex-shrink: 0;
  padding: 8px 18px;
  border: 1px solid #409eff;
  border-radius: 8px;
  background: #fff;
  color: #409eff;
  font-size: 14px;
  cursor: pointer;
}

.reset-btn:hover {
  background: #ecf5ff;
}

.reset-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.chat-box {
  height: 60vh;
  min-height: 380px;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 12px;
  padding: 25px;
  overflow-y: auto;
  scroll-behavior: smooth;
}

.welcome {
  text-align: center;
  margin-top: 120px;
  color: #666;
}

.welcome-icon {
  font-size: 50px;
}

.welcome h2 {
  color: #333;
}

.message-row {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  align-items: flex-start;
}

.message-row.user {
  flex-direction: row-reverse;
}

.avatar {
  flex-shrink: 0;
  font-size: 25px;
  line-height: 1.4;
}

.message {
  max-width: 78%;
  padding: 12px 16px;
  border-radius: 12px;
  line-height: 1.6;
  font-size: 15px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.message-row.assistant .message {
  background: #f1f3f5;
  white-space: normal;
}

.message-row.user .message {
  background: #d9f0ff;
}

.message-row.error .message {
  background: #fff1f0;
  color: #cf1322;
}

/* 结构化行程卡片 */
.itinerary-card {
  flex: 1;
  max-width: 100%;
  padding: 18px 20px;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
  line-height: 1.6;
  font-size: 15px;
}

.itinerary-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  color: #333;
  font-size: 18px;
}

.itinerary-header span {
  color: #888;
  font-size: 13px;
  font-weight: normal;
}

.itinerary-actions {
  display: flex;
  gap: 8px;
  margin: 10px 0 14px;
}

.itinerary-actions button {
  padding: 4px 12px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  background: #fff;
  color: #409eff;
  font-size: 13px;
  cursor: pointer;
}

.itinerary-actions button:hover {
  background: #ecf5ff;
}

.itinerary-summary {
  margin: 8px 0 16px;
  color: #555;
}

.day-card {
  position: relative;
  margin-bottom: 16px;
  padding: 2px 0 10px 16px;
  border-left: 3px solid #409eff;
}

.day-card::before {
  content: "";
  position: absolute;
  left: -6px;
  top: 8px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #409eff;
}

.day-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.day-date {
  font-weight: 700;
}

.day-weekday {
  color: #888;
  font-size: 13px;
}

.day-weather {
  padding: 2px 8px;
  border-radius: 999px;
  background: #eef6ff;
  color: #2b6cb0;
  font-size: 12px;
}

.day-edit-btn {
  margin-left: auto;
  padding: 2px 8px;
  border: 1px solid #d0d7de;
  border-radius: 6px;
  background: #fff;
  color: #409eff;
  font-size: 12px;
  cursor: pointer;
}

.day-edit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.day-card h3 {
  margin: 6px 0;
  font-size: 16px;
}

.day-items {
  margin: 6px 0;
  padding: 0;
  list-style: none;
}

.day-items li {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 5px 0;
}

.item-time {
  flex-shrink: 0;
  min-width: 36px;
  color: #409eff;
  font-size: 13px;
}

.item-title {
  font-weight: 600;
}

.item-desc {
  color: #666;
}

.item-travel {
  flex-basis: 100%;
  margin-left: 44px;
  color: #999;
  font-size: 12px;
}

.item-remove {
  margin-left: auto;
  border: none;
  background: transparent;
  color: #bbb;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.item-remove:hover {
  color: #cf1322;
}

.day-notes {
  margin: 6px 0 0;
  color: #888;
  font-size: 13px;
}

.edited-note {
  margin: 8px 0 0;
  color: #999;
  font-size: 12px;
}

.tips {
  margin-top: 6px;
  padding-top: 10px;
  border-top: 1px dashed #e5e7eb;
}

.tips h4 {
  margin: 0 0 6px;
  font-size: 14px;
}

.tips ul {
  margin: 0;
  padding-left: 18px;
  color: #555;
}

.budget-card {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #e5e7eb;
}

.budget-card h4 {
  margin: 0 0 6px;
  font-size: 14px;
}

.budget-main {
  color: #333;
  font-size: 15px;
}

.budget-main strong {
  color: #cf1322;
  font-size: 18px;
}

.budget-items {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  color: #666;
  font-size: 13px;
}

.budget-items li {
  display: flex;
  gap: 4px;
}

.budget-items li span:last-child {
  color: #333;
}

.budget-note {
  margin: 6px 0 0;
  color: #999;
  font-size: 12px;
}

.raw-guide {
  margin-top: 12px;
}

.raw-guide summary {
  color: #409eff;
  font-size: 14px;
  cursor: pointer;
}

.raw-message {
  margin-top: 10px;
  max-width: 100%;
}

.message h1,
.message h2,
.message h3 {
  line-height: 1.4;
  margin: 10px 0 8px;
}

.message h1 { font-size: 22px; }
.message h2 { font-size: 19px; }
.message h3 { font-size: 17px; }

.message p {
  margin: 6px 0;
}

.message ul,
.message ol {
  margin: 6px 0;
  padding-left: 24px;
}

.message li {
  margin: 3px 0;
}

.message table {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 14px;
}

.message th,
.message td {
  border: 1px solid #ddd;
  padding: 6px 10px;
  text-align: left;
}

.message strong {
  font-weight: 700;
}

.message code {
  padding: 2px 5px;
  border-radius: 4px;
  background: #e9ecef;
  font-size: 13px;
}

.message pre {
  padding: 10px;
  border-radius: 8px;
  background: #e9ecef;
  overflow-x: auto;
}

.message pre code {
  padding: 0;
  background: transparent;
}

.message.typing-text {
  display: flex;
  align-items: center;
  min-height: 44px;
  color: #666;
  font-size: 14px;
}

.stream-status {
  margin: -8px 0 14px 45px;
  color: #999;
  font-size: 13px;
}

.error-tip {
  margin: 10px 0 0;
  color: #cf1322;
  font-size: 14px;
}

.input-box {
  display: flex;
  gap: 10px;
  margin-top: 15px;
}

.input-box input {
  flex: 1;
  padding: 14px;
  font-size: 16px;
  border: 1px solid #ccc;
  border-radius: 8px;
  outline: none;
}

.input-box input:focus {
  border-color: #409eff;
}

.input-box input:disabled {
  background: #f5f5f5;
}

.input-box button {
  width: 96px;
  border: none;
  border-radius: 8px;
  background: #409eff;
  color: #fff;
  font-size: 16px;
  cursor: pointer;
}

.input-box button:hover:not(:disabled) {
  background: #337ecc;
}

.input-box button:disabled {
  background: #a0cfff;
  cursor: not-allowed;
}

@media (max-width: 600px) {
  .chat-page {
    margin: 16px auto;
  }

  .header {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }

  .chat-box {
    height: 65vh;
    padding: 16px;
  }

  .message {
    max-width: 82%;
  }

  .itinerary-card {
    padding: 14px;
  }

  .welcome {
    margin-top: 80px;
  }
}
</style>