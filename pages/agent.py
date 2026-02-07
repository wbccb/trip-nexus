import os

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import streamlit as st

from src.frontend.ui_manager import UIManager
from src.llm.llm_manager import LlmManager
from src.map.map_renderer import TripMap
from src.config import Config


@st.cache_resource
def _get_config() -> Config:
    return Config()


@st.cache_resource
def _get_map_renderer() -> TripMap:
    return TripMap()


@st.cache_resource
def _get_llm_manager() -> LlmManager:
    cfg = Config()
    llm_manager = LlmManager(
        model_name=cfg.GENERATION_MODEL_NAME,
        ollama_base_url=cfg.GENERATION_BASE_URL,
        provider=cfg.GENERATION_PROVIDER,
        base_url=cfg.GENERATION_BASE_URL,
        api_key=cfg.GENERATION_API_KEY,
        temperature=cfg.GENERATION_TEMPERATURE,
    )
    llm_manager.update_llm_config(
        {
            "provider": cfg.GENERATION_PROVIDER,
            "base_url": cfg.GENERATION_BASE_URL,
            "model_name": cfg.GENERATION_MODEL_NAME,
            "api_key": cfg.GENERATION_API_KEY,
            "temperature": cfg.GENERATION_TEMPERATURE,
            "analysis_provider": cfg.ANALYSIS_PROVIDER,
            "analysis_base_url": cfg.ANALYSIS_BASE_URL,
            "analysis_model_name": cfg.ANALYSIS_MODEL_NAME,
            "analysis_api_key": cfg.ANALYSIS_API_KEY,
            "analysis_temperature": cfg.ANALYSIS_TEMPERATURE,
            "generation_provider": cfg.GENERATION_PROVIDER,
            "generation_base_url": cfg.GENERATION_BASE_URL,
            "generation_model_name": cfg.GENERATION_MODEL_NAME,
            "generation_api_key": cfg.GENERATION_API_KEY,
            "generation_temperature": cfg.GENERATION_TEMPERATURE,
        }
    )
    return llm_manager


def main() -> None:
    config = _get_config()
    map_renderer = _get_map_renderer()
    llm_manager = _get_llm_manager()
    ui_manager = UIManager(llm_manager, config, map_renderer)
    st.title("Agent 调试")

    with st.sidebar:
        st.subheader("LLM 设置")
        ui_manager.render_llm_settings()

    left_col, right_col = st.columns([0.48, 0.52])

    with left_col:
        ui_manager.agent_ui.render_debug_panel()

    with right_col:
        ui_manager.agent_ui.render_status_panel(floating=False)


if __name__ == "__main__":
    main()
