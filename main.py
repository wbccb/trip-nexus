import os
from datetime import datetime

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import streamlit as st
from src.frontend.ui_manager import UIManager
from src.rag.rag_main import AIRetrievalPipeline
from src.llm.llm_manager import LlmManager
from src.map.map_renderer import TripMap
from __init__ import __version__, __description__
from src.config import Config
from src.utils.console import console_log


@st.cache_resource
def _get_config() -> Config:
    return Config()


@st.cache_resource
def _get_map_renderer() -> TripMap:
    return TripMap()


@st.cache_resource
def _get_llm_manager() -> LlmManager:
    """
    构建并缓存 LlmManager 实例。

    说明：
    - 默认从 Config 中读取“生成阶段”配置作为基础初始化参数；
    - 随后调用 update_llm_config 同步“分析阶段 + 生成阶段”完整配置，
      保证两次调用都与 .env / Config 中的设置对齐；
    - 这样可以实现“开箱即用”的远端 openai_compatible 默认配置，
      同时保留侧边栏动态调整能力。
    """

    # 读取全局配置，包含分析/生成两阶段的 provider/base_url/model 等字段
    cfg = Config()

    # 使用“生成阶段”配置初始化基础 LlmManager（内部会复制为分析/生成两份配置）
    llm_manager = LlmManager(
        model_name=cfg.GENERATION_MODEL_NAME,          # 默认生成模型名称
        ollama_base_url=cfg.GENERATION_BASE_URL,       # 兼容旧参数命名，但会被 base_url 覆盖
        provider=cfg.GENERATION_PROVIDER,              # 默认提供方（现为 openai_compatible）
        base_url=cfg.GENERATION_BASE_URL,              # 默认 Base URL（远端 /v1 网关）
        api_key=cfg.GENERATION_API_KEY,                # 生成阶段 API Key
        temperature=cfg.GENERATION_TEMPERATURE,        # 生成阶段温度
    )

    # 通过统一的 update_llm_config 接口，将分析/生成两阶段配置一次性同步进去
    llm_manager.update_llm_config(
        {
            # 通用兜底配置（如果某些分析/生成字段缺失会回落到这些值）
            "provider": cfg.GENERATION_PROVIDER,
            "base_url": cfg.GENERATION_BASE_URL,
            "model_name": cfg.GENERATION_MODEL_NAME,
            "api_key": cfg.GENERATION_API_KEY,
            "temperature": cfg.GENERATION_TEMPERATURE,
            # 分析阶段专用配置（第 1 次调用）
            "analysis_provider": cfg.ANALYSIS_PROVIDER,
            "analysis_base_url": cfg.ANALYSIS_BASE_URL,
            "analysis_model_name": cfg.ANALYSIS_MODEL_NAME,
            "analysis_api_key": cfg.ANALYSIS_API_KEY,
            "analysis_temperature": cfg.ANALYSIS_TEMPERATURE,
            # 生成阶段专用配置（第 2 次调用）
            "generation_provider": cfg.GENERATION_PROVIDER,
            "generation_base_url": cfg.GENERATION_BASE_URL,
            "generation_model_name": cfg.GENERATION_MODEL_NAME,
            "generation_api_key": cfg.GENERATION_API_KEY,
            "generation_temperature": cfg.GENERATION_TEMPERATURE,
        }
    )

    # 返回已经按 Config 完整配置好的 LlmManager 实例
    return llm_manager


def main() -> None:
    st.switch_page("pages/agent.py")
    return
    config = _get_config()
    map_renderer = _get_map_renderer()
    llm_manager = _get_llm_manager()
    # rag = AIRetrievalPipeline(llm)
    ui_manager = UIManager(llm_manager, config, map_renderer)


    user_id = "wcb"
    device_id = "mac"
    console_log("userId", user_id)
    ui_manager.render_main_interface(user_id, device_id);

if __name__ == "__main__":
    main()
