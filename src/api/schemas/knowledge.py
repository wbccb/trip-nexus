from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field

class KnowledgeSearchRequest(BaseModel):
    """知识库检索请求体"""
    query: str = Field(..., description="检索问题或主题")
    generate_answer: bool = Field(False, description="是否直接生成回答")
    knowledge_base_id: Optional[str] = Field(None, description="知识库ID（可选）")
    knowledge_scope: Optional[str] = Field("private_plus_public", description="知识范围 private_only/private_plus_public")


class KnowledgeSearchResponse(BaseModel):
    """知识库检索响应体"""
    query: str = Field(..., description="检索问题")
    evidence: Dict[str, Any] = Field(..., description="证据结构化结果")
    answer: Optional[str] = Field(None, description="可选回答")
    source_evidence: List[Dict[str, Any]] = Field(default_factory=list, description="私有来源证据列表")
    knowledge_debug: Dict[str, Any] = Field(default_factory=dict, description="检索调试信息")


class KnowledgeAnswerRequest(BaseModel):
    query: str = Field(..., description="检索问题")
    evidence: Dict[str, Any] = Field(..., description="证据结构化结果")


class KnowledgeAnswerResponse(BaseModel):
    query: str = Field(..., description="检索问题")
    evidence: Dict[str, Any] = Field(..., description="证据结构化结果")
    answer: str = Field(..., description="生成回答")


class KnowledgeBaseCreateRequest(BaseModel):
    """创建知识库请求体"""
    name: str = Field(..., description="知识库名称")


class KnowledgeBaseCreateResponse(BaseModel):
    """创建知识库响应体"""
    knowledge_base_id: str = Field(..., description="知识库ID")
    name: str = Field(..., description="知识库名称")
    collection_name: str = Field(..., description="向量集合名")


class KnowledgeBaseDeleteResponse(BaseModel):
    """删除知识库响应体"""
    knowledge_base_id: str = Field(..., description="知识库ID")
    success: bool = Field(..., description="是否删除成功")


class KnowledgeBaseItem(BaseModel):
    """知识库列表项"""
    knowledge_base_id: str = Field(..., description="知识库ID")
    name: str = Field(..., description="知识库名称")
    collection_name: str = Field(..., description="向量集合名")
    document_count: int = Field(0, description="知识条目分块数量")
    source_count: int = Field(0, description="知识来源数量")
    source_types: List[str] = Field(default_factory=list, description="来源类型列表")
    last_updated_at: Optional[str] = Field(None, description="最后更新时间")


class KnowledgeBaseListResponse(BaseModel):
    """知识库列表响应体"""
    items: List[KnowledgeBaseItem] = Field(default_factory=list, description="知识库列表")


class KnowledgeUploadResponse(BaseModel):
    """知识库文档上传响应体"""
    knowledge_base_id: str = Field(..., description="知识库ID")
    filename: str = Field(..., description="原始文件名")
    chunks: int = Field(..., description="入库分块数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="来源元数据")
    parsed_content_preview: str = Field("", description="解析正文预览")
    parsed_content_chars: int = Field(0, description="解析正文字符数")


class KnowledgeIngestUrlRequest(BaseModel):
    url: str = Field(..., description="待导入链接")
    mode: str = Field("auto", description="导入模式 auto/manual")
    manual_text: Optional[str] = Field(None, description="手动粘贴文本")
    ocr_text: Optional[str] = Field(None, description="OCR 提取文本")


class KnowledgePreprocessUrlRequest(BaseModel):
    url: str = Field(..., description="待预处理链接")


class KnowledgePreprocessUrlResponse(BaseModel):
    success: bool = Field(..., description="是否预处理成功")
    normalized_url: str = Field("", description="规范化链接")
    resolved_url: str = Field("", description="解跳后的链接")
    source_platform: str = Field("unknown", description="来源平台")
    source_risk_level: str = Field("low", description="来源风险等级 low/medium/high")
    resolve_error_code: Optional[str] = Field(None, description="短链解跳异常码")
    extractor_layer: Optional[str] = Field(None, description="预处理阶段命中的提取层级")
    quality_score: Optional[int] = Field(None, description="预处理阶段质量分")
    ingest_error_code: Optional[str] = Field(None, description="预处理阶段预测失败码")
    failure_reason: Optional[str] = Field(None, description="预处理阶段失败原因")
    content_lang: Optional[str] = Field(None, description="预处理阶段识别的内容语言")
    requires_user_assist: bool = Field(False, description="是否建议用户直接切换手动/OCR 辅助导入")
    parsed_content_preview: str = Field("", description="预处理阶段正文预览")
    parsed_content_chars: int = Field(0, description="预处理阶段正文字符数")


class KnowledgeIngestUrlResponse(BaseModel):
    success: bool = Field(..., description="是否导入成功")
    ingest_status: str = Field(..., description="导入状态 parsed/fallback/failed")
    chunks_count: int = Field(0, description="入库分块数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="来源元数据")
    parsed_content_preview: str = Field("", description="解析正文预览")
    parsed_content_chars: int = Field(0, description="解析正文字符数")


class KnowledgeSourceItem(BaseModel):
    source_id: str = Field(..., description="来源唯一标识")
    source_type: str = Field(..., description="来源类型")
    source_platform: str = Field(..., description="来源平台")
    source_url: str = Field(..., description="来源链接")
    author: Optional[str] = Field(None, description="作者")
    ingest_mode: str = Field(..., description="导入模式")
    ingest_status: str = Field(..., description="导入状态")
    ingest_error_code: Optional[str] = Field(None, description="导入失败码")
    ingested_at: Optional[str] = Field(None, description="导入时间")
    expires_at: Optional[str] = Field(None, description="过期时间")
    chunks_count: int = Field(0, description="分块数")
    parsed_content_preview: str = Field("", description="解析正文预览")
    parsed_content_chars: int = Field(0, description="解析正文字符数")
    normalized_url: Optional[str] = Field(None, description="规范化链接")
    resolved_url: Optional[str] = Field(None, description="解跳后链接")
    source_risk_level: Optional[str] = Field(None, description="来源风险等级")
    extractor_layer: Optional[str] = Field(None, description="提取层级")
    quality_score: Optional[int] = Field(None, description="质量分")
    failure_reason: Optional[str] = Field(None, description="失败原因")
    retry_count: int = Field(0, description="重试次数")
    last_retry_at: Optional[str] = Field(None, description="最后重试时间")


class KnowledgeSourceStats(BaseModel):
    total: int = Field(0, description="来源总数")
    parsed: int = Field(0, description="解析成功数量")
    fallback: int = Field(0, description="降级导入数量")
    failed: int = Field(0, description="失败数量")


class KnowledgeSourceListResponse(BaseModel):
    knowledge_base_id: str = Field(..., description="知识库ID")
    items: List[KnowledgeSourceItem] = Field(default_factory=list, description="来源列表")
    stats: KnowledgeSourceStats = Field(default_factory=KnowledgeSourceStats, description="来源状态统计")


class KnowledgeSourceDeleteResponse(BaseModel):
    knowledge_base_id: str = Field(..., description="知识库ID")
    source_id: str = Field(..., description="来源唯一标识")
    success: bool = Field(..., description="是否删除成功")
    deleted_chunks: int = Field(0, description="删除分块数")


class KnowledgeSourceUpdateRequest(BaseModel):
    content: str = Field(..., description="更新后的来源正文内容")
    source_url: Optional[str] = Field(None, description="可选来源链接，传入时会覆盖原链接")
    ocr_text: Optional[str] = Field(None, description="可选 OCR 或字幕文本")


class KnowledgeSourceUpdateResponse(BaseModel):
    knowledge_base_id: str = Field(..., description="知识库ID")
    source_id: str = Field(..., description="来源唯一标识")
    success: bool = Field(..., description="是否更新成功")
    chunks_count: int = Field(0, description="更新后分块数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="更新后的来源元数据")
    parsed_content_preview: str = Field("", description="解析正文预览")
    parsed_content_chars: int = Field(0, description="解析正文字符数")


class KnowledgeDebugChunkItem(BaseModel):
    chunk_id: str = Field(..., description="分块ID")
    content: str = Field(..., description="分块内容")
    content_chars: int = Field(0, description="分块字符数")
    chunk_index: int = Field(0, description="分块序号")
    chunk_total: int = Field(0, description="来源总分块数")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="分块元数据")


class KnowledgeDebugSourceItem(BaseModel):
    source_id: str = Field(..., description="来源唯一标识")
    source_type: str = Field(..., description="来源类型")
    source_platform: str = Field(..., description="来源平台")
    source_url: str = Field(..., description="来源链接")
    author: Optional[str] = Field(None, description="作者")
    ingest_mode: str = Field(..., description="导入模式")
    ingest_status: str = Field(..., description="导入状态")
    ingest_error_code: Optional[str] = Field(None, description="导入失败码")
    ingested_at: Optional[str] = Field(None, description="导入时间")
    expires_at: Optional[str] = Field(None, description="过期时间")
    chunks_count: int = Field(0, description="分块数")
    parsed_content_preview: str = Field("", description="解析正文预览")
    parsed_content_chars: int = Field(0, description="解析正文字符数")
    normalized_url: Optional[str] = Field(None, description="规范化链接")
    resolved_url: Optional[str] = Field(None, description="解跳后链接")
    source_risk_level: Optional[str] = Field(None, description="来源风险等级")
    extractor_layer: Optional[str] = Field(None, description="提取层级")
    quality_score: Optional[int] = Field(None, description="质量分")
    failure_reason: Optional[str] = Field(None, description="失败原因")
    retry_count: int = Field(0, description="重试次数")
    last_retry_at: Optional[str] = Field(None, description="最后重试时间")
    chunks: List[KnowledgeDebugChunkItem] = Field(default_factory=list, description="分块调试数据")


class KnowledgeDebugBaseItem(BaseModel):
    knowledge_base_id: str = Field(..., description="知识库ID")
    name: str = Field(..., description="知识库名称")
    collection_name: str = Field(..., description="向量集合名")
    document_count: int = Field(0, description="知识条目分块数量")
    source_count: int = Field(0, description="知识来源数量")
    last_updated_at: Optional[str] = Field(None, description="最后更新时间")
    sources: List[KnowledgeDebugSourceItem] = Field(default_factory=list, description="来源与分块调试数据")


class KnowledgeDebugSnapshotResponse(BaseModel):
    generated_at: str = Field(..., description="快照生成时间")
    items: List[KnowledgeDebugBaseItem] = Field(default_factory=list, description="知识库调试快照列表")
