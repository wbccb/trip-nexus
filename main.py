from datetime import datetime

import streamlit as st
from src.frontend.ui_manager import UIManager
from src.rag.rag_main import AIRetrievalPipeline
from src.llm.llm_manager import LlmManager
from src.map.map_renderer import TripMap
from __init__ import __version__, __description__
from src.config import Config
from src.utils.console import console_log


def main() -> None:
    config = Config()
    map_renderer = TripMap()
    llm_manager = LlmManager()
    # rag = AIRetrievalPipeline(llm)
    ui_manager = UIManager(llm_manager, config, map_renderer)

    # 版本信息
    st.sidebar.markdown(f"### 📱 版本: v{__version__}")
    st.sidebar.markdown(f"ℹ️ {__description__}")
    st.sidebar.markdown("---")

    user_id = "wcb"
    device_id = "mac"
    console_log("userId", user_id)
    ui_manager.render_main_interface(user_id, device_id);

if __name__ == "__main__":
    main()
