# TripNexus
An AI-planned travel product


## 版本

### v0.0.1
1. frontend: 用户可以手动输入地点、天数、预算进行【行程生成】
2. rag: 用户可以输入链接进行爬虫获取数据，去除冗杂标签，切割后转化为向量存入向量数据库中
3. llm: 根据用户的输入 + rag数据生成prompt，进行本地Ollama的模型调用
4. map: 根据llm转化为地图数据进行Marker的添加显示 + 文字显示llm的行程规划列表


### v0.0.2(开发中)
1. frontend: 实现前端对话式更新行程
2. rag: 爬虫优化，提高数据采集稳定性
3. llm: LLM 双模式支持（线上 + 本地）
4. map: 多地图切换 + POI 样式定制:基于 CSS Sprite+SVG，实现轻量、清晰的分类渲染 + POI 层级渲染：通过四叉树实现按需渲染，避免遮挡与卡顿

## 环境初始化

```shell
pyenv local 3.12.0
# 创建虚拟环境
python -m venv venv
# 激活环境
source venv/bin/activate
# 安装核心依赖
pip install -r requirements.txt
```

> 如果使用`PyCharm`，需要设置`Python Interpreter` -> `/xxxx/TripNexus/venv/bin/python`


## 安装本地模型

安装`Ollama`的`deepseek-r1:7b`并且运行模型

## 前端运行

```shell
streamlit run main.py
```
