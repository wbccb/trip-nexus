from datetime import datetime

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
    return LlmManager()


def main() -> None:
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
