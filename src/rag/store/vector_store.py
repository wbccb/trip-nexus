from typing import List, Dict, Any
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from chromadb import EphemeralClient, PersistentClient
from src.config import Config
from src.rag.module.intent_recognition import _get_sentence_transformer
import logging
import os
import re
import time

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
        self.persist_directory = os.path.abspath(self.config.CHROMA_DB_PATH)
        self.collection_name = self._normalize_collection_name(collection_name)
        self.client = None
        self.vector_db = None
        self.client_mode = "persistent"
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.RAG_CHUNK_SIZE,
            chunk_overlap=self.config.RAG_CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]
        )
        self._init_db()

    def _normalize_collection_name(self, collection_name: str) -> str:
        """规范化集合名，确保可持久化且命名安全。"""
        raw_value = str(collection_name or "").strip().lower()
        if not raw_value:
            raw_value = "default"
        normalized = re.sub(r"[^a-z0-9_\-]", "_", raw_value)
        return normalized[:63] or "default"

    def _init_db(self):
        """初始化向量数据库"""
        try:
            self.client = self._build_client()
            self.client.get_or_create_collection(name=self.collection_name)
            self.vector_db = Chroma(
                collection_name=self.collection_name,
                client=self.client,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory
            )
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            raise

    def _build_client(self):
        """构建 Chroma client，初始化失败时自动修复持久化目录后重试。"""
        try:
            self.client_mode = "persistent"
            return PersistentClient(path=self.persist_directory)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            logger.warning(f"PersistentClient init failed, start repair: {exc}")
            self._repair_persist_directory()
            try:
                self.client_mode = "persistent"
                return PersistentClient(path=self.persist_directory)
            except BaseException as retry_exc:
                if isinstance(retry_exc, (KeyboardInterrupt, SystemExit)):
                    raise
                logger.warning(f"PersistentClient retry failed, fallback to EphemeralClient: {retry_exc}")
                self.client_mode = "ephemeral"
                return EphemeralClient()

    def _repair_persist_directory(self) -> None:
        """修复持久化目录：备份损坏目录并重建空目录。"""
        target_dir = os.path.abspath(self.persist_directory)
        if os.path.isdir(target_dir):
            backup_dir = f"{target_dir}_broken_{int(time.time())}"
            os.rename(target_dir, backup_dir)
            logger.warning(f"Chroma persist directory moved to backup: {backup_dir}")
        os.makedirs(target_dir, exist_ok=True)

    def switch_collection(self, collection_name: str, create_if_missing: bool = True) -> None:
        """切换当前集合，用于多知识库并行管理。"""
        next_collection_name = self._normalize_collection_name(collection_name)
        if create_if_missing:
            self.client.get_or_create_collection(name=next_collection_name)
        self.collection_name = next_collection_name
        self.vector_db = Chroma(
            collection_name=self.collection_name,
            client=self.client,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

    def create_collection(self, collection_name: str) -> str:
        """创建集合并返回规范化后的集合名。"""
        normalized_name = self._normalize_collection_name(collection_name)
        self.client.get_or_create_collection(name=normalized_name)
        return normalized_name

    def list_collections(self) -> List[str]:
        """列出当前持久化目录下的全部集合名。"""
        try:
            collections = self.client.list_collections()
            return [str(item.name) for item in collections]
        except Exception as e:
            logger.error(f"Error listing collections: {e}")
            return []

    def delete_collection(self, collection_name: str) -> bool:
        """删除指定集合，删除成功返回 True。"""
        normalized_name = self._normalize_collection_name(collection_name)
        try:
            self.client.delete_collection(name=normalized_name)
            if normalized_name == self.collection_name:
                fallback_collection = "web_search_cache"
                self.switch_collection(fallback_collection, create_if_missing=True)
            return True
        except Exception as e:
            logger.error(f"Error deleting collection {normalized_name}: {e}")
            return False

    def add_documents(self, documents: List[Dict[str, Any]]) -> int:
        """
        添加文档到向量数据库
        documents: List of dict with 'content' and 'metadata'
        """
        if not documents:
            return 0

        docs_to_add = []
        for doc in documents:
            text = doc.get('content', '')
            metadata = doc.get('metadata', {})
            
            # 分割文本
            splits = self.text_splitter.create_documents([text], metadatas=[metadata])
            total_chunks = len(splits)
            for chunk_index, split_doc in enumerate(splits):
                chunk_metadata = dict(split_doc.metadata or {})
                chunk_metadata["chunk_index"] = chunk_index + 1
                chunk_metadata["chunk_total"] = total_chunks
                split_doc.metadata = chunk_metadata
            docs_to_add.extend(splits)

        if docs_to_add:
            try:
                self.vector_db.add_documents(docs_to_add)
                logger.debug(f"Added {len(docs_to_add)} chunks to vector store")
                return len(docs_to_add)
            except Exception as e:
                logger.error(f"Error adding documents to vector store: {e}")
        return 0

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
                logger.debug(f"Cleared {len(ids)} documents from collection {self.collection_name}")
        except Exception as e:
            logger.error(f"Error clearing vector store: {e}")
