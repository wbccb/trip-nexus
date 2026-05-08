import json
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from src.auth.middleware import (
    AuthenticatedUser,
    get_current_user,
    get_optional_user,
)
from src.rag.network.url_preprocessor import preprocess_url
from src.api.schemas.knowledge import (
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeAnswerRequest,
    KnowledgeAnswerResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseCreateResponse,
    KnowledgeBaseDeleteResponse,
    KnowledgeBaseItem,
    KnowledgeBaseListResponse,
    KnowledgeUploadResponse,
    KnowledgeIngestUrlRequest,
    KnowledgePreprocessUrlRequest,
    KnowledgePreprocessUrlResponse,
    KnowledgeIngestUrlResponse,
    KnowledgeSourceItem,
    KnowledgeSourceStats,
    KnowledgeSourceListResponse,
    KnowledgeSourceDeleteResponse,
    KnowledgeSourceUpdateRequest,
    KnowledgeSourceUpdateResponse,
    KnowledgeDebugChunkItem,
    KnowledgeDebugSourceItem,
    KnowledgeDebugBaseItem,
    KnowledgeDebugSnapshotResponse,
)
from src.api.dependencies import (
    _get_llm_manager,
    _get_rag_pipeline,
    _get_knowledge_store,
    _normalize_knowledge_base_id,
    _apply_authenticated_request_guard,
    _apply_authenticated_audit_context,
    _reset_observability_context,
    _record_audit_log,
    _assert_within_quota,
    _assert_session_owned,
)
from src.api.logic.knowledge import (
    _to_collection_name,
    _load_knowledge_base_registry,
    _upsert_knowledge_base_registry,
    _delete_knowledge_base_registry,
    _build_text_preview,
    _extract_text_from_upload,
    _detect_source_type_by_filename,
    _resolve_knowledge_base_collection,
    _infer_source_platform,
    _build_source_metadata,
    _exists_source_url,
    _run_url_auto_parse_preview,
    _load_collection_source_entries,
    _load_collection_debug_entries,
    _build_knowledge_base_item,
    _normalize_knowledge_scope,
    _build_empty_evidence,
    _build_private_knowledge_evidence,
    _merge_knowledge_evidence,
    _delete_failed_source_entry,
    _upsert_failed_source_entry,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

@router.post("/search", response_model=KnowledgeSearchResponse)
def knowledge_search(
    payload: KnowledgeSearchRequest,
    request: Request,
    current_user: Optional[AuthenticatedUser] = Depends(get_optional_user),
) -> KnowledgeSearchResponse:
    """搜索知识库内容，支持私有知识库与公网检索融合"""
    guard_token = None
    if current_user:
        guard_token = _apply_authenticated_request_guard(
            request=request,
            current_user=current_user,
            request_path="/api/knowledge/search",
            bucket="knowledge_search",
        )
    try:
        rag_pipeline = _get_rag_pipeline()
        knowledge_scope = _normalize_knowledge_scope(payload.knowledge_scope)
        allow_public_fusion = knowledge_scope == "private_plus_public"
        public_evidence = _build_empty_evidence()
        if allow_public_fusion:
            public_evidence = rag_pipeline.get_evidence_by_query(payload.query)
        private_evidence = _build_empty_evidence()
        if payload.knowledge_base_id:
            private_evidence = _build_private_knowledge_evidence(payload.knowledge_base_id, payload.query)
        merged_evidence = _merge_knowledge_evidence(private_evidence, public_evidence)
        answer = None
        if payload.generate_answer:
            answer = rag_pipeline.get_answer_by_evidence(payload.query, merged_evidence)
        source_evidence = []
        if payload.knowledge_base_id:
            private_docs = []
            private_body_candidates = private_evidence.get("body", {}).get("candidates") or []
            for item in private_body_candidates:
                if isinstance(item, dict):
                    private_docs.append(item)
            source_evidence = private_docs[:10]
        if current_user:
            _record_audit_log(
                action="knowledge_search",
                status="success",
                user_id=current_user.user_id,
                user_email=current_user.email,
                detail={
                    "query": payload.query,
                    "kb_id": payload.knowledge_base_id,
                    "scope": knowledge_scope,
                    "has_answer": bool(answer),
                },
            )
        return KnowledgeSearchResponse(
            query=payload.query,
            evidence=merged_evidence,
            answer=answer,
            source_evidence=source_evidence,
            knowledge_debug={
                "knowledge_scope": knowledge_scope,
                "allow_public_fusion": allow_public_fusion,
                "has_private_evidence": bool(payload.knowledge_base_id),
            },
        )
    finally:
        if guard_token:
            _reset_observability_context(guard_token)


@router.post("/answer", response_model=KnowledgeAnswerResponse)
@router.post("/answer_from_evidence", response_model=KnowledgeAnswerResponse)
def knowledge_answer(
    payload: KnowledgeAnswerRequest,
    request: Request,
    current_user: Optional[AuthenticatedUser] = Depends(get_optional_user),
) -> KnowledgeAnswerResponse:
    """根据给定的证据生成回答"""
    guard_token = None
    if current_user:
        guard_token = _apply_authenticated_request_guard(
            request=request,
            current_user=current_user,
            request_path="/api/knowledge/answer",
            bucket="knowledge_answer",
        )
    try:
        rag_pipeline = _get_rag_pipeline()
        answer = rag_pipeline.get_answer_by_evidence(payload.query, payload.evidence)
        if current_user:
            _record_audit_log(
                action="knowledge_answer",
                status="success",
                user_id=current_user.user_id,
                user_email=current_user.email,
                detail={"query": payload.query},
            )
        return KnowledgeAnswerResponse(query=payload.query, evidence=payload.evidence, answer=answer)
    finally:
        if guard_token:
            _reset_observability_context(guard_token)


@router.post("/base/create", response_model=KnowledgeBaseCreateResponse)
@router.post("/bases", response_model=KnowledgeBaseCreateResponse)
def create_knowledge_base(
    payload: KnowledgeBaseCreateRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeBaseCreateResponse:
    """创建新的私有知识库"""
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/knowledge/base/create",
    )
    try:
        knowledge_base_id = f"kb_{datetime.now().strftime('%Y%m%d%H%M%S')}_{current_user.user_id}"
        collection_name = _to_collection_name(knowledge_base_id)
        store = _get_knowledge_store()
        store.switch_collection(collection_name, create_if_missing=True)
        _upsert_knowledge_base_registry(knowledge_base_id, payload.name, collection_name)
        _record_audit_log(
            action="knowledge_base_create",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={"kb_id": knowledge_base_id, "name": payload.name},
        )
        return KnowledgeBaseCreateResponse(
            knowledge_base_id=knowledge_base_id,
            name=payload.name,
            collection_name=collection_name,
        )
    finally:
        _reset_observability_context(guard_token)


@router.get("/base/list", response_model=KnowledgeBaseListResponse)
@router.get("/bases", response_model=KnowledgeBaseListResponse)
def list_knowledge_bases(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeBaseListResponse:
    """获取用户的知识库列表"""
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/knowledge/base/list",
    )
    try:
        try:
            registry_rows = _load_knowledge_base_registry()
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"知识库功能暂不可用：{exc}",
            ) from exc
        user_id_suffix = f"_{current_user.user_id}"
        user_rows = [row for row in registry_rows if str(row.get("knowledge_base_id") or "").endswith(user_id_suffix)]
        items = [_build_knowledge_base_item(row) for row in user_rows]
        return KnowledgeBaseListResponse(items=items)
    finally:
        _reset_observability_context(guard_token)


@router.delete("/base/{knowledge_base_id}", response_model=KnowledgeBaseDeleteResponse)
@router.delete("/bases/{knowledge_base_id}", response_model=KnowledgeBaseDeleteResponse)
def delete_knowledge_base(
    knowledge_base_id: str,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeBaseDeleteResponse:
    """删除指定的知识库及其所有数据"""
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    if not str(kb_info["knowledge_base_id"]).endswith(f"_{current_user.user_id}"):
        raise HTTPException(status_code=403, detail="无权操作此知识库")
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path=f"/api/knowledge/base/{knowledge_base_id}",
    )
    try:
        store = _get_knowledge_store()
        # 删除知识库时统一走 VectorStore 封装，避免直接依赖底层向量实现细节。
        store.delete_collection(kb_info["collection_name"])
        _delete_knowledge_base_registry(knowledge_base_id)
        _record_audit_log(
            action="knowledge_base_delete",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={"kb_id": knowledge_base_id},
        )
        return KnowledgeBaseDeleteResponse(knowledge_base_id=knowledge_base_id, success=True)
    finally:
        _reset_observability_context(guard_token)


@router.post("/base/{knowledge_base_id}/upload", response_model=KnowledgeUploadResponse)
@router.post("/bases/{knowledge_base_id}/upload", response_model=KnowledgeUploadResponse)
async def upload_knowledge_file(
    knowledge_base_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeUploadResponse:
    """上传本地文档到指定的知识库"""
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    if not str(kb_info["knowledge_base_id"]).endswith(f"_{current_user.user_id}"):
        raise HTTPException(status_code=403, detail="无权操作此知识库")
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path=f"/api/knowledge/base/{knowledge_base_id}/upload",
    )
    try:
        content_bytes = await file.read()
        if not content_bytes:
            raise HTTPException(status_code=400, detail="文件内容为空")
        text_content = _extract_text_from_upload(file.filename, content_bytes)
        if not text_content:
            raise HTTPException(status_code=400, detail="文档解析后无正文内容")
        source_type = _detect_source_type_by_filename(file.filename)
        source_id = f"src_{uuid4().hex}"
        metadata = _build_source_metadata(
            knowledge_base_id=knowledge_base_id,
            source_url=file.filename,
            source_type=source_type,
            source_platform="local_upload",
            ingest_mode="manual",
            ingest_status="parsed",
            source_id=source_id,
            author=str(current_user.email),
        )
        store = _get_knowledge_store()
        store.switch_collection(kb_info["collection_name"], create_if_missing=False)
        # add_documents 返回的是“新增 chunk 数量”，这里直接按整数使用，避免再做 len()。
        chunks_count = store.add_documents([{"content": text_content, "metadata": metadata}])
        _record_audit_log(
            action="knowledge_upload",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={
                "kb_id": knowledge_base_id,
                "filename": file.filename,
                "source_id": source_id,
                "chunks": chunks_count,
            },
        )
        return KnowledgeUploadResponse(
            knowledge_base_id=knowledge_base_id,
            filename=file.filename,
            chunks=chunks_count,
            metadata=metadata,
            parsed_content_preview=_build_text_preview(text_content, 180),
            parsed_content_chars=len(text_content),
        )
    finally:
        _reset_observability_context(guard_token)


@router.post("/preprocess/url", response_model=KnowledgePreprocessUrlResponse)
def preprocess_knowledge_url_public(
    payload: KnowledgePreprocessUrlRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgePreprocessUrlResponse:
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/knowledge/preprocess/url",
    )
    try:
        url = str(payload.url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="url 不能为空")
        pre_payload = preprocess_url(url)
        resolved_url = str(pre_payload.get("resolved_url") or url)
        source_platform = _infer_source_platform(resolved_url)
        source_risk_level = str(pre_payload.get("risk_level") or "low")
        resolve_error_code = str(pre_payload.get("error_code") or "") or None
        parse_preview = _run_url_auto_parse_preview(resolved_url, source_platform, source_risk_level, resolve_error_code)
        return KnowledgePreprocessUrlResponse(
            success=True,
            normalized_url=str(pre_payload.get("normalized_url") or ""),
            resolved_url=resolved_url,
            source_platform=source_platform,
            source_risk_level=source_risk_level,
            resolve_error_code=resolve_error_code,
            extractor_layer=parse_preview.get("extractor_layer"),
            quality_score=parse_preview.get("quality_score"),
            ingest_error_code=parse_preview.get("ingest_error_code"),
            failure_reason=parse_preview.get("failure_reason"),
            content_lang=parse_preview.get("content_lang"),
            requires_user_assist=bool(parse_preview.get("requires_user_assist")),
            parsed_content_preview=str(parse_preview.get("parsed_content_preview") or ""),
            parsed_content_chars=int(parse_preview.get("parsed_content_chars") or 0),
        )
    finally:
        _reset_observability_context(guard_token)


@router.post("/base/{knowledge_base_id}/preprocess_url", response_model=KnowledgePreprocessUrlResponse)
def preprocess_knowledge_url(
    knowledge_base_id: str,
    payload: KnowledgePreprocessUrlRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgePreprocessUrlResponse:
    """预处理 URL：检查重复、解跳、风险评估与解析预判"""
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    if not str(kb_info["knowledge_base_id"]).endswith(f"_{current_user.user_id}"):
        raise HTTPException(status_code=403, detail="无权操作此知识库")
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path=f"/api/knowledge/base/{knowledge_base_id}/preprocess_url",
    )
    try:
        url = str(payload.url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="url 不能为空")
        pre_payload = preprocess_url(url)
        resolved_url = str(pre_payload.get("resolved_url") or url)
        if _exists_source_url(kb_info["collection_name"], resolved_url):
            raise HTTPException(status_code=409, detail="该链接已存在于知识库中")
        source_platform = _infer_source_platform(resolved_url)
        source_risk_level = str(pre_payload.get("risk_level") or "low")
        resolve_error_code = str(pre_payload.get("error_code") or "") or None
        parse_preview = _run_url_auto_parse_preview(resolved_url, source_platform, source_risk_level, resolve_error_code)
        return KnowledgePreprocessUrlResponse(
            success=True,
            normalized_url=str(pre_payload.get("normalized_url") or ""),
            resolved_url=resolved_url,
            source_platform=source_platform,
            source_risk_level=source_risk_level,
            resolve_error_code=resolve_error_code,
            extractor_layer=parse_preview.get("extractor_layer"),
            quality_score=parse_preview.get("quality_score"),
            ingest_error_code=parse_preview.get("ingest_error_code"),
            failure_reason=parse_preview.get("failure_reason"),
            content_lang=parse_preview.get("content_lang"),
            requires_user_assist=bool(parse_preview.get("requires_user_assist")),
            parsed_content_preview=str(parse_preview.get("parsed_content_preview") or ""),
            parsed_content_chars=int(parse_preview.get("parsed_content_chars") or 0),
        )
    finally:
        _reset_observability_context(guard_token)


@router.post("/base/{knowledge_base_id}/ingest_url", response_model=KnowledgeIngestUrlResponse)
@router.post("/bases/{knowledge_base_id}/ingest/url", response_model=KnowledgeIngestUrlResponse)
def ingest_knowledge_url(
    knowledge_base_id: str,
    payload: KnowledgeIngestUrlRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeIngestUrlResponse:
    """正式导入 URL 链接内容到知识库"""
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    if not str(kb_info["knowledge_base_id"]).endswith(f"_{current_user.user_id}"):
        raise HTTPException(status_code=403, detail="无权操作此知识库")
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path=f"/api/knowledge/base/{knowledge_base_id}/ingest_url",
    )
    try:
        url = str(payload.url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="url 不能为空")
        pre_payload = preprocess_url(url)
        resolved_url = str(pre_payload.get("resolved_url") or url)
        if _exists_source_url(kb_info["collection_name"], resolved_url):
            raise HTTPException(status_code=409, detail="该链接已存在于知识库中")
        source_platform = _infer_source_platform(resolved_url)
        source_risk_level = str(pre_payload.get("risk_level") or "low")
        resolve_error_code = str(pre_payload.get("error_code") or "") or None
        source_id = f"src_{uuid4().hex}"
        ingest_mode = str(payload.mode or "auto").strip().lower()
        if ingest_mode == "manual" and payload.manual_text:
            text_content = str(payload.manual_text).strip()
            ingest_status = "parsed"
            ingest_error_code = None
        elif ingest_mode == "manual" and payload.ocr_text:
            text_content = str(payload.ocr_text).strip()
            ingest_status = "fallback"
            ingest_error_code = None
        else:
            parse_preview = _run_url_auto_parse_preview(resolved_url, source_platform, source_risk_level, resolve_error_code)
            text_content = str(parse_preview.get("content_text") or "").strip()
            ingest_status = "parsed" if bool(parse_preview.get("is_valid")) else "failed"
            ingest_error_code = parse_preview.get("ingest_error_code")
        metadata = _build_source_metadata(
            knowledge_base_id=knowledge_base_id,
            source_url=url,
            source_type="url",
            source_platform=source_platform,
            ingest_mode=ingest_mode,
            ingest_status=ingest_status,
            source_id=source_id,
            author=str(current_user.email),
            ingest_error_code=ingest_error_code,
            normalized_url=str(pre_payload.get("normalized_url") or ""),
            resolved_url=resolved_url,
            source_risk_level=source_risk_level,
            extractor_layer=parse_preview.get("extractor_layer") if ingest_mode == "auto" else "manual_assist",
            quality_score=parse_preview.get("quality_score") if ingest_mode == "auto" else 100,
        )
        if ingest_status == "failed":
            _upsert_failed_source_entry(metadata, _build_text_preview(text_content, 3000), len(text_content))
            _record_audit_log(
                action="knowledge_ingest_url",
                status="failed",
                user_id=current_user.user_id,
                user_email=current_user.email,
                detail={"kb_id": knowledge_base_id, "url": url, "error": ingest_error_code},
            )
            return KnowledgeIngestUrlResponse(
                success=False,
                ingest_status=ingest_status,
                chunks_count=0,
                metadata=metadata,
                parsed_content_preview=_build_text_preview(text_content, 180),
                parsed_content_chars=len(text_content),
            )
        _delete_failed_source_entry(knowledge_base_id, source_id)
        store = _get_knowledge_store()
        store.switch_collection(kb_info["collection_name"], create_if_missing=False)
        # add_documents 返回的是“新增 chunk 数量”，这里直接按整数使用，避免再做 len()。
        chunks_count = store.add_documents([{"content": text_content, "metadata": metadata}])
        _record_audit_log(
            action="knowledge_ingest_url",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={"kb_id": knowledge_base_id, "url": url, "source_id": source_id, "chunks": chunks_count},
        )
        return KnowledgeIngestUrlResponse(
            success=True,
            ingest_status=ingest_status,
            chunks_count=chunks_count,
            metadata=metadata,
            parsed_content_preview=_build_text_preview(text_content, 180),
            parsed_content_chars=len(text_content),
        )
    finally:
        _reset_observability_context(guard_token)


@router.get("/base/{knowledge_base_id}/sources", response_model=KnowledgeSourceListResponse)
@router.get("/bases/{knowledge_base_id}/sources", response_model=KnowledgeSourceListResponse)
def list_knowledge_sources(
    knowledge_base_id: str,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeSourceListResponse:
    """获取知识库中的所有来源列表"""
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    if not str(kb_info["knowledge_base_id"]).endswith(f"_{current_user.user_id}"):
        raise HTTPException(status_code=403, detail="无权操作此知识库")
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path=f"/api/knowledge/base/{knowledge_base_id}/sources",
    )
    try:
        source_entries = _load_collection_source_entries(kb_info["collection_name"], knowledge_base_id)
        items = [KnowledgeSourceItem(**item) for item in source_entries]
        stats = KnowledgeSourceStats(
            total=len(items),
            parsed=len([i for i in items if i.ingest_status == "parsed"]),
            fallback=len([i for i in items if i.ingest_status == "fallback"]),
            failed=len([i for i in items if i.ingest_status == "failed"]),
        )
        return KnowledgeSourceListResponse(knowledge_base_id=knowledge_base_id, items=items, stats=stats)
    finally:
        _reset_observability_context(guard_token)


@router.delete("/base/{knowledge_base_id}/sources/{source_id}", response_model=KnowledgeSourceDeleteResponse)
@router.delete("/bases/{knowledge_base_id}/sources/{source_id}", response_model=KnowledgeSourceDeleteResponse)
def delete_knowledge_source(
    knowledge_base_id: str,
    source_id: str,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeSourceDeleteResponse:
    """删除指定的来源及其关联的所有知识分块"""
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    if not str(kb_info["knowledge_base_id"]).endswith(f"_{current_user.user_id}"):
        raise HTTPException(status_code=403, detail="无权操作此知识库")
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path=f"/api/knowledge/base/{knowledge_base_id}/sources/{source_id}",
    )
    try:
        source_entries = _load_collection_source_entries(kb_info["collection_name"], knowledge_base_id)
        target = next((s for s in source_entries if s["source_id"] == source_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="来源不存在")
        deleted_count = 0
        if target.get("ingest_status") == "failed":
            _delete_failed_source_entry(knowledge_base_id, source_id)
        else:
            chunk_ids = target.get("chunk_ids") or []
            if chunk_ids:
                store = _get_knowledge_store()
                store.switch_collection(kb_info["collection_name"], create_if_missing=False)
                store.vector_db.delete(ids=chunk_ids)
                deleted_count = len(chunk_ids)
        _record_audit_log(
            action="knowledge_source_delete",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={"kb_id": knowledge_base_id, "source_id": source_id, "deleted_chunks": deleted_count},
        )
        return KnowledgeSourceDeleteResponse(
            knowledge_base_id=knowledge_base_id,
            source_id=source_id,
            success=True,
            deleted_chunks=deleted_count,
        )
    finally:
        _reset_observability_context(guard_token)


@router.put("/base/{knowledge_base_id}/sources/{source_id}", response_model=KnowledgeSourceUpdateResponse)
@router.put("/bases/{knowledge_base_id}/sources/{source_id}", response_model=KnowledgeSourceUpdateResponse)
@router.patch("/bases/{knowledge_base_id}/sources/{source_id}", response_model=KnowledgeSourceUpdateResponse)
def update_knowledge_source(
    knowledge_base_id: str,
    source_id: str,
    payload: KnowledgeSourceUpdateRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeSourceUpdateResponse:
    """更新来源正文内容（支持手动纠错或 OCR 补全）并重新入库"""
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    if not str(kb_info["knowledge_base_id"]).endswith(f"_{current_user.user_id}"):
        raise HTTPException(status_code=403, detail="无权操作此知识库")
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path=f"/api/knowledge/base/{knowledge_base_id}/sources/{source_id}",
    )
    try:
        source_entries = _load_collection_source_entries(kb_info["collection_name"], knowledge_base_id)
        target = next((s for s in source_entries if s["source_id"] == source_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="来源不存在")
        next_content = str(payload.content or "").strip()
        if not next_content:
            raise HTTPException(status_code=400, detail="content 不能为空")
        next_url = str(payload.source_url or target.get("source_url") or "").strip()
        next_metadata = _build_source_metadata(
            knowledge_base_id=knowledge_base_id,
            source_url=next_url,
            source_type=str(target.get("source_type") or "url"),
            source_platform=str(target.get("source_platform") or "unknown"),
            ingest_mode="manual",
            ingest_status="parsed",
            source_id=source_id,
            author=str(current_user.email),
            normalized_url=str(target.get("normalized_url") or ""),
            resolved_url=str(target.get("resolved_url") or ""),
            source_risk_level=str(target.get("source_risk_level") or "low"),
            extractor_layer="manual_update",
            quality_score=100,
        )
        store = _get_knowledge_store()
        store.switch_collection(kb_info["collection_name"], create_if_missing=False)
        old_chunk_ids = target.get("chunk_ids") or []
        if old_chunk_ids:
            store.vector_db.delete(ids=old_chunk_ids)
        if target.get("ingest_status") == "failed":
            _delete_failed_source_entry(knowledge_base_id, source_id)
        # add_documents 返回的是“新增 chunk 数量”，这里直接按整数使用，避免再做 len()。
        new_chunks_count = store.add_documents([{"content": next_content, "metadata": next_metadata}])
        _record_audit_log(
            action="knowledge_source_update",
            status="success",
            user_id=current_user.user_id,
            user_email=current_user.email,
            detail={"kb_id": knowledge_base_id, "source_id": source_id, "chunks": new_chunks_count},
        )
        return KnowledgeSourceUpdateResponse(
            knowledge_base_id=knowledge_base_id,
            source_id=source_id,
            success=True,
            chunks_count=new_chunks_count,
            metadata=next_metadata,
            parsed_content_preview=_build_text_preview(next_content, 180),
            parsed_content_chars=len(next_content),
        )
    finally:
        _reset_observability_context(guard_token)


@router.get("/base/debug_snapshot", response_model=KnowledgeDebugSnapshotResponse)
@router.get("/debug/snapshot", response_model=KnowledgeDebugSnapshotResponse)
def knowledge_debug_snapshot(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> KnowledgeDebugSnapshotResponse:
    """获取所有知识库的调试快照，包含所有分块正文（仅供调试排查）"""
    guard_token = _apply_authenticated_audit_context(
        request=request,
        current_user=current_user,
        request_path="/api/knowledge/base/debug_snapshot",
    )
    try:
        registry_rows = _load_knowledge_base_registry()
        user_id_suffix = f"_{current_user.user_id}"
        user_rows = [row for row in registry_rows if str(row.get("knowledge_base_id") or "").endswith(user_id_suffix)]
        items = []
        for row in user_rows:
            kb_id = str(row.get("knowledge_base_id") or "")
            col_name = str(row.get("collection_name") or "")
            debug_entries = _load_collection_debug_entries(col_name, kb_id)
            source_items = []
            doc_count = 0
            for entry in debug_entries:
                chunk_items = [KnowledgeDebugChunkItem(**c) for c in (entry.get("chunks") or [])]
                doc_count += len(chunk_items)
                source_items.append(KnowledgeDebugSourceItem(**{**entry, "chunks": chunk_items}))
            items.append(
                KnowledgeDebugBaseItem(
                    knowledge_base_id=kb_id,
                    name=str(row.get("name") or ""),
                    collection_name=col_name,
                    document_count=doc_count,
                    source_count=len(source_items),
                    last_updated_at=max([str(s.ingested_at) for s in source_items if s.ingested_at]) if source_items else None,
                    sources=source_items,
                )
            )
        return KnowledgeDebugSnapshotResponse(generated_at=datetime.now().isoformat(), items=items)
    finally:
        _reset_observability_context(guard_token)
