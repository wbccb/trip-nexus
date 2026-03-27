import hashlib
import json
import logging
import re
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException
from langchain_core.documents.base import Document
from pypdf import PdfReader

from src.rag.network.content_validator import validate_content_quality
from src.rag.network.crawler import ContentCrawler
from src.rag.network.url_preprocessor import infer_source_platform, preprocess_url
from src.api.schemas.knowledge import KnowledgeBaseItem
from src.api.dependencies import (
    _get_knowledge_store,
    _normalize_knowledge_base_id,
)

logger = logging.getLogger(__name__)

KNOWLEDGE_COLLECTION_PREFIX = "kb_"
KNOWLEDGE_REGISTRY_COLLECTION = "kb_registry"
FAILED_SOURCE_RECORD_TYPE = "failed_source"
SOCIAL_SOURCE_PLATFORMS = {"xiaohongshu", "wechat", "bilibili", "douyin", "zhihu", "ctrip"}

def _to_collection_name(knowledge_base_id: str) -> str:
    """将业务知识库ID映射为向量集合名。"""
    normalized_id = _normalize_knowledge_base_id(knowledge_base_id).lower()
    safe_collection_id = re.sub(r"[^a-z0-9_\-]", "_", normalized_id)
    return f"{KNOWLEDGE_COLLECTION_PREFIX}{safe_collection_id}"[:63]


def _build_kb_query(destination: str, days: int, budget: Optional[str], preference: Optional[str], override_query: Optional[str]) -> str:
    """构造知识库检索查询，支持用户自定义覆盖。"""
    if override_query and override_query.strip():
        return override_query.strip()
    query_parts = [
        f"目的地:{destination}",
        f"天数:{days}",
        f"预算:{budget or '未指定'}",
        f"偏好:{preference or '未指定'}",
        "行程建议",
    ]
    return " ".join(query_parts)


def _load_knowledge_base_registry() -> List[Dict[str, str]]:
    """从 registry 集合加载知识库定义列表。"""
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    payload = store.vector_db.get(include=["metadatas"])
    metadata_list = payload.get("metadatas") if isinstance(payload, dict) else []
    rows: List[Dict[str, str]] = []
    if not isinstance(metadata_list, list):
        return rows
    for metadata in metadata_list:
        if not isinstance(metadata, dict):
            continue
        knowledge_base_id = str(metadata.get("knowledge_base_id") or "").strip()
        collection_name = str(metadata.get("collection_name") or "").strip()
        name = str(metadata.get("name") or "").strip()
        if not knowledge_base_id or not collection_name or not name:
            continue
        rows.append(
            {
                "knowledge_base_id": knowledge_base_id,
                "name": name,
                "collection_name": collection_name,
            }
        )
    unique_map: Dict[str, Dict[str, str]] = {}
    for row in rows:
        unique_map[row["knowledge_base_id"]] = row
    return list(unique_map.values())


def _upsert_knowledge_base_registry(knowledge_base_id: str, name: str, collection_name: str) -> None:
    """写入或更新知识库 registry 记录。"""
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    existing = store.vector_db.get(
        where=_build_chroma_where(
            knowledge_base_id=knowledge_base_id,
            record_type="knowledge_base",
        ),
        include=["metadatas"],
    )
    existing_ids = existing.get("ids") if isinstance(existing, dict) else []
    if isinstance(existing_ids, list) and existing_ids:
        store.vector_db.delete(ids=existing_ids)
    store.add_documents(
        [
            {
                "content": f"knowledge_base:{knowledge_base_id}",
                "metadata": {
                    "record_type": "knowledge_base",
                    "knowledge_base_id": knowledge_base_id,
                    "name": name,
                    "collection_name": collection_name,
                },
            }
        ]
    )


def _delete_knowledge_base_registry(knowledge_base_id: str) -> None:
    """删除知识库 registry 记录。"""
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    existing = store.vector_db.get(
        where=_build_chroma_where(
            knowledge_base_id=knowledge_base_id,
            record_type="knowledge_base",
        ),
        include=["metadatas"],
    )
    existing_ids = existing.get("ids") if isinstance(existing, dict) else []
    if isinstance(existing_ids, list) and existing_ids:
        store.vector_db.delete(ids=existing_ids)


def _build_chroma_where(**filters: Any) -> Dict[str, Any]:
    """构造兼容 Chroma 的 metadata 等值过滤条件。"""
    normalized_filters = {key: value for key, value in filters.items() if value is not None}
    if not normalized_filters:
        return {}
    if len(normalized_filters) == 1:
        return normalized_filters
    return {"$and": [{key: value} for key, value in normalized_filters.items()]}


def _load_failed_source_entries(knowledge_base_id: str) -> List[Dict[str, Any]]:
    """从 registry 集合加载指定知识库的失败来源记录。"""
    normalized_id = str(knowledge_base_id or "").strip()
    if not normalized_id:
        return []
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    payload = store.vector_db.get(
        where=_build_chroma_where(
            knowledge_base_id=normalized_id,
            record_type=FAILED_SOURCE_RECORD_TYPE,
        ),
        include=["metadatas"],
    )
    metadata_rows = payload.get("metadatas") if isinstance(payload, dict) else []
    if not isinstance(metadata_rows, list):
        return []
    items: List[Dict[str, Any]] = []
    for metadata in metadata_rows:
        if not isinstance(metadata, dict):
            continue
        source_id = str(metadata.get("source_id") or "").strip()
        if not source_id:
            continue
        items.append(
            {
                "source_id": source_id,
                "source_type": str(metadata.get("source_type") or "url"),
                "source_platform": str(metadata.get("source_platform") or "unknown"),
                "source_url": str(metadata.get("source_url") or ""),
                "author": metadata.get("author"),
                "ingest_mode": str(metadata.get("ingest_mode") or "auto"),
                "ingest_status": "failed",
                "ingest_error_code": metadata.get("ingest_error_code"),
                "ingested_at": metadata.get("ingested_at"),
                "expires_at": metadata.get("expires_at"),
                "chunks_count": 0,
                "parsed_content_preview": str(metadata.get("parsed_content_preview") or ""),
                "parsed_content_chars": int(metadata.get("parsed_content_chars") or 0),
                "normalized_url": metadata.get("normalized_url"),
                "resolved_url": metadata.get("resolved_url"),
                "source_risk_level": metadata.get("source_risk_level"),
                "extractor_layer": metadata.get("extractor_layer"),
                "quality_score": metadata.get("quality_score"),
                "failure_reason": metadata.get("failure_reason"),
                "retry_count": int(metadata.get("retry_count") or 0),
                "last_retry_at": metadata.get("last_retry_at"),
                "chunk_ids": [],
            }
        )
    return sorted(items, key=lambda item: str(item.get("ingested_at") or ""), reverse=True)


def _upsert_failed_source_entry(metadata: Dict[str, Any], parsed_preview_text: str = "", parsed_chars: int = 0) -> None:
    """将失败来源写入 registry 集合，保证来源列表与重试链路可见。"""
    source_id = str((metadata or {}).get("source_id") or "").strip()
    knowledge_base_id = str((metadata or {}).get("knowledge_base_id") or "").strip()
    if not source_id or not knowledge_base_id:
        return
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    existing = store.vector_db.get(
        where=_build_chroma_where(
            knowledge_base_id=knowledge_base_id,
            record_type=FAILED_SOURCE_RECORD_TYPE,
            source_id=source_id,
        ),
        include=["metadatas"],
    )
    existing_ids = existing.get("ids") if isinstance(existing, dict) else []
    existing_rows = existing.get("metadatas") if isinstance(existing, dict) else []
    previous_retry_count = 0
    if isinstance(existing_rows, list) and existing_rows:
        previous = existing_rows[0] if isinstance(existing_rows[0], dict) else {}
        previous_retry_count = int((previous or {}).get("retry_count") or 0)
    if isinstance(existing_ids, list) and existing_ids:
        store.vector_db.delete(ids=existing_ids)
    failure_reason = str(metadata.get("failure_reason") or metadata.get("ingest_error_code") or "INGEST_FAILED")
    payload_metadata = {
        **metadata,
        "record_type": FAILED_SOURCE_RECORD_TYPE,
        "ingest_status": "failed",
        "parsed_content_preview": parsed_preview_text,
        "parsed_content_chars": parsed_chars,
        "failure_reason": failure_reason,
        "retry_count": previous_retry_count,
        "last_retry_at": metadata.get("last_retry_at"),
    }
    store.add_documents(
        [
            {
                "content": parsed_preview_text or failure_reason,
                "metadata": payload_metadata,
            }
        ]
    )


def _delete_failed_source_entry(knowledge_base_id: str, source_id: str) -> None:
    normalized_id = str(knowledge_base_id or "").strip()
    normalized_source_id = str(source_id or "").strip()
    if not normalized_id or not normalized_source_id:
        return
    store = _get_knowledge_store()
    store.switch_collection(KNOWLEDGE_REGISTRY_COLLECTION, create_if_missing=True)
    existing = store.vector_db.get(
        where=_build_chroma_where(
            knowledge_base_id=normalized_id,
            record_type=FAILED_SOURCE_RECORD_TYPE,
            source_id=normalized_source_id,
        ),
        include=["metadatas"],
    )
    existing_ids = existing.get("ids") if isinstance(existing, dict) else []
    if isinstance(existing_ids, list) and existing_ids:
        store.vector_db.delete(ids=existing_ids)


def _extract_text_from_upload(filename: str, content_bytes: bytes) -> str:
    """按文件后缀解析上传文档文本，支持 PDF/Markdown/纯文本。"""
    lower_name = str(filename or "").lower()
    if lower_name.endswith(".pdf"):
        reader = PdfReader(BytesIO(content_bytes))
        page_text_list: List[str] = []
        for page in reader.pages:
            page_text_list.append(str(page.extract_text() or ""))
        return "\n".join(page_text_list).strip()
    if lower_name.endswith(".md") or lower_name.endswith(".markdown") or lower_name.endswith(".txt"):
        for encoding in ["utf-8", "utf-8-sig", "gbk"]:
            try:
                return content_bytes.decode(encoding).strip()
            except Exception:
                continue
        raise HTTPException(status_code=400, detail="文本文件编码不支持，请使用 UTF-8/GBK")
    raise HTTPException(status_code=400, detail="仅支持 PDF/Markdown/纯文本文件")


def _detect_source_type_by_filename(filename: str) -> str:
    """根据上传文件名推断来源类型。"""
    lower_name = str(filename or "").lower()
    if lower_name.endswith(".pdf"):
        return "pdf"
    if lower_name.endswith(".md") or lower_name.endswith(".markdown"):
        return "markdown"
    return "txt"


def _resolve_knowledge_base_collection(knowledge_base_id: str) -> Dict[str, str]:
    """校验知识库并返回标准化后的知识库与集合信息。"""
    normalized_id = _normalize_knowledge_base_id(knowledge_base_id)
    collection_name = _to_collection_name(normalized_id)
    store = _get_knowledge_store()
    existing = set(store.list_collections())
    if collection_name not in existing:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return {
        "knowledge_base_id": normalized_id,
        "collection_name": collection_name,
    }


def _infer_source_platform(source_url: str) -> str:
    """根据来源链接域名推断平台标识。"""
    return infer_source_platform(source_url)


def _build_source_metadata(
    knowledge_base_id: str,
    source_url: str,
    source_type: str,
    source_platform: str,
    ingest_mode: str,
    ingest_status: str,
    source_id: Optional[str] = None,
    author: Optional[str] = None,
    ingest_error_code: Optional[str] = None,
    expires_at: Optional[str] = None,
    normalized_url: Optional[str] = None,
    resolved_url: Optional[str] = None,
    source_risk_level: Optional[str] = None,
    extractor_layer: Optional[str] = None,
    quality_score: Optional[int] = None,
) -> Dict[str, Any]:
    """构造来源 metadata，统一字段协议。"""
    return {
        "knowledge_base_id": knowledge_base_id,
        "source_id": source_id or f"src_{uuid4().hex}",
        "source_type": source_type,
        "source_platform": source_platform if source_platform in SOCIAL_SOURCE_PLATFORMS else (source_platform or "unknown"),
        "source_url": source_url,
        "author": author or None,
        "ingest_mode": ingest_mode,
        "ingest_status": ingest_status,
        "ingest_error_code": ingest_error_code or None,
        "ingested_at": datetime.now().isoformat(),
        "expires_at": expires_at or None,
        "normalized_url": normalized_url or None,
        "resolved_url": resolved_url or None,
        "source_risk_level": source_risk_level or None,
        "extractor_layer": extractor_layer or None,
        "quality_score": int(quality_score) if quality_score is not None else None,
    }


def _exists_source_url(collection_name: str, target_url: str) -> bool:
    normalized_target = str(target_url or "").strip()
    if not normalized_target:
        return False
    for item in _load_collection_source_entries(collection_name):
        if str(item.get("resolved_url") or "").strip() == normalized_target:
            return True
        if str(item.get("normalized_url") or "").strip() == normalized_target:
            return True
        if str(item.get("source_url") or "").strip() == normalized_target:
            return True
    return False


def _build_text_preview(text: str, max_chars: int = 180) -> str:
    """构造统一文本预览，便于日志观察解析结果。"""
    normalized_text = str(text or "").replace("\n", " ").strip()
    if not normalized_text:
        return ""
    if len(normalized_text) <= max_chars:
        return normalized_text
    return f"{normalized_text[:max_chars]}..."


def _run_url_auto_parse_preview(
    resolved_url: str,
    source_platform: str,
    source_risk_level: str,
    resolve_error_code: Optional[str] = None,
) -> Dict[str, Any]:
    """执行一次无副作用的自动解析预判，供 preprocess 与 ingest 复用。"""
    crawler = ContentCrawler(max_workers=1, timeout=10)
    parsed_item = crawler.fetch_url_with_fallback(resolved_url, source_platform=source_platform)
    content_text = ""
    extractor_layer: Optional[str] = None
    if isinstance(parsed_item, dict):
        content_text = str(parsed_item.get("content") or "").strip()
        extractor_layer = str(parsed_item.get("extractor_layer") or "").strip() or None
    quality_payload: Dict[str, Any] = {}
    quality_score: Optional[int] = None
    ingest_error_code: Optional[str] = None
    failure_reason: Optional[str] = None
    content_lang: Optional[str] = None
    if content_text:
        quality_payload = validate_content_quality(
            content_text,
            {
                "source_platform": source_platform,
                "source_risk_level": source_risk_level,
                "extractor_layer": extractor_layer,
            },
        )
        quality_score = int(quality_payload.get("quality_score") or 0)
        content_lang = str(quality_payload.get("content_lang") or "").strip() or None
        if bool(quality_payload.get("is_valid")):
            ingest_error_code = None
            failure_reason = None
        else:
            ingest_error_code = str(quality_payload.get("error_code") or resolve_error_code or "AUTO_PARSE_LOW_QUALITY")
            failure_reason = str(quality_payload.get("failure_reason") or ingest_error_code)
    else:
        ingest_error_code = str(resolve_error_code or "AUTO_PARSE_EMPTY")
        failure_reason = str(resolve_error_code or "content_too_short")
    parsed_content_preview = _build_text_preview(content_text, 3000) if content_text else ""
    requires_user_assist = bool(ingest_error_code) or str(source_risk_level or "").lower() == "high"
    return {
        "content_text": content_text,
        "extractor_layer": extractor_layer,
        "quality_payload": quality_payload,
        "quality_score": quality_score,
        "ingest_error_code": ingest_error_code,
        "failure_reason": failure_reason,
        "content_lang": content_lang,
        "parsed_content_preview": parsed_content_preview,
        "parsed_content_chars": len(content_text),
        "requires_user_assist": requires_user_assist,
        "is_valid": not ingest_error_code,
    }


def _load_collection_source_entries(collection_name: str, knowledge_base_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取知识库集合中的来源条目并按 source_id 聚合。"""
    store = _get_knowledge_store()
    try:
        store.switch_collection(collection_name, create_if_missing=False)
        payload = store.vector_db.get(include=["metadatas"])
    except Exception as exc:
        logger.error(
            "knowledge_source_entries_load_failed collection=%s error=%s",
            collection_name,
            str(exc),
        )
        return _load_failed_source_entries(knowledge_base_id) if knowledge_base_id else []
    metadata_rows = payload.get("metadatas") if isinstance(payload, dict) else []
    id_rows = payload.get("ids") if isinstance(payload, dict) else []
    if not isinstance(metadata_rows, list):
        metadata_rows = []
    if not isinstance(id_rows, list):
        id_rows = []
    source_map: Dict[str, Dict[str, Any]] = {}
    legacy_source_rows = 0
    for index, metadata in enumerate(metadata_rows):
        if not isinstance(metadata, dict):
            continue
        source_id = str(metadata.get("source_id") or "").strip()
        if not source_id:
            fallback_source = str(metadata.get("source_url") or metadata.get("source") or "").strip()
            if fallback_source:
                source_id = f"legacy_{hashlib.md5(fallback_source.encode('utf-8')).hexdigest()[:16]}"
            else:
                source_id = f"legacy_{index}"
            legacy_source_rows += 1
        if not source_id:
            continue
        ingest_status = str(metadata.get("ingest_status") or "parsed").strip() or "parsed"
        current_entry = source_map.get(source_id)
        if not current_entry:
            current_entry = {
                "source_id": source_id,
                "source_type": str(metadata.get("source_type") or "txt"),
                "source_platform": str(metadata.get("source_platform") or "unknown"),
                "source_url": str(metadata.get("source_url") or metadata.get("source") or ""),
                "author": metadata.get("author"),
                "ingest_mode": str(metadata.get("ingest_mode") or "auto"),
                "ingest_status": ingest_status,
                "ingest_error_code": metadata.get("ingest_error_code"),
                "ingested_at": metadata.get("ingested_at"),
                "expires_at": metadata.get("expires_at"),
                "chunks_count": 0,
                "parsed_content_preview": str(metadata.get("parsed_content_preview") or ""),
                "parsed_content_chars": int(metadata.get("parsed_content_chars") or 0),
                "normalized_url": metadata.get("normalized_url"),
                "resolved_url": metadata.get("resolved_url"),
                "source_risk_level": metadata.get("source_risk_level"),
                "extractor_layer": metadata.get("extractor_layer"),
                "quality_score": metadata.get("quality_score"),
                "failure_reason": metadata.get("failure_reason"),
                "retry_count": int(metadata.get("retry_count") or 0),
                "last_retry_at": metadata.get("last_retry_at"),
                "chunk_ids": [],
            }
            source_map[source_id] = current_entry
        if not str(current_entry.get("parsed_content_preview") or "") and str(metadata.get("parsed_content_preview") or ""):
            current_entry["parsed_content_preview"] = str(metadata.get("parsed_content_preview") or "")
        if int(current_entry.get("parsed_content_chars") or 0) <= 0 and int(metadata.get("parsed_content_chars") or 0) > 0:
            current_entry["parsed_content_chars"] = int(metadata.get("parsed_content_chars") or 0)
        current_entry["chunks_count"] = int(current_entry.get("chunks_count") or 0) + 1
        if index < len(id_rows):
            current_entry["chunk_ids"].append(str(id_rows[index]))
    if legacy_source_rows > 0:
        logger.info(
            "knowledge_source_entries_legacy_fallback collection=%s legacy_rows=%s total_rows=%s",
            collection_name,
            legacy_source_rows,
            len(metadata_rows),
        )
    if knowledge_base_id:
        for failed_entry in _load_failed_source_entries(knowledge_base_id):
            failed_source_id = str(failed_entry.get("source_id") or "").strip()
            if not failed_source_id or failed_source_id in source_map:
                continue
            source_map[failed_source_id] = failed_entry
    return sorted(list(source_map.values()), key=lambda item: str(item.get("ingested_at") or ""), reverse=True)


def _load_collection_debug_entries(collection_name: str, knowledge_base_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取知识库集合调试快照并按 source_id 聚合分块内容。"""
    store = _get_knowledge_store()
    try:
        store.switch_collection(collection_name, create_if_missing=False)
        payload = store.vector_db.get(include=["metadatas", "documents"])
    except Exception:
        return _load_failed_source_entries(knowledge_base_id) if knowledge_base_id else []
    metadata_rows = payload.get("metadatas") if isinstance(payload, dict) else []
    id_rows = payload.get("ids") if isinstance(payload, dict) else []
    document_rows = payload.get("documents") if isinstance(payload, dict) else []
    if not isinstance(metadata_rows, list):
        metadata_rows = []
    if not isinstance(id_rows, list):
        id_rows = []
    if not isinstance(document_rows, list):
        document_rows = []
    source_map: Dict[str, Dict[str, Any]] = {}
    for index, metadata in enumerate(metadata_rows):
        if not isinstance(metadata, dict):
            continue
        source_id = str(metadata.get("source_id") or "").strip()
        if not source_id:
            source_id = f"unknown_{index}"
        ingest_status = str(metadata.get("ingest_status") or "parsed").strip() or "parsed"
        current_entry = source_map.get(source_id)
        if not current_entry:
            current_entry = {
                "source_id": source_id,
                "source_type": str(metadata.get("source_type") or "txt"),
                "source_platform": str(metadata.get("source_platform") or "unknown"),
                "source_url": str(metadata.get("source_url") or metadata.get("source") or ""),
                "author": metadata.get("author"),
                "ingest_mode": str(metadata.get("ingest_mode") or "auto"),
                "ingest_status": ingest_status,
                "ingest_error_code": metadata.get("ingest_error_code"),
                "ingested_at": metadata.get("ingested_at"),
                "expires_at": metadata.get("expires_at"),
                "chunks_count": 0,
                "parsed_content_preview": str(metadata.get("parsed_content_preview") or ""),
                "parsed_content_chars": int(metadata.get("parsed_content_chars") or 0),
                "normalized_url": metadata.get("normalized_url"),
                "resolved_url": metadata.get("resolved_url"),
                "source_risk_level": metadata.get("source_risk_level"),
                "extractor_layer": metadata.get("extractor_layer"),
                "quality_score": metadata.get("quality_score"),
                "failure_reason": metadata.get("failure_reason"),
                "retry_count": int(metadata.get("retry_count") or 0),
                "last_retry_at": metadata.get("last_retry_at"),
                "chunks": [],
            }
            source_map[source_id] = current_entry
        chunk_id = str(id_rows[index]) if index < len(id_rows) else f"chunk_{index}"
        chunk_content = str(document_rows[index] or "") if index < len(document_rows) else ""
        current_entry["chunks_count"] = int(current_entry.get("chunks_count") or 0) + 1
        current_entry["chunks"].append(
            {
                "chunk_id": chunk_id,
                "content": chunk_content,
                "content_chars": len(chunk_content),
                "chunk_index": int(metadata.get("chunk_index") or (current_entry.get("chunks_count") or 0)),
                "chunk_total": int(metadata.get("chunk_total") or 0),
                "metadata": metadata,
            }
        )
    if knowledge_base_id:
        for failed_entry in _load_failed_source_entries(knowledge_base_id):
            failed_source_id = str(failed_entry.get("source_id") or "").strip()
            if not failed_source_id or failed_source_id in source_map:
                continue
            source_map[failed_source_id] = {**failed_entry, "chunks": []}
    return sorted(list(source_map.values()), key=lambda item: str(item.get("ingested_at") or ""), reverse=True)


def _build_knowledge_base_item(record: Dict[str, str]) -> KnowledgeBaseItem:
    """将 registry 记录转换为知识库列表项并补充统计信息。"""
    collection_name = str(record.get("collection_name") or "")
    source_entries = _load_collection_source_entries(collection_name, str(record.get("knowledge_base_id") or "")) if collection_name else []
    source_types = sorted({str(item.get("source_type") or "") for item in source_entries if str(item.get("source_type") or "").strip()})
    last_updated_at = None
    ingest_times = [str(item.get("ingested_at") or "").strip() for item in source_entries]
    ingest_times = [value for value in ingest_times if value]
    if ingest_times:
        last_updated_at = max(ingest_times)
    document_count = sum([int(item.get("chunks_count") or 0) for item in source_entries])
    return KnowledgeBaseItem(
        knowledge_base_id=str(record.get("knowledge_base_id") or ""),
        name=str(record.get("name") or ""),
        collection_name=collection_name,
        document_count=document_count,
        source_count=len(source_entries),
        source_types=source_types,
        last_updated_at=last_updated_at,
    )


def _build_knowledge_context_payload(
    knowledge_base_id: Optional[str],
    destination: str,
    days: int,
    budget: Optional[str],
    preference: Optional[str],
    knowledge_query: Optional[str],
) -> Tuple[List[str], List[Document]]:
    """构造主流程私有知识上下文，并返回实际命中的文档列表。"""
    if not knowledge_base_id:
        return [], []
    store = _get_knowledge_store()
    collection_name = _to_collection_name(knowledge_base_id)
    all_collections = set(store.list_collections())
    if collection_name not in all_collections:
        raise HTTPException(status_code=404, detail="指定知识库不存在")
    store.switch_collection(collection_name, create_if_missing=False)
    query_text = _build_kb_query(destination, days, budget, preference, knowledge_query)
    related_docs = store.similarity_search(query_text, k=4)
    logger.info(
        "knowledge_context_search kb=%s query=%s hits=%s",
        knowledge_base_id,
        query_text,
        len(related_docs),
    )
    context_texts: List[str] = []
    for index, doc in enumerate(related_docs):
        metadata = doc.metadata or {}
        source = str(metadata.get("source") or metadata.get("source_url") or "私有知识库")
        source_type = str(metadata.get("source_type") or "unknown")
        source_platform = str(metadata.get("source_platform") or "unknown")
        ingest_status = str(metadata.get("ingest_status") or "parsed")
        snippet = str(doc.page_content or "").strip()
        if not snippet:
            continue
        logger.info(
            "knowledge_context_hit kb=%s index=%s source_id=%s source_type=%s ingest_status=%s preview=%s",
            knowledge_base_id,
            index,
            str(metadata.get("source_id") or ""),
            source_type,
            ingest_status,
            _build_text_preview(snippet, 220),
        )
        context_texts.append(
            f"私有知识库参考（来源:{source}，类型:{source_type}，平台:{source_platform}，导入状态:{ingest_status}）：{snippet[:800]}"
        )
    return context_texts, [doc for doc in related_docs if isinstance(doc, Document)]


def _normalize_knowledge_scope(knowledge_scope: Optional[str]) -> str:
    scope = str(knowledge_scope or "private_plus_public").strip().lower()
    if scope not in {"private_only", "private_plus_public"}:
        raise HTTPException(status_code=400, detail="knowledge_scope 仅支持 private_only/private_plus_public")
    return scope


def _build_empty_evidence() -> Dict[str, Any]:
    return {
        "summary": {"items": [], "candidates": [], "used_chars": 0, "budget_chars": 0},
        "body": {"items": [], "candidates": [], "used_chars": 0, "budget_chars": 0},
        "budget": {"summary_max_chars": 0, "body_max_chars": 0},
    }


def _is_social_private_source(metadata: Dict[str, Any]) -> bool:
    source_type = str(metadata.get("source_type") or "").strip().lower()
    source_platform = str(metadata.get("source_platform") or "").strip().lower()
    return source_type in {"url", "manual", "ocr"} or source_platform in SOCIAL_SOURCE_PLATFORMS


def _build_source_evidence_from_docs(docs: List[Document]) -> List[Dict[str, Any]]:
    """从实际命中的私有文档中提取来源证据，避免直接回传整个来源列表。"""
    source_map: Dict[str, Dict[str, Any]] = {}
    for doc in docs:
        if not isinstance(doc, Document):
            continue
        metadata = doc.metadata or {}
        source_id = str(metadata.get("source_id") or "").strip()
        if not source_id:
            continue
        current = source_map.get(source_id)
        if not current:
            current = {
                "source_id": source_id,
                "source_type": str(metadata.get("source_type") or "unknown"),
                "source_platform": str(metadata.get("source_platform") or "unknown"),
                "source_url": str(metadata.get("source_url") or metadata.get("source") or ""),
                "ingest_status": str(metadata.get("ingest_status") or "parsed"),
                "hit_count": 0,
                "hit_chunk_ids": [],
            }
            source_map[source_id] = current
        current["hit_count"] = int(current.get("hit_count") or 0) + 1
        chunk_id = str(metadata.get("chunk_id") or "").strip()
        if chunk_id and chunk_id not in current["hit_chunk_ids"]:
            current["hit_chunk_ids"].append(chunk_id)
    return sorted(list(source_map.values()), key=lambda item: (-int(item.get("hit_count") or 0), str(item.get("source_id") or "")))


def _search_private_knowledge_docs(knowledge_base_id: str, query: str, k: int = 14) -> List[Document]:
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    store = _get_knowledge_store()
    store.switch_collection(kb_info["collection_name"], create_if_missing=False)
    related_docs = store.similarity_search(query, k=k)
    social_docs: List[Document] = []
    other_docs: List[Document] = []
    for doc in related_docs:
        if not isinstance(doc, Document):
            continue
        metadata = doc.metadata or {}
        if _is_social_private_source(metadata):
            social_docs.append(doc)
        else:
            other_docs.append(doc)
    return (social_docs + other_docs)[:10]


def _build_private_knowledge_evidence(knowledge_base_id: str, query: str) -> Dict[str, Any]:
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    ordered_docs = _search_private_knowledge_docs(knowledge_base_id, query, k=14)
    summary_items: List[Dict[str, Any]] = []
    body_items: List[Dict[str, Any]] = []
    for index, doc in enumerate(ordered_docs):
        metadata = doc.metadata or {}
        text = str(doc.page_content or "").strip()
        if not text:
            continue
        source_url = str(metadata.get("source_url") or metadata.get("source") or "").strip()
        source = source_url or f"private://{kb_info['knowledge_base_id']}/{index + 1}"
        title = str(metadata.get("title") or metadata.get("source_type") or f"私有知识片段 {index + 1}")
        summary_text = text[:220]
        body_text = text[:1200]
        confidence = 1.0
        summary_items.append(
            {
                "source": source,
                "title": title,
                "text": summary_text,
                "score": confidence,
                "confidence": confidence,
                "timestamp": metadata.get("ingested_at"),
                "source_type": str(metadata.get("source_type") or "private"),
                "source_platform": str(metadata.get("source_platform") or "unknown"),
                "ingest_status": str(metadata.get("ingest_status") or "parsed"),
            }
        )
        body_items.append(
            {
                "source": source,
                "title": title,
                "text": body_text,
                "score": confidence,
                "confidence": confidence,
                "timestamp": metadata.get("ingested_at"),
                "source_type": str(metadata.get("source_type") or "private"),
                "source_platform": str(metadata.get("source_platform") or "unknown"),
                "ingest_status": str(metadata.get("ingest_status") or "parsed"),
            }
        )
    return {
        "summary": {
            "items": summary_items[:6],
            "candidates": summary_items,
            "used_chars": sum([len(str(item.get("text") or "")) for item in summary_items[:6]]),
            "budget_chars": sum([len(str(item.get("text") or "")) for item in summary_items]),
        },
        "body": {
            "items": body_items[:4],
            "candidates": body_items,
            "used_chars": sum([len(str(item.get("text") or "")) for item in body_items[:4]]),
            "budget_chars": sum([len(str(item.get("text") or "")) for item in body_items]),
        },
        "budget": {
            "summary_max_chars": sum([len(str(item.get("text") or "")) for item in summary_items]),
            "body_max_chars": sum([len(str(item.get("text") or "")) for item in body_items]),
        },
    }


def _merge_evidence_sections(private_section: Any, public_section: Any) -> Dict[str, Any]:
    private_payload = private_section if isinstance(private_section, dict) else {}
    public_payload = public_section if isinstance(public_section, dict) else {}
    private_items = list(private_payload.get("items") or [])
    public_items = list(public_payload.get("items") or [])
    private_candidates = list(private_payload.get("candidates") or [])
    public_candidates = list(public_payload.get("candidates") or [])
    merged_items = private_items + public_items
    merged_candidates = private_candidates + public_candidates
    return {
        "items": merged_items,
        "candidates": merged_candidates,
        "used_chars": sum([len(str(item.get("text") or "")) for item in merged_items]),
        "budget_chars": sum([len(str(item.get("text") or "")) for item in merged_candidates]),
    }


def _merge_knowledge_evidence(private_evidence: Dict[str, Any], public_evidence: Dict[str, Any]) -> Dict[str, Any]:
    private_payload = private_evidence if isinstance(private_evidence, dict) else {}
    public_payload = public_evidence if isinstance(public_evidence, dict) else {}
    if not private_payload and not public_payload:
        return _build_empty_evidence()
    merged_summary = _merge_evidence_sections(private_payload.get("summary"), public_payload.get("summary"))
    merged_body = _merge_evidence_sections(private_payload.get("body"), public_payload.get("body"))
    return {
        "summary": merged_summary,
        "body": merged_body,
        "budget": {
            "summary_max_chars": int(merged_summary.get("budget_chars") or 0),
            "body_max_chars": int(merged_body.get("budget_chars") or 0),
        },
    }
