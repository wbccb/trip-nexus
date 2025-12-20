import streamlit as st
import json

def console_log(message: str, data=None):
    """封装浏览器console.log的函数"""
    # 若有数据，转为JSON字符串；否则直接传消息
    if data is not None:
        # 处理非JSON序列化的对象（如Message）
        if hasattr(data, 'model_dump'):  # Pydantic模型
            data = data.model_dump()
        elif hasattr(data, '__dict__'):  # 普通类实例
            data = data.__dict__
        data_str = json.dumps(data, ensure_ascii=False)
        js_code = f"""
        <script>
            console.log("{message}", {data_str});
        </script>
        """
    else:
        js_code = f"""
        <script>
            console.log("{message}");
        </script>
        """
    st.components.v1.html(js_code, height=0)  # height=0隐藏HTML组件