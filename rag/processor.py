from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.document_loaders import PlaywrightURLLoader
from bs4 import BeautifulSoup
import re
from pathlib import Path
from typing import List, Optional


class TripRAG:
    def __init__(self, persist_dir: str = "./chroma_db"):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(exist_ok=True)

        # 初始化嵌入模型（LangChain 1.x 导入路径）
        self.embeddings = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True}
        )

        # 初始化Chroma向量库（使用langchain-chroma 1.0.0）
        self.vector_db = Chroma(
            persist_directory=str(self.persist_dir),
            embedding_function=self.embeddings,
            collection_name="trip_guides"
        )

        # 文本分割器（langchain-text-splitters 1.0.0）
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            separators=["。", "！", "？", "\n", "，", " "]
        )

    def _clean_html_text(self, html_content: str) -> str:
        """清洗HTML文本，保留核心攻略内容"""
        soup = BeautifulSoup(html_content, "html.parser")
        # 移除广告和冗余标签
        for tag in soup.find_all(["script", "style", "div", "span"]):
            if "ad" in tag.get("class", []) or "广告" in tag.text:
                tag.decompose()
        text = soup.get_text()
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def load_and_store_guides(self, urls: List[str]) -> None:
        """加载攻略并存储到Chroma 1.x"""
        if not urls:
            return

        loader = PlaywrightURLLoader(
            urls=urls,
            headless=True,
            remove_selectors=["header", "footer", ".ad-container", ".promotion"],
            wait_time=2000
        )

        try:
            docs = loader.load()
        except Exception as e:
            print(f"攻略加载失败：{str(e)}")
            return

        # 清洗并分割文本
        for doc in docs:
            doc.page_content = self._clean_html_text(doc.page_content)
        split_docs = self.text_splitter.split_documents(docs)

        # 存入向量库（Chroma 1.x 兼容API）
        if split_docs:
            self.vector_db.add_documents(split_docs)
            self.vector_db.persist()

    def retrieve_relevant_info(self, query: str, k: int = 3) -> List[str]:
        """检索相关攻略信息"""
        try:
            docs = self.vector_db.similarity_search(query, k=k)
            return [doc.page_content for doc in docs if len(doc.page_content) > 50]
        except Exception as e:
            print(f"检索失败：{str(e)}")
            return []