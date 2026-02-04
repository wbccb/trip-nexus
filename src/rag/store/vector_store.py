from typing import List, Dict, Any, Optional
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from src.config import Config
from src.rag.module.intent_recognition import _get_sentence_transformer
import logging
import shutil
import os

logger = logging.getLogger(__name__)


class _SentenceTransformerEmbeddings:
    def __init__(self, model_name: str) -> None:
        self._model = _get_sentence_transformer(model_name)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._model.encode(texts, show_progress_bar=False).tolist()

    def embed_query(self, text: str) -> List[float]:
        return self._model.encode([text], show_progress_bar=False)[0].tolist()


class VectorStore:
    def __init__(self, collection_name: str = "web_search_cache"):
        self.config = Config()
        self.embeddings = _SentenceTransformerEmbeddings(self.config.SENTENCE_BERT_MODEL)
        self.persist_directory = self.config.CHROMA_DB_PATH
        self.collection_name = collection_name
        self.vector_db = None
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.RAG_CHUNK_SIZE,
            chunk_overlap=self.config.RAG_CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]
        )
        self._init_db()

    def _init_db(self):
        """初始化向量数据库"""
        try:
            self.vector_db = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory
            )
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            raise

    def add_documents(self, documents: List[Dict[str, Any]]) -> None:
        """
        添加文档到向量数据库
        documents: List of dict with 'content' and 'metadata'
        """
        if not documents:
            return

        docs_to_add = []
        for doc in documents:
            text = doc.get('content', '')
            metadata = doc.get('metadata', {})
            
            # 分割文本
            splits = self.text_splitter.create_documents([text], metadatas=[metadata])
            docs_to_add.extend(splits)

        if docs_to_add:
            try:
                self.vector_db.add_documents(docs_to_add)
                logger.info(f"Added {len(docs_to_add)} chunks to vector store")
            except Exception as e:
                logger.error(f"Error adding documents to vector store: {e}")

    def similarity_search(self, query: str, k: int = 5) -> List[Document]:
        """
        相似度搜索
        """
        try:
            return self.vector_db.similarity_search(query, k=k)
        except Exception as e:
            logger.error(f"Error during similarity search: {e}")
            return []

    def clear(self):
        """
        清空当前集合 (用于新的搜索会话)
        注意：Chroma的delete_collection比较彻底，这里简单实现为删除所有数据
        """
        try:
            # 获取所有ID并删除
            ids = self.vector_db.get()['ids']
            if ids:
                self.vector_db.delete(ids=ids)
                logger.info(f"Cleared {len(ids)} documents from collection {self.collection_name}")
        except Exception as e:
            logger.error(f"Error clearing vector store: {e}")
