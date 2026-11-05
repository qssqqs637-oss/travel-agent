# AI 旅行规划助手

一个基于 LangGraph 的旅行规划 Demo：用户用自然语言描述旅行需求，系统补全城市/日期/天数，调用天气和景点工具，最后生成每日行程攻略。

## 技术栈

- 后端：FastAPI + LangGraph + LangChain + DeepSeek
- 前端：Vue 3 + Vite + marked + DOMPurify
- 数据：Open-Meteo（免费天气，无需 Key）

## 功能

- 多轮对话补全旅行需求（城市、开始日期、天数）
- 天气查询（未来 15 天）与景点查询
- 按天气安排每日行程，生成 Markdown 攻略
- 结构化行程卡片，支持单日重排和删除景点
- 景点之间自动计算车程和距离（配置高德 Key 后）
- 参考预算（人均 / 2 人合计）
- SSE 流式输出，边生成边显示
- 每个浏览器标签页独立会话，支持“新对话”
- 过去日期、格式错误会明确提示

## 目录结构

```text
pythonProject1/
├── main.py                 # FastAPI 入口（/chat、/chat/stream、/reset）
├── graph/
│   ├── state.py            # LangGraph State 定义
│   └── workflow.py         # 规划、执行、合成等节点
├── travel-frontend/        # Vue 3 + Vite 前端
├── requirements.txt
├── .env.example
└── README.md
```

## 快速开始

### 1. 后端

```powershell
cd D:\python\pythonProject1
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# 编辑 .env，填入真实的 DEEPSEEK_API_KEY
uvicorn main:app --reload --port 8000
```

启动成功后访问：

- <http://127.0.0.1:8000/> → `{"message":"Hello Agent"}`
- <http://127.0.0.1:8000/docs> → Swagger 接口文档

### 2. 前端

```powershell
cd D:\python\pythonProject1\travel-frontend
npm install
npm run dev
```

浏览器打开 Vite 提示的地址（默认 <http://localhost:5173>）。

> 前端通过 `VITE_API_BASE` 指定后端地址，默认 `http://127.0.0.1:8000`。
> 需要修改时，在 `travel-frontend` 下新建 `.env.local`：
>
> ```dotenv
> VITE_API_BASE=http://127.0.0.1:8000
> ```

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `LLM_PROVIDER` | 否 | `deepseek` | `deepseek` / `dashscope` / `openai-compatible` |
| `LLM_BASE_URL` | 否 | 百炼兼容地址 | 非 deepseek 时使用 |
| `LLM_MODEL` | 否 | `qwen-plus` | 非 deepseek 时使用 |
| `LLM_API_KEY` | 条件 | — | 非 deepseek 时使用 |
| `DEEPSEEK_API_KEY` | 条件 | — | `LLM_PROVIDER=deepseek` 时必填 |
| `DEEPSEEK_API_URL` | 否 | `https://api.deepseek.com` | `LLM_PROVIDER=deepseek` 时使用 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-flash` | `LLM_PROVIDER=deepseek` 时使用 |
| `GUIDE_MAX_TOKENS` | 否 | `1200` | 攻略最大输出 token |
| `HISTORY_TURNS` | 否 | `8` | 发给模型的历史对话轮数 |
| `STRUCTURED_METHOD` | 否 | `function_calling` | 结构化输出方式；百炼模型用 `function_calling` |
| `CHECKPOINT_DB` | 否 | `checkpoints.sqlite` | 会话数据库文件路径 |
| `AMAP_API_KEY` | 否 | — | 高德 Web 服务 Key；不填则用内置景点 |
| `ROOT_PATH` | 否 | — | 反向代理前缀；Nginx `/api` 部署时设为 `/api` |

### 使用阿里云百炼（可选）

项目支持 OpenAI 兼容接口，可以切到阿里云百炼的 Qwen：

1. 登录 [阿里云百炼控制台](https://bailian.console.aliyun.com/)，完成实名认证
2. API-KEY 管理 → 创建 API Key
3. 在模型广场确认免费额度与可用模型（如 `qwen-plus`、`qwen-turbo`）
4. `.env` 配置：

```dotenv
LLM_PROVIDER=dashscope
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
LLM_API_KEY=sk-你的百炼Key
```

5. 重启后端

> 免费额度、模型名和有效期以百炼控制台为准。
> 如果 Qwen 对强制 `tool_choice` 支持不好，可以把 `with_structured_output` 改成 `method="json_mode"`。

### 配置高德景点数据（可选）

不配置也能用，但只有北京/上海/杭州的内置景点。配置后支持任意城市：

1. 登录 [高德开放平台](https://console.amap.com/)，完成实名认证
2. 应用管理 → 创建应用 → 添加 Key
3. 服务平台选择 **Web服务**
4. 把 Key 写入 `.env`：

```dotenv
AMAP_API_KEY=你的Key
```

5. 重启后端

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 健康检查 |
| GET | `/health` | 健康检查 |
| POST | `/chat` | 一次性返回完整回复 |
| POST | `/chat/stream` | SSE 流式返回（前端使用） |
| POST | `/itinerary/day` | 重排行程中的某一天 |
| POST | `/reset` | 删除当前会话的 checkpoint |
| GET | `/docs` | Swagger 文档 |

请求体：

```json
{
  "message": "我想去北京玩 3 天，10 月 28 号出发",
  "session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

`session_id` 由前端生成（`crypto.randomUUID()`），长度 8~64，只允许字母、数字、下划线、短横线。

## 测试

纯逻辑测试，不调用模型和高德 API：

```bash
pip install -r requirements-dev.txt
python -m pytest
```

覆盖：

- 时区（UTC+8）
- 温度区间归一化
- 历史消息截断（不会留下孤立 ToolMessage）
- 行程合并（新计划、换城市、沿用旧日程、日期/天数校验）
- 车程格式化（分钟、小时、异常值）
- 预算区间解析与加总

## 部署

云服务器（Ubuntu + Nginx + systemd）的完整步骤见 [deploy/README.md](deploy/README.md)。

配套文件：

- `deploy/nginx.conf`
- `deploy/travel-planner.service`

## 已知限制

- 会话保存在 `checkpoints.sqlite`（SQLite），重启后端不会丢失；删除该文件即可清空所有会话。
- 天气接口只支持未来 15 天，超出范围会明确提示“天气暂缺”。
- 过去的日期会被拒绝，并提示用户重新选择。
- 景点默认使用内置字典（北京/上海/杭州）；配置 `AMAP_API_KEY` 后改用高德真实 POI，支持任意城市。

## 常见问题

**`ModuleNotFoundError: No module named 'graph'`**

工作目录不对。必须在 `D:\python\pythonProject1` 下启动 `uvicorn`。

**`ModuleNotFoundError: No module named 'langgraph'`**

用错了解释器。确认虚拟环境已激活，或使用完整路径：

```powershell
& "D:\python\pythonProject\.venv1\Scripts\python.exe" -m uvicorn main:app --reload --port 8000
```

**`Error code: 400 - Thinking mode does not support this tool_choice`**

`deepseek-flash` 默认开启思考模式，而思考模式不支持强制 `tool_choice`。
代码中已经通过 `extra_body={"thinking": {"type": "disabled"}}` 关闭，确认没有被改掉。

**端口 8000 被占用**

```powershell
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

或者换端口启动，同时修改前端 `VITE_API_BASE`。

**“新对话”提示重置失败**

后端需要是新版 `main.py`，并且提供 `/reset` 接口。
