from typing import List, Dict, Any
import numpy as np
from sentence_transformers import CrossEncoder
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.document_loaders import WebBaseLoader
import time
from src.config import Config

class QualityFilter:
    def __init__(self):
        self.config = Config()


    def filter_and_rank(self, results: List[Dict[str, any]], query:str) -> List[Dict[str, any]]:
        """
        对搜索结果进行质量过滤和重排序
        """