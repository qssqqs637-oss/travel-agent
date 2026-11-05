"""模型工厂：通过环境变量在 DeepSeek / 阿里云百炼 / 其他 OpenAI 兼容服务之间切换。"""

import os

from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI


def build_model():
    provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()

    if provider == "deepseek":
        return ChatDeepSeek(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv(
                "DEEPSEEK_API_URL", "https://api.deepseek.com"
            ),
            temperature=0.7,
            timeout=120,
            max_retries=1,
            # 思考模式不支持强制 tool_choice
            extra_body={"thinking": {"type": "disabled"}},
        )

    model_name = os.getenv("LLM_MODEL", "qwen-plus").strip()
    kwargs = {
        "model": model_name,
        "api_key": os.getenv("LLM_API_KEY", ""),
        "base_url": os.getenv(
            "LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        "temperature": 0.7,
        "timeout": 120,
        "max_retries": 1,
    }

    # Qwen3 默认可能开启思考，和结构化输出/工具调用容易冲突
    if provider in ("dashscope", "qwen") and model_name.startswith("qwen3"):
        kwargs["extra_body"] = {"enable_thinking": False}

    return ChatOpenAI(**kwargs)
