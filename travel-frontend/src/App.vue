<script setup>
import { marked } from 'marked'
import { ref } from 'vue'

const inputMessage = ref('')
const messages = ref([])

async function sendMessage() {
  if (!inputMessage.value.trim()) {
    return
  }

  const message = inputMessage.value

  // 先显示用户消息
  messages.value.push({
    type: 'user',
    content: message
  })

  inputMessage.value = ''

  // 请求后端
  const response = await fetch('http://127.0.0.1:8000/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      message: message
    })
  })

  const data = await response.json()

  // 显示 AI 回复
  messages.value.push({
    type: 'assistant',
    content: data.reply
  })
}
</script>

<template>
  <div class="chat-page">

    <!-- 顶部标题 -->
    <div class="header">
      <h1>AI 旅行规划助手</h1>
      <p>告诉我你的旅行需求，我来帮你规划行程</p>
    </div>

    <!-- 聊天区域 -->
    <div class="chat-box">

      <!-- 欢迎语 -->
      <div v-if="messages.length === 0" class="welcome">
        <div class="welcome-icon">✈️</div>
        <h2>你好！</h2>
        <p>你可以告诉我：</p>
        <p>“我想去北京玩 3 天，9 月 15 号出发”</p>
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
          {{ message.type === 'user' ? '👤' : '🤖' }}
        </div>

        <!-- 消息内容 -->
        <div
          class="message"
          v-html="
            message.type === 'assistant'
              ? marked(message.content)
              : message.content
          "
        ></div>

      </div>

    </div>

    <!-- 输入区域 -->
    <div class="input-box">

      <input
        v-model="inputMessage"
        type="text"
        placeholder="请输入你的旅行需求..."
        @keyup.enter="sendMessage"
      />

      <button @click="sendMessage">
        发送
      </button>

    </div>

  </div>
</template>

<style>
/* =========================
   全局
   ========================= */

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: Arial, sans-serif;
  background: #f5f7fa;
}


/* =========================
   页面
   ========================= */

.chat-page {
  width: 800px;
  margin: 40px auto;
}


/* =========================
   顶部标题
   ========================= */

.header {
  text-align: center;
  margin-bottom: 20px;
}

.header h1 {
  margin-bottom: 8px;
}

.header p {
  color: #777;
  margin: 0;
}


/* =========================
   聊天框
   ========================= */

.chat-box {
  height: 600px;
  background: white;
  border: 1px solid #ddd;
  border-radius: 12px;
  padding: 25px;
  overflow-y: auto;
}


/* =========================
   欢迎语
   ========================= */

.welcome {
  text-align: center;
  margin-top: 150px;
  color: #666;
}

.welcome-icon {
  font-size: 50px;
}

.welcome h2 {
  color: #333;
}


/* =========================
   消息
   ========================= */

.message-row {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  align-items: flex-start;
}


/* 用户消息靠右 */
.message-row.user {
  flex-direction: row-reverse;
}


/* 头像 */
.avatar {
  font-size: 25px;
}


/* 消息气泡 */
.message {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 12px;
  white-space: pre-wrap;
  line-height: 1.6;
  font-size: 15px;
}


/* AI 消息 */
.message-row.assistant .message {
  background: #f1f3f5;
}


/* 用户消息 */
.message-row.user .message {
  background: #d9f0ff;
}


/* =========================
   Markdown 样式
   ========================= */

/* 标题 */
.message h1,
.message h2,
.message h3 {
  line-height: 1.4;
  margin-top: 10px;
  margin-bottom: 8px;
}


/* 一级标题 */
.message h1 {
  font-size: 22px;
}


/* 二级标题 */
.message h2 {
  font-size: 19px;
}


/* 三级标题 */
.message h3 {
  font-size: 17px;
}


/* 普通段落 */
.message p {
  margin: 6px 0;
}


/* 无序列表 / 有序列表 */
.message ul,
.message ol {
  margin: 6px 0;
  padding-left: 24px;
}


/* 列表项目 */
.message li {
  margin: 3px 0;
}


/* 表格 */
.message table {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
}


/* 表格单元格 */
.message th,
.message td {
  border: 1px solid #ddd;
  padding: 6px 10px;
  text-align: left;
}


/* 加粗 */
.message strong {
  font-weight: 700;
}


/* =========================
   输入区域
   ========================= */

.input-box {
  display: flex;
  gap: 10px;
  margin-top: 15px;
}


/* 输入框 */
.input-box input {
  flex: 1;
  padding: 14px;
  font-size: 16px;
  border: 1px solid #ccc;
  border-radius: 8px;
  outline: none;
}


/* 输入框获得焦点 */
.input-box input:focus {
  border-color: #409eff;
}


/* 发送按钮 */
.input-box button {
  width: 90px;
  border: none;
  border-radius: 8px;
  background: #409eff;
  color: white;
  font-size: 16px;
  cursor: pointer;
}


/* 鼠标悬停 */
.input-box button:hover {
  background: #337ecc;
}
</style>