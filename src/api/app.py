# API 服务入口，提供行程生成、会话列表、知识库检索的 HTTP 接口
from datetime import datetime
import asyncio
import json
import hashlib
from langchain_core.documents.base import Document
import time
import re
import logging
import os
import glob
import sqlite3
import threading
from urllib.parse import urlparse
from uuid import uuid4
from io import BytesIO
from functools import lru_cache  # 用于缓存全局单例对象，减少重复初始化
from typing import Any, Dict, List, Optional, Set, Tuple  # 提供类型注解，便于接口契约清晰

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile  # FastAPI 框架与错误处理
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware  # 允许前端跨域访问
from pydantic import BaseModel, Field  # 数据模型与字段校验
from pypdf import PdfReader

from src.config import Config  # 读取项目配置
from src.frontend.context.conversation_manager import ConversationManager
from src.frontend.context.entity import Message, MessageType
from src.frontend.context.storage import get_conversation_storage  # 获取会话存储实现
from src.llm.llm_manager import LlmManager  # 行程生成核心管理器
from src.map.map_renderer import TripMap
from src.rag.rag_main import AIRetrievalPipeline  # 知识库检索流水线
from src.rag.network.content_validator import validate_content_quality
from src.rag.network.crawler import ContentCrawler
from src.rag.network.url_preprocessor import infer_source_platform, preprocess_url
from src.rag.store.vector_store import VectorStore
from src.agent import run_agent_loop_sync

logger = logging.getLogger(__name__)


class StartSessionRequest(BaseModel):
    """创建新会话的请求体"""
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")


class StartSessionResponse(BaseModel):
    """创建新会话的响应体"""
    session_id: str = Field(..., description="新会话ID")


class SessionItem(BaseModel):
    """会话列表展示结构"""
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")
    name: str = Field(..., description="会话名称")
    update_time: str = Field(..., description="最后更新时间")


class FlowRequestBase(BaseModel):
    """主流程请求基础模型，描述一次规划任务的核心输入。"""
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    destination: str = Field(..., description="目的地城市")
    days: int = Field(..., description="行程天数")
    budget: Optional[str] = Field(None, description="预算（可选）")
    preference: Optional[str] = Field(None, description="偏好（可选）")
    message: Optional[str] = Field(None, description="用户自然语言任务描述（可选）")
    context_texts: List[str] = Field(default_factory=list, description="上下文文本列表")
    knowledge_base_id: Optional[str] = Field(None, description="知识库ID（可选）")
    knowledge_query: Optional[str] = Field(None, description="知识库检索查询（可选）")
    knowledge_scope: Optional[str] = Field("private_plus_public", description="知识范围 private_only/private_plus_public")


class FlowStreamRequest(FlowRequestBase):
    """主流程流式请求体，扩展执行模式字段。"""
    mode: Optional[str] = Field("fast", description="执行模式：fast/deep")


class TripDataResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    trip_data: Optional[Dict[str, Any]] = Field(None, description="结构化行程数据")


class DeleteSessionResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    success: bool = Field(..., description="是否删除成功")


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


class ChatHistoryItem(BaseModel):
    role: str = Field(..., description="消息角色")
    content: str = Field(..., description="消息内容")
    timestamp: str = Field(..., description="消息时间")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="消息元数据")
    is_redundant: bool = Field(False, description="是否为冗余消息")


class ChatSendRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    message: str = Field(..., description="用户输入消息")


class ChatSendResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    response: str = Field(..., description="助手回复")
    trip_data: Optional[Dict[str, Any]] = Field(None, description="结构化行程数据")
    intent: Optional[str] = Field(None, description="意图类型")
    needs_more_info: bool = Field(False, description="是否需要补充信息")


class MapRenderRequest(BaseModel):
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")
    batch_index: Optional[int] = Field(0, description="批次序号，从 0 开始")
    batch_size: Optional[int] = Field(4, description="每批 POI 数量")


class MapRenderResponse(BaseModel):
    map_html: str = Field(..., description="地图 HTML 字符串")
    sequence: int = Field(..., description="当前批次序号")
    day: Optional[str] = Field(None, description="当前批次所属天数")
    is_final: bool = Field(False, description="是否最终批次")


class MapGeoJsonRequest(BaseModel):
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class MapGeoJsonResponse(BaseModel):
    points: Dict[str, Any] = Field(..., description="POI 点位 GeoJSON")
    routes: Dict[str, Any] = Field(..., description="路线 GeoJSON")
    center: Dict[str, float] = Field(..., description="地图中心点")
    bounds: List[float] = Field(..., description="地图边界")
    total_points: int = Field(..., description="POI 数量")


class TripUpdateRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class TripUpdateResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class TripReplanDayRequest(BaseModel):
    user_id: str = Field(..., description="用户唯一ID")
    device_id: str = Field(..., description="设备唯一ID")
    session_id: Optional[str] = Field(None, description="会话ID，可为空以便新建")
    day: int = Field(..., description="需要重新规划的天数")


class TripReplanDayResponse(BaseModel):
    session_id: str = Field(..., description="会话ID")
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class FlowMetricItem(BaseModel):
    message_id: str = Field(..., description="流式消息ID")
    session_id: str = Field(..., description="会话ID")
    user_id: str = Field(..., description="用户ID")
    device_id: str = Field(..., description="设备ID")
    mode: str = Field(..., description="执行模式")
    intent: str = Field(..., description="识别意图")
    status: str = Field(..., description="执行状态")
    latency_ms: int = Field(0, description="端到端耗时毫秒")
    tool_count: int = Field(0, description="工具调用次数")
    rag_hit: bool = Field(False, description="是否命中知识增强")
    agent_escalated: bool = Field(False, description="是否升级Agent")
    context_count: int = Field(0, description="上下文条目数")
    context_chars: int = Field(0, description="上下文字符数")
    context_budget: Dict[str, Any] = Field(default_factory=dict, description="上下文预算配置")
    escalation_reasons: List[str] = Field(default_factory=list, description="升级原因")
    error: Optional[str] = Field(None, description="失败错误信息")
    created_at: str = Field(..., description="记录时间")


class FlowMetricsListResponse(BaseModel):
    total: int = Field(0, description="满足条件的记录总数")
    items: List[FlowMetricItem] = Field(default_factory=list, description="指标明细")


class FlowMetricsSummaryResponse(BaseModel):
    total: int = Field(0, description="满足条件的样本总数")
    success_count: int = Field(0, description="成功样本数")
    failed_count: int = Field(0, description="失败样本数")
    avg_latency_ms: float = Field(0.0, description="平均耗时毫秒")
    p50_latency_ms: float = Field(0.0, description="P50 耗时毫秒")
    p90_latency_ms: float = Field(0.0, description="P90 耗时毫秒")
    agent_escalated_rate: float = Field(0.0, description="Agent 升级比例")
    rag_hit_rate: float = Field(0.0, description="RAG 命中比例")
    avg_tool_count: float = Field(0.0, description="平均工具调用次数")


class FlowControlRequest(BaseModel):
    """主流程控制请求体，支持暂停、恢复、重试。"""
    message_id: str = Field(..., description="目标流式消息ID")
    action: str = Field(..., description="控制动作：pause/resume/retry")


class FlowControlResponse(BaseModel):
    """主流程控制响应体，反馈控制动作是否生效。"""
    message_id: str = Field(..., description="目标流式消息ID")
    action: str = Field(..., description="控制动作")
    accepted: bool = Field(..., description="是否受理成功")
    status: str = Field(..., description="当前流状态")
    next_message_id: Optional[str] = Field(None, description="重试后新消息ID")
    detail: str = Field("", description="控制结果说明")


class FlowStatusResponse(BaseModel):
    """主流程状态响应体，展示运行态与可恢复信息。"""
    message_id: str = Field(..., description="流式消息ID")
    session_id: str = Field(..., description="会话ID")
    running: bool = Field(False, description="是否运行中")
    done: bool = Field(False, description="是否已结束")
    paused: bool = Field(False, description="是否暂停中")
    status: str = Field("running", description="状态：running/paused/done/failed")
    retry_count: int = Field(0, description="已执行重试次数")
    has_error: bool = Field(False, description="是否存在错误")
    last_error: Optional[str] = Field(None, description="最后一次错误信息")
    latest_sequence: int = Field(0, description="当前最新事件序号")
    event_count: int = Field(0, description="事件数量")
    created_at: float = Field(0.0, description="创建时间戳")
    updated_at: float = Field(0.0, description="更新时间戳")


class ReleaseChecklistItem(BaseModel):
    """发布检查项结果。"""
    key: str = Field(..., description="检查项唯一键")
    title: str = Field(..., description="检查项名称")
    required: bool = Field(True, description="是否发布门槛必选项")
    status: str = Field(..., description="检查状态：passed/failed/partial/unknown")
    detail: str = Field("", description="检查结论说明")


class ReleaseGateResponse(BaseModel):
    """最终验收清单与发布门槛响应体。"""
    generated_at: str = Field(..., description="生成时间")
    overall_status: str = Field(..., description="总体状态：passed/blocked")
    checklist: List[ReleaseChecklistItem] = Field(default_factory=list, description="逐项检查结果")
    metrics_snapshot: Dict[str, Any] = Field(default_factory=dict, description="指标快照")
    replay_snapshot: Dict[str, Any] = Field(default_factory=dict, description="回放报告快照")


@lru_cache(maxsize=1)
def _get_config() -> Config:
    """缓存 Config 实例，避免重复读取环境变量"""
    return Config()


@lru_cache(maxsize=1)
def _get_storage():
    """缓存会话存储实例，用于会话列表与行程数据持久化"""
    config = _get_config()
    return get_conversation_storage(config)


@lru_cache(maxsize=1)
def _get_llm_manager() -> LlmManager:
    """缓存 LlmManager 实例，统一行程生成入口"""
    config = _get_config()
    llm_manager = LlmManager(
        model_name=config.GENERATION_MODEL_NAME,  # 使用生成模型名称初始化
        ollama_base_url=config.GENERATION_BASE_URL,  # 兼容旧命名，作为 base_url 兜底
        provider=config.GENERATION_PROVIDER,  # 生成模型提供方
        base_url=config.GENERATION_BASE_URL,  # 生成模型 Base URL
        api_key=config.GENERATION_API_KEY,  # 生成模型 API Key
        temperature=config.GENERATION_TEMPERATURE,  # 生成温度
    )
    llm_manager.update_llm_config(
        {
            "provider": config.GENERATION_PROVIDER,  # 通用 provider
            "base_url": config.GENERATION_BASE_URL,  # 通用 base_url
            "model_name": config.GENERATION_MODEL_NAME,  # 通用 model_name
            "api_key": config.GENERATION_API_KEY,  # 通用 api_key
            "temperature": config.GENERATION_TEMPERATURE,  # 通用温度
            "analysis_provider": config.ANALYSIS_PROVIDER,  # 分析阶段 provider
            "analysis_base_url": config.ANALYSIS_BASE_URL,  # 分析阶段 base_url
            "analysis_model_name": config.ANALYSIS_MODEL_NAME,  # 分析阶段模型
            "analysis_api_key": config.ANALYSIS_API_KEY,  # 分析阶段 key
            "analysis_temperature": config.ANALYSIS_TEMPERATURE,  # 分析阶段温度
            "generation_provider": config.GENERATION_PROVIDER,  # 生成阶段 provider
            "generation_base_url": config.GENERATION_BASE_URL,  # 生成阶段 base_url
            "generation_model_name": config.GENERATION_MODEL_NAME,  # 生成阶段模型
            "generation_api_key": config.GENERATION_API_KEY,  # 生成阶段 key
            "generation_temperature": config.GENERATION_TEMPERATURE,  # 生成阶段温度
        }
    )
    return llm_manager


@lru_cache(maxsize=1)
def _get_conversation_manager() -> ConversationManager:
    storage = _get_storage()
    llm_manager = _get_llm_manager()
    return ConversationManager(storage, llm_manager)


@lru_cache(maxsize=1)
def _get_rag_pipeline() -> AIRetrievalPipeline:
    """缓存 RAG Pipeline 实例，提供知识库检索能力"""
    llm_manager = _get_llm_manager()
    return AIRetrievalPipeline(llm_manager.get_analysis_llm())


@lru_cache(maxsize=1)
def _get_map_renderer() -> TripMap:
    return TripMap()


KNOWLEDGE_COLLECTION_PREFIX = "kb_"
KNOWLEDGE_REGISTRY_COLLECTION = "knowledge_registry"
SOCIAL_SOURCE_PLATFORMS = {"xiaohongshu", "weibo", "bilibili", "zhihu"}
FAILED_SOURCE_RECORD_TYPE = "knowledge_failed_source"


@lru_cache(maxsize=1)
def _get_knowledge_store() -> VectorStore:
    """缓存知识库向量实例，统一管理 collection 生命周期。"""
    return VectorStore(collection_name=KNOWLEDGE_REGISTRY_COLLECTION)


def _normalize_knowledge_base_id(raw_id: str) -> str:
    """将知识库ID标准化为可持久化命名。"""
    cleaned = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fa5]", "_", str(raw_id or "").strip())
    cleaned = cleaned.strip("_")
    if not cleaned:
        raise HTTPException(status_code=400, detail="知识库名称不能为空")
    return cleaned[:48]


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
    # Chroma 在单条件和多条件下的 where 结构不同，这里统一做一层适配，
    # 避免调用方在删除 registry、查失败来源、查知识库定义时重复拼装 "$and"。
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
        # 失败来源没有真实 chunk，因此这里显式补一个“零分块”的来源项。
        # 这样前端来源列表、统计面板和“补全文本重试”入口都能看见它。
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
    # 失败来源与知识库定义共用一个 registry 集合，所以需要通过 record_type 区分。
    # 这里使用“先删旧记录再写新记录”的 upsert 方式，保证 source_id 维度始终只有一条最新失败快照。
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
    # 预处理阶段与正式 ingest 必须尽量复用同一套自动解析逻辑，
    # 否则前端会出现“预判能导入，真正导入却失败”的提示漂移。
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
        # 只要拿到了正文，就继续走质量门禁，而不是简单按“是否非空”判成功。
        # 这样可以提前拦住登录墙、风控页、广告页、过短文本等“看起来有字但不可用”的内容。
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
    # 高风险平台或者已命中失败码时，前端应该优先引导用户走手动/OCR，
    # 而不是继续让用户在自动解析上反复重试。
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
        # 一个来源可能因为文本分块被拆成多条向量记录，这里要重新按 source_id 聚合回“来源视图”，
        # 否则前端来源列表会把同一个 URL 渲染成多条记录。
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
        # 已入库来源来自向量集合，失败来源来自 registry；两边都要合并后前端才能看到完整状态。
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


def _build_knowledge_context_texts(
    knowledge_base_id: Optional[str],
    destination: str,
    days: int,
    budget: Optional[str],
    preference: Optional[str],
    knowledge_query: Optional[str],
) -> List[str]:
    """从指定知识库检索与本次行程相关的上下文片段并转为提示词上下文。"""
    context_texts, _ = _build_knowledge_context_payload(
        knowledge_base_id,
        destination,
        days,
        budget,
        preference,
        knowledge_query,
    )
    return context_texts


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
            # 这里刻意只回传“命中的来源集合”，而不是整个知识库来源列表。
            # 主流程调试区需要回答“这次检索到底命中了哪些来源”，不是“库里一共有多少来源”。
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


def _ensure_session_id(user_id: str, device_id: str, session_id: Optional[str]) -> str:
    """确保存在会话ID，不存在则创建并落库"""
    storage = _get_storage()
    if session_id:
        return session_id
    new_session_id = storage.generate_session_id(user_id, device_id)
    storage.store_session(user_id, new_session_id)
    return new_session_id


def _normalize_daily_plan(trip_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    daily_plan_raw = trip_data.get("daily_plan")
    if isinstance(daily_plan_raw, dict):
        return {str(key): value for key, value in daily_plan_raw.items() if isinstance(value, list)}
    if isinstance(daily_plan_raw, list):
        return {"1": daily_plan_raw}
    return {}


def _build_agent_thread_id(user_id: str, device_id: str) -> str:
    timestamp_ms = int(datetime.now().timestamp() * 1000)
    return f"{user_id}-{device_id}-{timestamp_ms}"


def _normalize_flow_mode(mode: Optional[str]) -> str:
    """规范化主流程模式，非法值统一回退到 fast。"""
    value = str(mode or "fast").strip().lower()
    if value not in {"fast", "deep"}:
        return "fast"
    return value


def _detect_agent_escalation(
    mode: str,
    intent: str,
    user_input: Dict[str, Any],
    knowledge_query: Optional[str],
    tool_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """根据模式、任务复杂度、知识增强与工具结果判定是否升级 Agent。"""
    reasons: List[str] = []
    days_value = int(user_input.get("days") or 0)
    if mode == "deep":
        reasons.append("deep_mode")
    if intent in {"modify_trip", "reorder_trip", "add_attraction", "delete_attraction"}:
        reasons.append("complex_intent")
    if days_value >= 4:
        reasons.append("multi_step_days")
    if knowledge_query and str(knowledge_query).strip():
        reasons.append("knowledge_enhanced_planning")
    if isinstance(tool_result, dict):
        if tool_result.get("needs_tool") and not ((tool_result.get("result") or {}).get("success")):
            reasons.append("tool_retry_or_fallback")
    return {"agent_escalated": len(reasons) > 0, "reasons": reasons}


def _build_flow_query_text(payload: FlowStreamRequest) -> str:
    """构建主流程查询文本，优先使用 message，缺失时回退结构化拼接。"""
    message_value = str(getattr(payload, "message", "") or "").strip()
    if message_value:
        return message_value
    parts: List[str] = []
    destination = str(payload.destination or "").strip()
    if destination:
        parts.append(f"目的地 {destination}")
    if int(payload.days or 0) > 0:
        parts.append(f"{int(payload.days)} 天")
    budget = str(payload.budget or "").strip()
    if budget:
        parts.append(f"预算 {budget}")
    preference = str(payload.preference or "").strip()
    if preference:
        parts.append(f"偏好 {preference}")
    knowledge_query = str(payload.knowledge_query or "").strip()
    if knowledge_query:
        parts.append(f"知识需求 {knowledge_query}")
    return "，".join(parts) or destination


def _build_flow_context_messages(context_texts: List[str]) -> List[Dict[str, str]]:
    """将上下文文本转换为分析模型可消费的角色消息结构。"""
    normalized: List[Dict[str, str]] = []
    for text in context_texts or []:
        content = str(text or "").strip()
        if not content:
            continue
        normalized.append({"role": "user", "content": content})
        if len(normalized) >= _FLOW_CONTEXT_MAX_ITEMS:
            break
    return normalized


def _merge_context_with_budget(context_texts: List[str]) -> List[str]:
    """对主流程上下文做去重与证据预算裁剪，避免冗余与超长输入。"""
    deduped: List[str] = []
    seen: set[str] = set()
    total_chars = 0
    for raw_text in context_texts or []:
        text = str(raw_text or "").strip()
        if not text:
            continue
        normalized_key = " ".join(text.split()).lower()
        if normalized_key in seen:
            continue
        bounded_text = text[:_FLOW_CONTEXT_ITEM_MAX_CHARS]
        next_total = total_chars + len(bounded_text)
        if next_total > _FLOW_CONTEXT_TOTAL_MAX_CHARS:
            break
        seen.add(normalized_key)
        deduped.append(bounded_text)
        total_chars = next_total
        if len(deduped) >= _FLOW_CONTEXT_MAX_ITEMS:
            break
    return deduped


def _row_to_session_item(row: Any) -> Dict[str, str]:
    """将数据库行转换为可序列化字典"""
    return {
        "session_id": str(row["session_id"]) if isinstance(row, dict) or hasattr(row, "__getitem__") else "",
        "user_id": str(row["user_id"]) if isinstance(row, dict) or hasattr(row, "__getitem__") else "",
        "name": str(row["name"]) if isinstance(row, dict) or hasattr(row, "__getitem__") else "",
        "update_time": str(row["update_time"]) if isinstance(row, dict) or hasattr(row, "__getitem__") else "",
    }


def _normalize_message_payload(message: Message) -> Dict[str, Any]:
    payload = message.model_dump()
    payload["timestamp"] = message.timestamp.isoformat()
    return payload


def _get_context_messages(storage, session_id: str) -> List[Dict[str, Any]]:
    short_term_context = storage.get_short_term_context(session_id)
    if isinstance(short_term_context, dict):
        messages = short_term_context.get("messages") or []
        if messages:
            return messages[-10:]
    history_messages = storage.get_session_chat_list(session_id)
    normalized_messages: List[Dict[str, Any]] = []
    if history_messages:
        for message_json in history_messages[-10:]:
            try:
                message_obj = Message.model_validate_json(message_json)
                normalized_messages.append(_normalize_message_payload(message_obj))
            except Exception:
                continue
    return normalized_messages


CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app = FastAPI(title="TripNexus API", version="0.0.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有请求头
)

_flow_streams: Dict[str, Dict[str, Any]] = {}
_flow_streams_lock = asyncio.Lock()
_FLOW_STREAM_TTL_SECONDS = 600
_FLOW_CONTEXT_MAX_ITEMS = 12
_FLOW_CONTEXT_ITEM_MAX_CHARS = 600
_FLOW_CONTEXT_TOTAL_MAX_CHARS = 3200
_FLOW_METRICS_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "flow_metrics.db"))
_flow_metrics_lock = threading.Lock()


def _init_flow_metrics_table() -> None:
    """初始化主流程指标表，确保落库查询能力可用。"""
    with _flow_metrics_lock:
        conn = sqlite3.connect(_FLOW_METRICS_DB_PATH, check_same_thread=False)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS flow_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    tool_count INTEGER NOT NULL DEFAULT 0,
                    rag_hit INTEGER NOT NULL DEFAULT 0,
                    agent_escalated INTEGER NOT NULL DEFAULT 0,
                    context_count INTEGER NOT NULL DEFAULT 0,
                    context_chars INTEGER NOT NULL DEFAULT 0,
                    context_budget_json TEXT NOT NULL DEFAULT '{}',
                    escalation_reasons_json TEXT NOT NULL DEFAULT '[]',
                    error_text TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def _to_int(value: Any, default: int = 0) -> int:
    """将任意值安全转换为整数，失败时返回默认值。"""
    try:
        return int(value)
    except Exception:
        return int(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    """将任意值安全转换为浮点数，失败时返回默认值。"""
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_json_loads(text: Any, fallback: Any) -> Any:
    """将 JSON 字符串解析为对象，失败时返回兜底值。"""
    try:
        return json.loads(str(text or ""))
    except Exception:
        return fallback


def _percentile(values: List[int], ratio: float) -> float:
    """计算百分位值，输入为空时返回 0。"""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    idx = int(round((len(sorted_values) - 1) * max(0.0, min(1.0, ratio))))
    idx = max(0, min(len(sorted_values) - 1, idx))
    return float(sorted_values[idx])


def _record_flow_metrics(payload: Dict[str, Any]) -> None:
    """持久化单次主流程执行指标，供后续明细与聚合查询。"""
    _init_flow_metrics_table()
    with _flow_metrics_lock:
        conn = sqlite3.connect(_FLOW_METRICS_DB_PATH, check_same_thread=False)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO flow_metrics (
                    message_id, session_id, user_id, device_id, mode, intent, status,
                    latency_ms, tool_count, rag_hit, agent_escalated, context_count, context_chars,
                    context_budget_json, escalation_reasons_json, error_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("message_id") or ""),
                    str(payload.get("session_id") or ""),
                    str(payload.get("user_id") or ""),
                    str(payload.get("device_id") or ""),
                    str(payload.get("mode") or "fast"),
                    str(payload.get("intent") or ""),
                    str(payload.get("status") or "done"),
                    _to_int(payload.get("latency_ms"), 0),
                    _to_int(payload.get("tool_count"), 0),
                    1 if bool(payload.get("rag_hit")) else 0,
                    1 if bool(payload.get("agent_escalated")) else 0,
                    _to_int(payload.get("context_count"), 0),
                    _to_int(payload.get("context_chars"), 0),
                    json.dumps(payload.get("context_budget") or {}, ensure_ascii=False),
                    json.dumps(payload.get("escalation_reasons") or [], ensure_ascii=False),
                    str(payload.get("error") or "") or None,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _build_flow_metrics_filters_sql(
    start_time: Optional[str],
    end_time: Optional[str],
    mode: Optional[str],
    intent: Optional[str],
    status: Optional[str],
    user_id: Optional[str],
    device_id: Optional[str],
    session_id: Optional[str],
    agent_escalated: Optional[bool],
    rag_hit: Optional[bool],
) -> tuple[str, List[Any]]:
    """构建指标查询的 WHERE SQL 与参数列表。"""
    clauses: List[str] = []
    params: List[Any] = []
    if start_time:
        clauses.append("created_at >= ?")
        params.append(start_time)
    if end_time:
        clauses.append("created_at <= ?")
        params.append(end_time)
    if mode:
        clauses.append("mode = ?")
        params.append(mode)
    if intent:
        clauses.append("intent = ?")
        params.append(intent)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if device_id:
        clauses.append("device_id = ?")
        params.append(device_id)
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if agent_escalated is not None:
        clauses.append("agent_escalated = ?")
        params.append(1 if agent_escalated else 0)
    if rag_hit is not None:
        clauses.append("rag_hit = ?")
        params.append(1 if rag_hit else 0)
    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where_sql, params


def _query_flow_metrics_rows(
    start_time: Optional[str],
    end_time: Optional[str],
    mode: Optional[str],
    intent: Optional[str],
    status: Optional[str],
    user_id: Optional[str],
    device_id: Optional[str],
    session_id: Optional[str],
    agent_escalated: Optional[bool],
    rag_hit: Optional[bool],
    limit: int,
    offset: int,
) -> tuple[int, List[Dict[str, Any]]]:
    """查询指标明细并返回总量与分页结果。"""
    _init_flow_metrics_table()
    where_sql, params = _build_flow_metrics_filters_sql(
        start_time,
        end_time,
        mode,
        intent,
        status,
        user_id,
        device_id,
        session_id,
        agent_escalated,
        rag_hit,
    )
    with _flow_metrics_lock:
        conn = sqlite3.connect(_FLOW_METRICS_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(1) AS total FROM flow_metrics{where_sql}", params)
            total_row = cursor.fetchone()
            total = _to_int(total_row["total"] if total_row else 0, 0)
            cursor.execute(
                f"""
                SELECT message_id, session_id, user_id, device_id, mode, intent, status,
                       latency_ms, tool_count, rag_hit, agent_escalated, context_count, context_chars,
                       context_budget_json, escalation_reasons_json, error_text, created_at
                FROM flow_metrics
                {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                params + [max(1, limit), max(0, offset)],
            )
            rows = []
            for row in cursor.fetchall():
                row_data = dict(row)
                rows.append(
                    {
                        "message_id": str(row_data.get("message_id") or ""),
                        "session_id": str(row_data.get("session_id") or ""),
                        "user_id": str(row_data.get("user_id") or ""),
                        "device_id": str(row_data.get("device_id") or ""),
                        "mode": str(row_data.get("mode") or "fast"),
                        "intent": str(row_data.get("intent") or ""),
                        "status": str(row_data.get("status") or "done"),
                        "latency_ms": _to_int(row_data.get("latency_ms"), 0),
                        "tool_count": _to_int(row_data.get("tool_count"), 0),
                        "rag_hit": bool(_to_int(row_data.get("rag_hit"), 0)),
                        "agent_escalated": bool(_to_int(row_data.get("agent_escalated"), 0)),
                        "context_count": _to_int(row_data.get("context_count"), 0),
                        "context_chars": _to_int(row_data.get("context_chars"), 0),
                        "context_budget": _safe_json_loads(row_data.get("context_budget_json"), {}),
                        "escalation_reasons": _safe_json_loads(row_data.get("escalation_reasons_json"), []),
                        "error": row_data.get("error_text"),
                        "created_at": str(row_data.get("created_at") or ""),
                    }
                )
            return total, rows
        finally:
            conn.close()


def _query_flow_metrics_summary(
    start_time: Optional[str],
    end_time: Optional[str],
    mode: Optional[str],
    intent: Optional[str],
    status: Optional[str],
    user_id: Optional[str],
    device_id: Optional[str],
    session_id: Optional[str],
    agent_escalated: Optional[bool],
    rag_hit: Optional[bool],
) -> Dict[str, Any]:
    """查询指标聚合摘要，输出成功率与时延分位信息。"""
    _init_flow_metrics_table()
    where_sql, params = _build_flow_metrics_filters_sql(
        start_time,
        end_time,
        mode,
        intent,
        status,
        user_id,
        device_id,
        session_id,
        agent_escalated,
        rag_hit,
    )
    with _flow_metrics_lock:
        conn = sqlite3.connect(_FLOW_METRICS_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT status, latency_ms, tool_count, rag_hit, agent_escalated
                FROM flow_metrics
                {where_sql}
                """,
                params,
            )
            rows = [dict(item) for item in cursor.fetchall()]
        finally:
            conn.close()
    total = len(rows)
    if total == 0:
        return {
            "total": 0,
            "success_count": 0,
            "failed_count": 0,
            "avg_latency_ms": 0.0,
            "p50_latency_ms": 0.0,
            "p90_latency_ms": 0.0,
            "agent_escalated_rate": 0.0,
            "rag_hit_rate": 0.0,
            "avg_tool_count": 0.0,
        }
    success_count = len([row for row in rows if str(row.get("status") or "") == "done"])
    failed_count = total - success_count
    latencies = [_to_int(row.get("latency_ms"), 0) for row in rows if _to_int(row.get("latency_ms"), 0) > 0]
    tool_counts = [_to_int(row.get("tool_count"), 0) for row in rows]
    escalated_count = len([row for row in rows if bool(_to_int(row.get("agent_escalated"), 0))])
    rag_hit_count = len([row for row in rows if bool(_to_int(row.get("rag_hit"), 0))])
    return {
        "total": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "avg_latency_ms": _to_float(sum(latencies) / len(latencies), 0.0) if latencies else 0.0,
        "p50_latency_ms": _percentile(latencies, 0.5) if latencies else 0.0,
        "p90_latency_ms": _percentile(latencies, 0.9) if latencies else 0.0,
        "agent_escalated_rate": _to_float(escalated_count / total, 0.0),
        "rag_hit_rate": _to_float(rag_hit_count / total, 0.0),
        "avg_tool_count": _to_float(sum(tool_counts) / len(tool_counts), 0.0) if tool_counts else 0.0,
    }


async def _cleanup_flow_streams() -> None:
    """清理已结束且超过保留期的流式会话缓存。"""
    now = time.time()
    async with _flow_streams_lock:
        expired = []
        for key, payload in _flow_streams.items():
            updated_at = float(payload.get("updated_at") or now)
            done = bool(payload.get("done"))
            running = bool(payload.get("running"))
            if done and not running and now - updated_at > _FLOW_STREAM_TTL_SECONDS:
                expired.append(key)
        for key in expired:
            _flow_streams.pop(key, None)


async def _append_flow_event(message_id: str, event_payload: Dict[str, Any]) -> None:
    """向指定流会话追加事件，并在终态时关闭运行标记。"""
    async with _flow_streams_lock:
        stream_state = _flow_streams.get(message_id)
        if not stream_state:
            return
        stream_state["events"].append(event_payload)
        stream_state["updated_at"] = time.time()
        stream_state["last_status"] = str(event_payload.get("status") or stream_state.get("last_status") or "running")
        if str(event_payload.get("status") or "") == "failed":
            payload_obj = event_payload.get("payload") if isinstance(event_payload.get("payload"), dict) else {}
            stream_state["last_error"] = str((payload_obj or {}).get("error") or "")
        if bool(event_payload.get("is_final")) or event_payload.get("event") == "error":
            stream_state["done"] = True
            stream_state["running"] = False


async def _pause_checkpoint(
    message_id: str,
    session_id: str,
    sequence: int,
    flow_mode: str,
    step: str,
) -> int:
    """在关键阶段检查暂停标记，暂停时阻塞执行并输出暂停/恢复事件。"""
    pause_emitted = False
    next_sequence = int(sequence)
    while True:
        async with _flow_streams_lock:
            stream_state = _flow_streams.get(message_id)
            pause_requested = bool(stream_state.get("pause_requested")) if isinstance(stream_state, dict) else False
            done = bool(stream_state.get("done")) if isinstance(stream_state, dict) else False
        if done:
            return next_sequence
        if not pause_requested:
            if pause_emitted:
                next_sequence += 1
                await _append_flow_event(
                    message_id,
                    {
                        "event": "delta",
                        "sequence": next_sequence,
                        "message_id": message_id,
                        "session_id": session_id,
                        "step": "control",
                        "status": "running",
                        "mode": flow_mode,
                        "content_delta": "流程已恢复执行",
                        "is_final": False,
                        "payload": {"from_step": step},
                    },
                )
            return next_sequence
        if not pause_emitted:
            next_sequence += 1
            await _append_flow_event(
                message_id,
                {
                    "event": "delta",
                    "sequence": next_sequence,
                    "message_id": message_id,
                    "session_id": session_id,
                    "step": "control",
                    "status": "paused",
                    "mode": flow_mode,
                    "content_delta": "流程已暂停，等待恢复",
                    "is_final": False,
                    "payload": {"from_step": step},
                },
            )
            pause_emitted = True
        await asyncio.sleep(0.2)


async def _get_flow_state(message_id: str) -> Optional[Dict[str, Any]]:
    """读取指定主流程运行状态，供控制与状态查询使用。"""
    async with _flow_streams_lock:
        stream_state = _flow_streams.get(message_id)
        if not isinstance(stream_state, dict):
            return None
        return dict(stream_state)


def _load_latest_replay_report() -> Dict[str, Any]:
    """读取最新回放报告，未找到时返回空字典。"""
    report_candidates = glob.glob(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "flow_replay_report_*.json")))
    if not report_candidates:
        return {}
    latest_path = sorted(report_candidates)[-1]
    try:
        with open(latest_path, "r", encoding="utf-8") as handle:
            parsed = json.loads(handle.read())
            if isinstance(parsed, dict):
                parsed["_report_path"] = latest_path
                return parsed
    except Exception:
        return {}
    return {}


def _build_release_gate_from_data(metrics_summary: Dict[str, Any], replay_report: Dict[str, Any]) -> ReleaseGateResponse:
    """根据指标快照与回放报告生成发布门槛判定结果。"""
    checklist: List[ReleaseChecklistItem] = []
    total_metrics = _to_int(metrics_summary.get("total"), 0)
    obs_passed = total_metrics > 0
    checklist.append(
        ReleaseChecklistItem(
            key="observability_metrics",
            title="关键指标落库与可查询",
            required=True,
            status="passed" if obs_passed else "failed",
            detail=f"当前可查询样本数: {total_metrics}",
        )
    )
    replay_total = _to_int(replay_report.get("total_cases"), 0)
    replay_success_rate = _to_float(replay_report.get("success_rate"), 0.0)
    fast_non_agent_ratio = _to_float(replay_report.get("fast_non_agent_ratio"), 0.0)
    replay_exists = replay_total > 0
    checklist.append(
        ReleaseChecklistItem(
            key="functional_non_agent_ratio",
            title="常规请求非Agent占比",
            required=True,
            status="passed" if replay_exists and fast_non_agent_ratio >= 0.9 else ("failed" if replay_exists else "unknown"),
            detail=f"fast_non_agent_ratio={fast_non_agent_ratio:.2%}, replay_total={replay_total}",
        )
    )
    checklist.append(
        ReleaseChecklistItem(
            key="functional_success_rate",
            title="回放样本成功率",
            required=True,
            status="passed" if replay_exists and replay_success_rate >= 0.9 else ("failed" if replay_exists else "unknown"),
            detail=f"success_rate={replay_success_rate:.2%}, replay_total={replay_total}",
        )
    )
    pause_resume_supported = True
    checklist.append(
        ReleaseChecklistItem(
            key="stability_pause_resume_retry",
            title="复杂任务暂停/恢复/重试闭环",
            required=True,
            status="passed" if pause_resume_supported else "failed",
            detail="已提供 /api/flow/control 与 /api/flow/status 控制与观测接口",
        )
    )
    avg_latency = _to_float(metrics_summary.get("avg_latency_ms"), 0.0)
    latency_has_data = avg_latency > 0
    checklist.append(
        ReleaseChecklistItem(
            key="performance_latency_baseline",
            title="性能目标（时延）可验证",
            required=False,
            status="partial" if latency_has_data else "unknown",
            detail="当前仅有实时样本时延，需与历史基线对照才能判定“下降20%”",
        )
    )
    checklist.append(
        ReleaseChecklistItem(
            key="cost_token_baseline",
            title="成本目标（Token）可验证",
            required=False,
            status="partial",
            detail="暂未建立标准化 token 基线对照报表，需在下一阶段补齐",
        )
    )
    required_items = [item for item in checklist if item.required]
    blocked = any(item.status != "passed" for item in required_items)
    return ReleaseGateResponse(
        generated_at=datetime.now().isoformat(),
        overall_status="blocked" if blocked else "passed",
        checklist=checklist,
        metrics_snapshot=metrics_summary,
        replay_snapshot=replay_report,
    )


async def _run_flow_stream(
    message_id: str,
    session_id: str,
    llm_manager: LlmManager,
    payload: FlowStreamRequest,
) -> None:
    """执行单主流程编排：工具与RAG增强、条件升级 Agent、统一流式事件输出。"""
    last_sequence = 0
    started_at = time.perf_counter()
    flow_mode = _normalize_flow_mode(payload.mode)
    metrics: Dict[str, Any] = {
        "tool_count": 0,
        "rag_hit": False,
        "agent_escalated": False,
        "knowledge_scope": str(payload.knowledge_scope or "private_plus_public"),
    }
    trip_data: Optional[Dict[str, Any]] = None
    escalation_reasons: List[str] = []
    user_input = {
        "destination": payload.destination,
        "days": payload.days,
        "budget": payload.budget,
        "preference": payload.preference,
    }
    merged_context_texts = list(payload.context_texts or [])
    response_text = ""
    source_evidence: List[Dict[str, Any]] = []
    allow_public_fusion = str(payload.knowledge_scope or "private_plus_public").strip().lower() != "private_only"
    logger.info(
        "flow_start message_id=%s session_id=%s knowledge_scope=%s allow_public_fusion=%s knowledge_base_id=%s",
        message_id,
        session_id,
        str(payload.knowledge_scope or "private_plus_public"),
        allow_public_fusion,
        str(payload.knowledge_base_id or ""),
    )
    try:
        # 初始化主流程 start 事件，通知前端进入统一状态机
        last_sequence += 1
        await _append_flow_event(
            message_id,
            {
                "event": "start",
                "sequence": last_sequence,
                "message_id": message_id,
                "session_id": session_id,
                "step": "intent",
                "status": "running",
                "mode": flow_mode,
                "content_delta": "",
                "is_final": False,
                "payload": {},
            },
        )
        last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "intent")
        flow_query = _build_flow_query_text(payload)
        context_messages = _build_flow_context_messages(merged_context_texts)
        current_trip = _get_storage().get_trip_data(session_id)
        intent_data = llm_manager.analyze_user_message(flow_query, context_messages, current_trip)
        intent = str(intent_data.get("intent") or "generate_trip")
        metrics["intent"] = intent
        last_sequence += 1
        await _append_flow_event(
            message_id,
            {
                "event": "delta",
                "sequence": last_sequence,
                "message_id": message_id,
                "session_id": session_id,
                "step": "intent",
                "status": "done",
                "mode": flow_mode,
                "content_delta": f"意图识别完成：{intent}",
                "is_final": False,
                "payload": {
                    "intent": intent,
                    "summary": intent_data.get("summary"),
                    "needs_more_info": bool(intent_data.get("needs_more_info")),
                },
            },
        )

        # 工具调用先于生成阶段执行，成功结果会注入上下文
        last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "tool")
        tool_query = llm_manager._build_tool_query(user_input=user_input, query=flow_query)
        tool_result: Dict[str, Any] = {"needs_tool": False}
        if tool_query and allow_public_fusion:
            tool_result = llm_manager.call_tool_by_llm(tool_query, context_messages)
            if tool_result.get("needs_tool"):
                metrics["tool_count"] = 1
                result_payload = tool_result.get("result")
                if isinstance(result_payload, dict) and result_payload.get("success"):
                    merged_context_texts.append(f"工具结果：{json.dumps(result_payload, ensure_ascii=False)}")
            logger.info(
                "flow_tool_result message_id=%s needs_tool=%s success=%s",
                message_id,
                bool(tool_result.get("needs_tool")),
                bool(((tool_result.get("result") or {}) if isinstance(tool_result, dict) else {}).get("success")),
            )
        elif tool_query and not allow_public_fusion:
            logger.info("flow_tool_skipped_private_only message_id=%s reason=knowledge_scope_private_only", message_id)
        kb_context_texts, kb_context_docs = _build_knowledge_context_payload(
            payload.knowledge_base_id,
            payload.destination,
            payload.days,
            payload.budget,
            payload.preference,
            payload.knowledge_query,
        )
        merged_context_texts.extend(kb_context_texts)
        if kb_context_texts:
            metrics["rag_hit"] = True
            source_evidence = _build_source_evidence_from_docs(kb_context_docs)
        logger.info(
            "flow_private_context message_id=%s kb_context_count=%s source_evidence_count=%s",
            message_id,
            len(kb_context_texts),
            len(source_evidence),
        )
        merged_context_texts = _merge_context_with_budget(merged_context_texts)
        metrics["context_count"] = len(merged_context_texts)
        metrics["context_chars"] = sum([len(item) for item in merged_context_texts])
        metrics["context_budget"] = {
            "max_items": _FLOW_CONTEXT_MAX_ITEMS,
            "item_max_chars": _FLOW_CONTEXT_ITEM_MAX_CHARS,
            "total_max_chars": _FLOW_CONTEXT_TOTAL_MAX_CHARS,
        }

        # 根据策略决定是否进入 Agent 深度执行
        last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "route")
        escalation = _detect_agent_escalation(flow_mode, intent, user_input, payload.knowledge_query, tool_result)
        metrics["agent_escalated"] = bool(escalation.get("agent_escalated"))
        escalation_reasons = list(escalation.get("reasons") or [])

        if metrics["agent_escalated"]:
            last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "agent")
            last_sequence += 1
            await _append_flow_event(
                message_id,
                {
                    "event": "delta",
                    "sequence": last_sequence,
                    "message_id": message_id,
                    "session_id": session_id,
                    "step": "agent",
                    "status": "running",
                    "mode": flow_mode,
                    "content_delta": "进入深度规划流程",
                    "is_final": False,
                    "payload": {"reasons": escalation_reasons},
                },
            )
            thread_id = _build_agent_thread_id(payload.user_id, payload.device_id)
            final_state = run_agent_loop_sync(
                llm_manager=llm_manager,
                user_input=user_input,
                thread_id=thread_id,
                agent_config={"mode": flow_mode},
                user_intent="generate_trip",
                context=merged_context_texts,
                resume=False,
            )
            if final_state and isinstance(final_state.final_payload, dict):
                draft_trip = final_state.final_payload.get("draft_trip")
                if isinstance(draft_trip, dict) and draft_trip:
                    trip_data = draft_trip
        elif intent == "general_conversation":
            response_chunks: List[str] = []
            stream = llm_manager.stream_chat_response(flow_query, context_messages, current_trip)
            for delta_text in stream:
                last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "generate")
                delta_value = str(delta_text or "")
                if not delta_value:
                    continue
                response_chunks.append(delta_value)
                last_sequence += 1
                await _append_flow_event(
                    message_id,
                    {
                        "event": "delta",
                        "sequence": last_sequence,
                        "message_id": message_id,
                        "session_id": session_id,
                        "step": "generate",
                        "status": "running",
                        "mode": flow_mode,
                        "content_delta": delta_value,
                        "is_final": False,
                        "payload": {},
                    },
                )
                await asyncio.sleep(0)
            response_text = "".join(response_chunks)
        elif intent in {"modify_trip", "add_attraction", "delete_attraction", "reorder_trip"} and current_trip:
            last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "modify")
            change_result = llm_manager.change_trip(flow_query, context_messages, current_trip)
            trip_data = change_result.get("trip_data")
            response_text = str(change_result.get("response") or "")
            if response_text:
                last_sequence += 1
                await _append_flow_event(
                    message_id,
                    {
                        "event": "delta",
                        "sequence": last_sequence,
                        "message_id": message_id,
                        "session_id": session_id,
                        "step": "generate",
                        "status": "running",
                        "mode": flow_mode,
                        "content_delta": response_text,
                        "is_final": False,
                        "payload": {"intent": intent},
                    },
                )
        else:
            # 常规模式：直接走行程文本流并在末尾解析为结构化 trip_data
            response_chunks: List[str] = []
            stream = llm_manager.stream_trip_generation(user_input, merged_context_texts)
            for event in llm_manager.build_stream_events_from_stream(stream, message_id):
                last_sequence = await _pause_checkpoint(message_id, session_id, last_sequence, flow_mode, "generate")
                raw_event = str(event.get("event") or "")
                if raw_event not in {"start", "delta", "end"}:
                    continue
                if raw_event == "end":
                    continue
                delta_text = event.get("content_delta") or ""
                if raw_event == "delta":
                    response_chunks.append(delta_text)
                last_sequence += 1
                await _append_flow_event(
                    message_id,
                    {
                        "event": raw_event,
                        "sequence": last_sequence,
                        "message_id": message_id,
                        "session_id": session_id,
                        "step": "generate",
                        "status": "running",
                        "mode": flow_mode,
                        "content_delta": delta_text,
                        "is_final": False,
                        "payload": {},
                    },
                )
                await asyncio.sleep(0)
            response_text = "".join(response_chunks)
            trip_data = llm_manager.parse_trip_from_response_text(response_text)

        if trip_data:
            _get_storage().store_trip_data(session_id, trip_data)
        metrics["latency_ms"] = int((time.perf_counter() - started_at) * 1000)
        _record_flow_metrics(
            {
                "message_id": message_id,
                "session_id": session_id,
                "user_id": payload.user_id,
                "device_id": payload.device_id,
                "mode": flow_mode,
                "intent": metrics.get("intent") or "",
                "status": "done",
                "latency_ms": metrics.get("latency_ms") or 0,
                "tool_count": metrics.get("tool_count") or 0,
                "rag_hit": bool(metrics.get("rag_hit")),
                "agent_escalated": bool(metrics.get("agent_escalated")),
                "context_count": metrics.get("context_count") or 0,
                "context_chars": metrics.get("context_chars") or 0,
                "context_budget": metrics.get("context_budget") or {},
                "escalation_reasons": escalation_reasons,
                "error": None,
            }
        )

        # 统一 finalize 终态事件，输出 trip_data 与关键指标
        last_sequence += 1
        await _append_flow_event(
            message_id,
            {
                "event": "end",
                "sequence": last_sequence,
                "message_id": message_id,
                "session_id": session_id,
                "step": "finalize",
                "status": "done",
                "mode": flow_mode,
                "content_delta": "",
                "is_final": True,
                "payload": {
                    "trip_data": trip_data,
                    "response_text": response_text,
                    "metrics": metrics,
                    "source_evidence": source_evidence,
                    "knowledge_debug": {
                        "knowledge_scope": str(payload.knowledge_scope or "private_plus_public"),
                        "allow_public_fusion": allow_public_fusion,
                        "kb_context_count": len(kb_context_texts),
                        "source_evidence_count": len(source_evidence),
                    },
                    "agent_escalated": metrics["agent_escalated"],
                    "escalation_reasons": escalation_reasons,
                },
            },
        )
    except Exception as exc:
        last_sequence += 1
        await _append_flow_event(
            message_id,
            {
                "event": "error",
                "sequence": last_sequence,
                "message_id": message_id,
                "session_id": session_id,
                "step": "finalize",
                "status": "failed",
                "mode": flow_mode,
                "content_delta": "",
                "is_final": True,
                "payload": {"error": str(exc)},
            },
        )
        _record_flow_metrics(
            {
                "message_id": message_id,
                "session_id": session_id,
                "user_id": payload.user_id,
                "device_id": payload.device_id,
                "mode": flow_mode,
                "intent": str(metrics.get("intent") or ""),
                "status": "failed",
                "latency_ms": int((time.perf_counter() - started_at) * 1000),
                "tool_count": metrics.get("tool_count") or 0,
                "rag_hit": bool(metrics.get("rag_hit")),
                "agent_escalated": bool(metrics.get("agent_escalated")),
                "context_count": metrics.get("context_count") or 0,
                "context_chars": metrics.get("context_chars") or 0,
                "context_budget": metrics.get("context_budget") or {},
                "escalation_reasons": escalation_reasons,
                "error": str(exc),
            }
        )


@app.get("/api/health")
def health_check() -> Dict[str, str]:
    """健康检查接口"""
    return {"status": "ok"}


@app.post("/api/sessions/start", response_model=StartSessionResponse)
def start_session(payload: StartSessionRequest) -> StartSessionResponse:
    """创建新会话并返回会话ID"""
    storage = _get_storage()
    session_id = storage.generate_session_id(payload.user_id, payload.device_id)
    storage.store_session(payload.user_id, session_id)
    return StartSessionResponse(session_id=session_id)


@app.get("/api/sessions/list", response_model=List[SessionItem])
def list_sessions(user_id: str = Query(..., description="用户ID")) -> List[SessionItem]:
    """获取指定用户的会话列表"""
    storage = _get_storage()
    rows = storage.get_session_list(user_id)
    return [SessionItem(**_row_to_session_item(row)) for row in rows]


@app.get("/api/sessions/history", response_model=List[ChatHistoryItem])
def session_history(session_id: str = Query(..., description="会话ID")) -> List[ChatHistoryItem]:
    storage = _get_storage()
    try:
        history_messages = storage.get_session_chat_list(session_id)
        if history_messages:
            parsed_messages = []
            for message_json in history_messages:
                try:
                    message_obj = Message.model_validate_json(message_json)
                    parsed_messages.append(ChatHistoryItem(**_normalize_message_payload(message_obj)))
                except Exception:
                    continue
            return parsed_messages
        short_term_context = storage.get_short_term_context(session_id)
        if isinstance(short_term_context, dict):
            messages = short_term_context.get("messages") or []
            return [ChatHistoryItem(**item) for item in messages if isinstance(item, dict)]
        return []
    except Exception:
        return []


@app.get("/api/sessions/trip", response_model=TripDataResponse)
def session_trip(session_id: str = Query(..., description="会话ID")) -> TripDataResponse:
    storage = _get_storage()
    trip_data = storage.get_trip_data(session_id)
    return TripDataResponse(session_id=session_id, trip_data=trip_data)


@app.delete("/api/sessions/delete", response_model=DeleteSessionResponse)
def delete_session(session_id: str = Query(..., description="会话ID")) -> DeleteSessionResponse:
    storage = _get_storage()
    storage.delete_session(session_id)
    return DeleteSessionResponse(session_id=session_id, success=True)


@app.post("/api/chat/send", response_model=ChatSendResponse)
def send_chat(payload: ChatSendRequest) -> ChatSendResponse:
    if not payload.message:
        raise HTTPException(status_code=400, detail="message 不能为空")
    session_id = _ensure_session_id(payload.user_id, payload.device_id, payload.session_id)
    storage = _get_storage()
    llm_manager = _get_llm_manager()
    conversation_manager = _get_conversation_manager()
    context_messages = _get_context_messages(storage, session_id)
    current_trip = storage.get_trip_data(session_id)
    intent_data = llm_manager.analyze_user_message(payload.message, context_messages, current_trip)
    user_message = Message(
        role=MessageType.USER,
        content=payload.message,
        timestamp=datetime.now(),
        metadata={},
    )
    conversation_manager.process_new_message(
        payload.user_id,
        payload.device_id,
        user_message,
        session_id,
        intent_data=intent_data,
    )
    intent = intent_data.get("intent")
    response_text = ""
    trip_data = None
    needs_more_info = False
    if intent == "general_conversation":
        tool_call = llm_manager.call_tool_by_llm(payload.message, context_messages)
        if tool_call.get("needs_tool") and tool_call.get("result"):
            result_payload = tool_call.get("result")
            if isinstance(result_payload, dict) and result_payload.get("success"):
                response_text = f"工具结果：{result_payload.get('data')}"
                print(f"目前用户的问题只需要调用工具返回即可，目前获取工具结果，准备返回数据: {response_text}")
        if not response_text:
            response_stream = llm_manager.stream_chat_response(payload.message, context_messages, current_trip)
            response_text = "".join([str(delta) for delta in response_stream])
            print(f"目前用户的问题 => 对话模式，工具调用无结果或者不需要工具调用，准备返回数据: {response_text}")
    elif intent == "generate_trip":
        result = llm_manager._handle_trip_generation(intent_data, context_messages)
        response_text = result.get("response") or ""
        trip_data = result.get("trip_data")
        needs_more_info = bool(result.get("needs_more_info"))
    elif intent in ["modify_trip", "add_attraction", "delete_attraction", "reorder_trip"]:
        if current_trip:
            result = llm_manager._handle_trip_modification(intent_data, current_trip, context_messages)
            response_text = result.get("response") or ""
            trip_data = result.get("trip_data")
        else:
            response_text = "我需要先为您生成一个基础行程，然后才能进行调整。请先提供目的地、天数和预算信息。"
    else:
        response_text = f"我理解您想{intent_data.get('summary', '进一步讨论行程')}. 请告诉我更多细节，比如目的地、旅行天数和您的偏好，我可以为您规划具体的行程。"
    if trip_data:
        storage.store_trip_data(session_id, trip_data)
    # 去除思考链内容
    cleaned_response = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL).strip()
    assistant_message = Message(
        role=MessageType.ASSISTANT,
        content=cleaned_response,
        timestamp=datetime.now(),
        metadata={
            "intent": intent,
            "needs_more_info": needs_more_info,
            "has_trip_data": bool(trip_data),
        },
    )
    conversation_manager.process_new_message(
        payload.user_id,
        payload.device_id,
        assistant_message,
        session_id,
    )
    return ChatSendResponse(
        session_id=session_id,
        response=response_text,
        trip_data=trip_data,
        intent=intent,
        needs_more_info=needs_more_info,
    )


@app.post("/api/flow/stream")
async def stream_main_flow(
    payload: FlowStreamRequest,
    request: Request,
    message_id: Optional[str] = Query(None, description="流式消息ID"),
    last_sequence: Optional[int] = Query(None, description="断线续传序号"),
):
    """单主流程唯一流式入口：启动或续传并输出统一 SSE 事件序列。"""
    if not payload.destination or payload.days <= 0:
        raise HTTPException(status_code=400, detail="destination 和 days 为必填且 days 必须大于 0")
    session_id = _ensure_session_id(payload.user_id, payload.device_id, payload.session_id)
    llm_manager = _get_llm_manager()
    await _cleanup_flow_streams()
    async with _flow_streams_lock:
        stream_id = message_id or f"flow-{datetime.now().strftime('%H%M%S%f')}"
        stream_state = _flow_streams.get(stream_id)
        if not stream_state:
            stream_state = {
                "message_id": stream_id,
                "session_id": session_id,
                "events": [],
                "done": False,
                "running": False,
                "pause_requested": False,
                "last_status": "running",
                "last_error": "",
                "retry_count": 0,
                "parent_message_id": "",
                "last_payload": payload.model_dump(),
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            _flow_streams[stream_id] = stream_state
        else:
            session_id = stream_state.get("session_id") or session_id
            stream_state["last_payload"] = payload.model_dump()
        if not stream_state.get("running") and not stream_state.get("done"):
            stream_state["running"] = True
            stream_state["pause_requested"] = False
            asyncio.create_task(
                _run_flow_stream(
                    stream_id,
                    session_id,
                    llm_manager,
                    payload,
                )
            )
    header_sequence = request.headers.get("Last-Event-ID")
    try:
        header_sequence_value = int(header_sequence) if header_sequence else None
    except Exception:
        header_sequence_value = None
    start_sequence = header_sequence_value if header_sequence_value is not None else last_sequence

    async def event_generator():
        current_sequence = int(start_sequence or 0)
        while True:
            if await request.is_disconnected():
                break
            async with _flow_streams_lock:
                events = [
                    event
                    for event in stream_state.get("events", [])
                    if int(event.get("sequence") or 0) > current_sequence
                ]
                done = bool(stream_state.get("done"))
            if events:
                for event in events:
                    current_sequence = int(event.get("sequence") or current_sequence)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if bool(event.get("is_final")):
                        return
            else:
                if done:
                    return
                await asyncio.sleep(0.2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/flow/status", response_model=FlowStatusResponse)
async def get_flow_status(
    message_id: str = Query(..., description="流式消息ID"),
) -> FlowStatusResponse:
    """查询主流程当前状态，供前端展示与恢复控制判断。"""
    stream_state = await _get_flow_state(message_id)
    if not stream_state:
        raise HTTPException(status_code=404, detail="未找到对应主流程消息")
    events = stream_state.get("events") if isinstance(stream_state.get("events"), list) else []
    latest_sequence = 0
    if events:
        latest_sequence = _to_int(events[-1].get("sequence"), 0) if isinstance(events[-1], dict) else 0
    status_name = str(stream_state.get("last_status") or "running")
    if bool(stream_state.get("pause_requested")) and not bool(stream_state.get("done")):
        status_name = "paused"
    return FlowStatusResponse(
        message_id=str(stream_state.get("message_id") or message_id),
        session_id=str(stream_state.get("session_id") or ""),
        running=bool(stream_state.get("running")),
        done=bool(stream_state.get("done")),
        paused=bool(stream_state.get("pause_requested")),
        status=status_name,
        retry_count=_to_int(stream_state.get("retry_count"), 0),
        has_error=bool(str(stream_state.get("last_error") or "").strip()),
        last_error=str(stream_state.get("last_error") or "") or None,
        latest_sequence=latest_sequence,
        event_count=len(events),
        created_at=float(stream_state.get("created_at") or 0.0),
        updated_at=float(stream_state.get("updated_at") or 0.0),
    )


@app.post("/api/flow/control", response_model=FlowControlResponse)
async def control_flow(payload: FlowControlRequest) -> FlowControlResponse:
    """控制主流程执行，支持 pause/resume/retry 三类动作。"""
    action = str(payload.action or "").strip().lower()
    if action not in {"pause", "resume", "retry"}:
        raise HTTPException(status_code=400, detail="action 仅支持 pause/resume/retry")
    await _cleanup_flow_streams()
    async with _flow_streams_lock:
        stream_state = _flow_streams.get(payload.message_id)
        if not stream_state:
            raise HTTPException(status_code=404, detail="未找到对应主流程消息")
        if action == "pause":
            if bool(stream_state.get("done")):
                return FlowControlResponse(
                    message_id=payload.message_id,
                    action=action,
                    accepted=False,
                    status=str(stream_state.get("last_status") or "done"),
                    detail="流程已结束，无法暂停",
                )
            stream_state["pause_requested"] = True
            stream_state["updated_at"] = time.time()
            return FlowControlResponse(
                message_id=payload.message_id,
                action=action,
                accepted=True,
                status="paused",
                detail="已标记暂停，执行线程将在检查点暂停",
            )
        if action == "resume":
            if bool(stream_state.get("done")):
                return FlowControlResponse(
                    message_id=payload.message_id,
                    action=action,
                    accepted=False,
                    status=str(stream_state.get("last_status") or "done"),
                    detail="流程已结束，无法恢复",
                )
            stream_state["pause_requested"] = False
            stream_state["updated_at"] = time.time()
            return FlowControlResponse(
                message_id=payload.message_id,
                action=action,
                accepted=True,
                status="running",
                detail="已恢复执行",
            )
        if bool(stream_state.get("running")):
            return FlowControlResponse(
                message_id=payload.message_id,
                action=action,
                accepted=False,
                status="running",
                detail="流程运行中，暂不支持并发重试，请先暂停或等待结束",
            )
        last_payload = stream_state.get("last_payload")
        if not isinstance(last_payload, dict):
            return FlowControlResponse(
                message_id=payload.message_id,
                action=action,
                accepted=False,
                status=str(stream_state.get("last_status") or "failed"),
                detail="缺少重试请求参数，无法重试",
            )
        retry_message_id = f"{payload.message_id}-retry-{datetime.now().strftime('%H%M%S%f')}"
        stream_state["retry_count"] = _to_int(stream_state.get("retry_count"), 0) + 1
        retry_payload = FlowStreamRequest(**last_payload)
        retry_state = {
            "message_id": retry_message_id,
            "session_id": str(stream_state.get("session_id") or ""),
            "events": [],
            "done": False,
            "running": True,
            "pause_requested": False,
            "last_status": "running",
            "last_error": "",
            "retry_count": _to_int(stream_state.get("retry_count"), 0),
            "parent_message_id": payload.message_id,
            "last_payload": retry_payload.model_dump(),
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        _flow_streams[retry_message_id] = retry_state
        llm_manager = _get_llm_manager()
        session_id = str(stream_state.get("session_id") or retry_payload.session_id or "")
        asyncio.create_task(
            _run_flow_stream(
                retry_message_id,
                session_id,
                llm_manager,
                retry_payload,
            )
        )
        return FlowControlResponse(
            message_id=payload.message_id,
            action=action,
            accepted=True,
            status="running",
            next_message_id=retry_message_id,
            detail="已创建重试任务，请使用 next_message_id 继续拉流",
        )


@app.get("/api/flow/metrics", response_model=FlowMetricsListResponse)
def list_flow_metrics(
    start_time: Optional[str] = Query(None, description="起始时间（ISO8601）"),
    end_time: Optional[str] = Query(None, description="结束时间（ISO8601）"),
    mode: Optional[str] = Query(None, description="执行模式 fast/deep"),
    intent: Optional[str] = Query(None, description="意图筛选"),
    status: Optional[str] = Query(None, description="状态筛选 done/failed"),
    user_id: Optional[str] = Query(None, description="用户ID"),
    device_id: Optional[str] = Query(None, description="设备ID"),
    session_id: Optional[str] = Query(None, description="会话ID"),
    agent_escalated: Optional[bool] = Query(None, description="是否升级Agent"),
    rag_hit: Optional[bool] = Query(None, description="是否命中RAG"),
    limit: int = Query(50, ge=1, le=500, description="分页条数"),
    offset: int = Query(0, ge=0, description="分页偏移"),
) -> FlowMetricsListResponse:
    """查询主流程指标明细，支持按模式、意图、状态与时间范围过滤。"""
    total, items = _query_flow_metrics_rows(
        start_time=start_time,
        end_time=end_time,
        mode=mode,
        intent=intent,
        status=status,
        user_id=user_id,
        device_id=device_id,
        session_id=session_id,
        agent_escalated=agent_escalated,
        rag_hit=rag_hit,
        limit=limit,
        offset=offset,
    )
    return FlowMetricsListResponse(total=total, items=[FlowMetricItem(**item) for item in items])


@app.get("/api/flow/metrics/summary", response_model=FlowMetricsSummaryResponse)
def summary_flow_metrics(
    start_time: Optional[str] = Query(None, description="起始时间（ISO8601）"),
    end_time: Optional[str] = Query(None, description="结束时间（ISO8601）"),
    mode: Optional[str] = Query(None, description="执行模式 fast/deep"),
    intent: Optional[str] = Query(None, description="意图筛选"),
    status: Optional[str] = Query(None, description="状态筛选 done/failed"),
    user_id: Optional[str] = Query(None, description="用户ID"),
    device_id: Optional[str] = Query(None, description="设备ID"),
    session_id: Optional[str] = Query(None, description="会话ID"),
    agent_escalated: Optional[bool] = Query(None, description="是否升级Agent"),
    rag_hit: Optional[bool] = Query(None, description="是否命中RAG"),
) -> FlowMetricsSummaryResponse:
    """查询主流程指标聚合摘要，输出成功率、时延分位与命中比例。"""
    summary = _query_flow_metrics_summary(
        start_time=start_time,
        end_time=end_time,
        mode=mode,
        intent=intent,
        status=status,
        user_id=user_id,
        device_id=device_id,
        session_id=session_id,
        agent_escalated=agent_escalated,
        rag_hit=rag_hit,
    )
    return FlowMetricsSummaryResponse(**summary)


@app.get("/api/flow/release_gate", response_model=ReleaseGateResponse)
def flow_release_gate() -> ReleaseGateResponse:
    """输出最终验收清单与发布门槛判定，供版本发布前检查。"""
    metrics_summary = _query_flow_metrics_summary(
        start_time=None,
        end_time=None,
        mode=None,
        intent=None,
        status=None,
        user_id=None,
        device_id=None,
        session_id=None,
        agent_escalated=None,
        rag_hit=None,
    )
    replay_report = _load_latest_replay_report()
    return _build_release_gate_from_data(metrics_summary, replay_report)


@app.post("/api/map/render", response_model=MapRenderResponse)
def render_map(payload: MapRenderRequest) -> MapRenderResponse:
    trip_data = payload.trip_data or {}
    map_renderer = _get_map_renderer()
    batch_index = payload.batch_index or 0
    batch_size = payload.batch_size or 4
    selected_event = None
    last_event = None
    for idx, event in enumerate(map_renderer.render_map_batches(trip_data, batch_size=batch_size)):
        last_event = event
        if idx == batch_index:
            selected_event = event
            break
    if selected_event is None:
        selected_event = last_event or {}
    map_html = selected_event.get("html") or ""
    sequence = selected_event.get("sequence")
    day = selected_event.get("day")
    is_final = bool(selected_event.get("is_final"))
    return MapRenderResponse(
        map_html=map_html,
        sequence=sequence if isinstance(sequence, int) else max(batch_index, 0),
        day=day if isinstance(day, str) or day is None else str(day),
        is_final=is_final,
    )


@app.post("/api/map/geojson", response_model=MapGeoJsonResponse)
def render_map_geojson(payload: MapGeoJsonRequest) -> MapGeoJsonResponse:
    trip_data = payload.trip_data or {}
    map_renderer = _get_map_renderer()
    geo_payload = map_renderer.build_geojson(trip_data)
    return MapGeoJsonResponse(**geo_payload)


@app.post("/api/trip/update", response_model=TripUpdateResponse)
def update_trip(payload: TripUpdateRequest) -> TripUpdateResponse:
    session_id = _ensure_session_id(payload.user_id, payload.device_id, payload.session_id)
    storage = _get_storage()
    storage.store_trip_data(session_id, payload.trip_data)
    return TripUpdateResponse(session_id=session_id, trip_data=payload.trip_data)


@app.post("/api/trip/replan_day", response_model=TripReplanDayResponse)
def replan_trip_day(payload: TripReplanDayRequest) -> TripReplanDayResponse:
    session_id = _ensure_session_id(payload.user_id, payload.device_id, payload.session_id)
    storage = _get_storage()
    current_trip = storage.get_trip_data(session_id)
    if not current_trip:
        raise HTTPException(status_code=404, detail="当前会话未找到行程数据")
    day_value = int(payload.day)
    llm_manager = _get_llm_manager()
    user_input = {
        "destination": current_trip.get("destination", "成都"),
        "days": current_trip.get("days", 3),
        "budget": current_trip.get("budget", ""),
        "preference": current_trip.get("preference", ""),
    }
    edit_cmd = {
        "type": "modify",
        "msg": f"仅重新规划第{day_value}天行程，其余天保持不变",
    }
    replanned_trip = llm_manager.generate_trip(user_input, [], edit_cmd)
    if not replanned_trip:
        raise HTTPException(status_code=500, detail="重新规划失败")
    current_daily_plan = _normalize_daily_plan(current_trip)
    replanned_daily_plan = _normalize_daily_plan(replanned_trip)
    day_key = str(day_value)
    if day_key in replanned_daily_plan:
        current_daily_plan[day_key] = replanned_daily_plan[day_key]
    merged_trip = dict(current_trip)
    merged_trip["daily_plan"] = current_daily_plan
    storage.store_trip_data(session_id, merged_trip)
    return TripReplanDayResponse(session_id=session_id, trip_data=merged_trip)


@app.get("/api/knowledge/bases", response_model=KnowledgeBaseListResponse)
def list_knowledge_bases() -> KnowledgeBaseListResponse:
    records = _load_knowledge_base_registry()
    items = [_build_knowledge_base_item(row) for row in records]
    return KnowledgeBaseListResponse(items=items)


@app.post("/api/knowledge/bases", response_model=KnowledgeBaseCreateResponse)
def create_knowledge_base(payload: KnowledgeBaseCreateRequest) -> KnowledgeBaseCreateResponse:
    normalized_id = _normalize_knowledge_base_id(payload.name)
    collection_name = _to_collection_name(normalized_id)
    store = _get_knowledge_store()
    created_collection_name = store.create_collection(collection_name)
    _upsert_knowledge_base_registry(
        knowledge_base_id=normalized_id,
        name=str(payload.name).strip(),
        collection_name=created_collection_name,
    )
    return KnowledgeBaseCreateResponse(
        knowledge_base_id=normalized_id,
        name=str(payload.name).strip(),
        collection_name=created_collection_name,
    )


@app.delete("/api/knowledge/bases/{knowledge_base_id}", response_model=KnowledgeBaseDeleteResponse)
def delete_knowledge_base(knowledge_base_id: str) -> KnowledgeBaseDeleteResponse:
    normalized_id = _normalize_knowledge_base_id(knowledge_base_id)
    collection_name = _to_collection_name(normalized_id)
    store = _get_knowledge_store()
    existing = set(store.list_collections())
    if collection_name not in existing:
        raise HTTPException(status_code=404, detail="知识库不存在")
    delete_success = store.delete_collection(collection_name)
    if not delete_success:
        raise HTTPException(status_code=500, detail="知识库删除失败")
    _delete_knowledge_base_registry(normalized_id)
    return KnowledgeBaseDeleteResponse(knowledge_base_id=normalized_id, success=True)


@app.post("/api/knowledge/bases/{knowledge_base_id}/upload", response_model=KnowledgeUploadResponse)
async def upload_knowledge_document(knowledge_base_id: str, file: UploadFile = File(...)) -> KnowledgeUploadResponse:
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    normalized_id = kb_info["knowledge_base_id"]
    collection_name = kb_info["collection_name"]
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="文件内容不能为空")
    extracted_text = _extract_text_from_upload(file.filename, file_bytes)
    if not extracted_text:
        raise HTTPException(status_code=400, detail="文档中未提取到可用文本")
    logger.info(
        "knowledge_upload_parse_success kb=%s filename=%s content_chars=%s preview=%s",
        normalized_id,
        str(file.filename),
        len(extracted_text),
        _build_text_preview(extracted_text, 260),
    )
    store = _get_knowledge_store()
    store.switch_collection(collection_name, create_if_missing=False)
    source_type = _detect_source_type_by_filename(file.filename)
    source_metadata = _build_source_metadata(
        knowledge_base_id=normalized_id,
        source_url="",
        source_type=source_type,
        source_platform="unknown",
        ingest_mode="auto",
        ingest_status="parsed",
    )
    parsed_preview_text = _build_text_preview(extracted_text, 3000)
    parsed_chars = len(extracted_text)
    added_chunks = store.add_documents(
        [
            {
                "content": extracted_text,
                "metadata": {
                    "source": str(file.filename),
                    **source_metadata,
                    "parsed_content_preview": parsed_preview_text,
                    "parsed_content_chars": parsed_chars,
                    "file_type": str(file.content_type or ""),
                },
            }
        ]
    )
    if added_chunks <= 0:
        raise HTTPException(status_code=500, detail="文档入库失败")
    logger.info(
        "knowledge_upload_store_success kb=%s filename=%s source_id=%s chunks=%s",
        normalized_id,
        str(file.filename),
        str(source_metadata.get("source_id") or ""),
        added_chunks,
    )
    return KnowledgeUploadResponse(
        knowledge_base_id=normalized_id,
        filename=str(file.filename),
        chunks=added_chunks,
        metadata=source_metadata,
        parsed_content_preview=parsed_preview_text,
        parsed_content_chars=parsed_chars,
    )


@app.post("/api/knowledge/preprocess/url", response_model=KnowledgePreprocessUrlResponse)
def preprocess_knowledge_url(payload: KnowledgePreprocessUrlRequest) -> KnowledgePreprocessUrlResponse:
    url = str(payload.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="url 必须是可公开访问的 http/https 链接")
    # preprocess 只做“预判”，不会真正入库。
    # 它承担的是前端即时反馈：平台识别、短链解跳、风险等级、自动解析成功概率等。
    preprocess_payload = preprocess_url(url, timeout=5)
    resolved_url = str(preprocess_payload.get("resolved_url") or "")
    source_platform = str(preprocess_payload.get("source_platform") or "unknown")
    source_risk_level = str(preprocess_payload.get("source_risk_level") or "low")
    resolve_error_code = str(preprocess_payload.get("resolve_error_code") or "").strip() or None
    auto_parse_preview = _run_url_auto_parse_preview(
        resolved_url=resolved_url or str(preprocess_payload.get("normalized_url") or ""),
        source_platform=source_platform,
        source_risk_level=source_risk_level,
        resolve_error_code=resolve_error_code,
    )
    return KnowledgePreprocessUrlResponse(
        success=True,
        normalized_url=str(preprocess_payload.get("normalized_url") or ""),
        resolved_url=resolved_url,
        source_platform=source_platform,
        source_risk_level=source_risk_level,
        resolve_error_code=resolve_error_code,
        extractor_layer=auto_parse_preview.get("extractor_layer"),
        quality_score=auto_parse_preview.get("quality_score"),
        ingest_error_code=auto_parse_preview.get("ingest_error_code"),
        failure_reason=auto_parse_preview.get("failure_reason"),
        content_lang=auto_parse_preview.get("content_lang"),
        requires_user_assist=bool(auto_parse_preview.get("requires_user_assist")),
        parsed_content_preview=str(auto_parse_preview.get("parsed_content_preview") or ""),
        parsed_content_chars=int(auto_parse_preview.get("parsed_content_chars") or 0),
    )


@app.post("/api/knowledge/bases/{knowledge_base_id}/ingest/url", response_model=KnowledgeIngestUrlResponse)
def ingest_knowledge_url(knowledge_base_id: str, payload: KnowledgeIngestUrlRequest) -> KnowledgeIngestUrlResponse:
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    normalized_id = kb_info["knowledge_base_id"]
    collection_name = kb_info["collection_name"]
    url = str(payload.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="url 必须是可公开访问的 http/https 链接")
    mode = str(payload.mode or "auto").strip().lower()
    if mode not in {"auto", "manual"}:
        raise HTTPException(status_code=400, detail="mode 仅支持 auto/manual")
    manual_text = str(payload.manual_text or "").strip()
    ocr_text = str(payload.ocr_text or "").strip()
    if mode == "manual" and not manual_text and not ocr_text:
        raise HTTPException(status_code=400, detail="manual 模式下需提供 manual_text 或 ocr_text")
    # 先做 URL 规范化与解跳，后续去重、平台判断、落库 metadata 都基于这组标准字段，
    # 避免同一条内容因为短链/追踪参数不同被重复导入。
    preprocess_payload = preprocess_url(url, timeout=5)
    normalized_url = str(preprocess_payload.get("normalized_url") or url)
    resolved_url = str(preprocess_payload.get("resolved_url") or normalized_url)
    platform = str(preprocess_payload.get("source_platform") or _infer_source_platform(resolved_url))
    source_risk_level = str(preprocess_payload.get("source_risk_level") or "low")
    resolve_error_code = str(preprocess_payload.get("resolve_error_code") or "").strip() or None
    store = _get_knowledge_store()
    store.switch_collection(collection_name, create_if_missing=False)
    ingest_status = "failed"
    ingest_error_code = resolve_error_code or "INGEST_EMPTY_CONTENT"
    source_type = "url"
    content_text = ""
    extractor_layer: Optional[str] = None
    quality_score: Optional[int] = None
    source_metadata: Dict[str, Any] = {}
    auto_parse_preview: Dict[str, Any] = {}
    if _exists_source_url(collection_name, resolved_url):
        # 即使是重复来源，也写一条失败来源记录。
        # 这样前端可以给用户明确反馈“这条链接已导入过”，同时保留失败原因与来源追踪。
        source_metadata = _build_source_metadata(
            knowledge_base_id=normalized_id,
            source_url=url,
            source_type=source_type,
            source_platform=platform,
            ingest_mode=mode,
            ingest_status="failed",
            ingest_error_code="AUTO_PARSE_DUPLICATED",
            normalized_url=normalized_url,
            resolved_url=resolved_url,
            source_risk_level=source_risk_level,
            extractor_layer=extractor_layer,
            quality_score=quality_score,
        )
        _upsert_failed_source_entry(source_metadata, parsed_preview_text="", parsed_chars=0)
        return KnowledgeIngestUrlResponse(
            success=False,
            ingest_status="failed",
            chunks_count=0,
            metadata=source_metadata,
            parsed_content_preview="",
            parsed_content_chars=0,
        )
    if mode == "manual":
        # manual 模式本质上是“跳过自动解析，直接走用户提供的正文/OCR 文本入库”，
        # 所以状态记为 fallback，用来和真正自动解析成功的 parsed 区分。
        content_text = manual_text or ocr_text
        source_type = "manual" if manual_text else "ocr"
        ingest_status = "fallback"
        ingest_error_code = None
        extractor_layer = "manual" if manual_text else "ocr"
        manual_quality = validate_content_quality(content_text, {"source_platform": platform})
        quality_score = int(manual_quality.get("quality_score") or 0)
    else:
        # auto 模式先走统一的预解析+质量门禁，再决定 parsed / failed / fallback。
        # 这里的 fallback 表示“自动解析失败，但用户同时补了手动文本，所以仍然允许导入”。
        auto_parse_preview = _run_url_auto_parse_preview(
            resolved_url=resolved_url,
            source_platform=platform,
            source_risk_level=source_risk_level,
            resolve_error_code=resolve_error_code,
        )
        content_text = str(auto_parse_preview.get("content_text") or "")
        extractor_layer = str(auto_parse_preview.get("extractor_layer") or "").strip() or None
        quality_score = auto_parse_preview.get("quality_score")
        if bool(auto_parse_preview.get("is_valid")):
            ingest_status = "parsed"
            ingest_error_code = None
            logger.info(
                "knowledge_ingest_url_auto_parse kb=%s platform=%s url=%s content_chars=%s preview=%s layer=%s quality_score=%s",
                normalized_id,
                platform,
                resolved_url,
                len(content_text),
                _build_text_preview(content_text, 260),
                extractor_layer,
                quality_score,
            )
        else:
            ingest_status = "failed"
            ingest_error_code = str(auto_parse_preview.get("ingest_error_code") or ingest_error_code or "AUTO_PARSE_LOW_QUALITY")
        if not content_text and (manual_text or ocr_text):
            content_text = manual_text or ocr_text
            source_type = "manual" if manual_text else "ocr"
            ingest_status = "fallback"
            ingest_error_code = ingest_error_code or "AUTO_PARSE_EMPTY_FALLBACK"
            extractor_layer = "manual" if manual_text else "ocr"
            manual_quality = validate_content_quality(content_text, {"source_platform": platform})
            quality_score = int(manual_quality.get("quality_score") or 0)
        elif ingest_status == "failed" and (manual_text or ocr_text):
            content_text = manual_text or ocr_text
            source_type = "manual" if manual_text else "ocr"
            ingest_status = "fallback"
            extractor_layer = "manual" if manual_text else "ocr"
            manual_quality = validate_content_quality(content_text, {"source_platform": platform})
            quality_score = int(manual_quality.get("quality_score") or 0)
        elif not content_text:
            ingest_status = "failed"
            ingest_error_code = ingest_error_code or "AUTO_PARSE_EMPTY"
    source_metadata = _build_source_metadata(
        knowledge_base_id=normalized_id,
        source_url=url,
        source_type=source_type,
        source_platform=platform,
        ingest_mode=mode,
        ingest_status=ingest_status,
        ingest_error_code=ingest_error_code,
        normalized_url=normalized_url,
        resolved_url=resolved_url,
        source_risk_level=source_risk_level,
        extractor_layer=extractor_layer,
        quality_score=quality_score,
    )
    if ingest_status == "failed":
        source_metadata["failure_reason"] = str(
            auto_parse_preview.get("failure_reason") if mode == "auto" else (ingest_error_code or "INGEST_FAILED")
        )
    if ingest_status == "failed":
        logger.warning(
            "knowledge_ingest_url_failed kb=%s platform=%s url=%s error_code=%s",
            normalized_id,
            platform,
            url,
            ingest_error_code,
        )
        failed_preview = _build_text_preview(content_text, 3000) if content_text else ""
        # 失败时不写向量分块，但必须把失败来源写进 registry，
        # 否则用户下一次进入页面会看不到这条失败记录，也就没法原位重试。
        _upsert_failed_source_entry(source_metadata, parsed_preview_text=failed_preview, parsed_chars=len(content_text))
        return KnowledgeIngestUrlResponse(
            success=False,
            ingest_status="failed",
            chunks_count=0,
            metadata=source_metadata,
            parsed_content_preview=failed_preview,
            parsed_content_chars=len(content_text),
        )
    parsed_preview_text = _build_text_preview(content_text, 3000)
    parsed_chars = len(content_text)
    logger.info(
        "knowledge_ingest_url_content_ready kb=%s source_type=%s ingest_status=%s content_chars=%s preview=%s",
        normalized_id,
        source_type,
        ingest_status,
        len(content_text),
        _build_text_preview(content_text, 260),
    )
    # 当前仍按“单条长文本交给向量库内部切分”的方式入库。
    # source_id / ingest_status / parsed_preview 等 metadata 会复制到所有分块上，供后续聚合与调试使用。
    added_chunks = store.add_documents(
        [
            {
                "content": content_text,
                "metadata": {
                    "source": url,
                    **source_metadata,
                    "parsed_content_preview": parsed_preview_text,
                    "parsed_content_chars": parsed_chars,
                },
            }
        ]
    )
    if added_chunks <= 0:
        failed_metadata = dict(source_metadata)
        failed_metadata["ingest_status"] = "failed"
        failed_metadata["ingest_error_code"] = "VECTOR_STORE_INSERT_FAILED"
        _upsert_failed_source_entry(failed_metadata, parsed_preview_text=parsed_preview_text, parsed_chars=parsed_chars)
        logger.error(
            "knowledge_ingest_url_store_failed kb=%s source_id=%s url=%s",
            normalized_id,
            str(source_metadata.get("source_id") or ""),
            url,
        )
        return KnowledgeIngestUrlResponse(
            success=False,
            ingest_status="failed",
            chunks_count=0,
            metadata=failed_metadata,
            parsed_content_preview=parsed_preview_text,
            parsed_content_chars=parsed_chars,
        )
    # 成功入库后要把同 source_id 的失败记录清掉，避免来源列表同时出现“失败记录 + 成功记录”。
    _delete_failed_source_entry(normalized_id, str(source_metadata.get("source_id") or ""))
    logger.info(
        "knowledge_ingest_url_store_success kb=%s source_id=%s ingest_status=%s chunks=%s",
        normalized_id,
        str(source_metadata.get("source_id") or ""),
        ingest_status,
        added_chunks,
    )
    return KnowledgeIngestUrlResponse(
        success=True,
        ingest_status=ingest_status,
        chunks_count=added_chunks,
        metadata=source_metadata,
        parsed_content_preview=parsed_preview_text,
        parsed_content_chars=parsed_chars,
    )


@app.get("/api/knowledge/bases/{knowledge_base_id}/sources", response_model=KnowledgeSourceListResponse)
def list_knowledge_sources(knowledge_base_id: str) -> KnowledgeSourceListResponse:
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    normalized_id = kb_info["knowledge_base_id"]
    collection_name = kb_info["collection_name"]
    store = _get_knowledge_store()
    all_collections = set(store.list_collections())
    collection_exists = collection_name in all_collections
    raw_doc_count = 0
    if collection_exists:
        try:
            store.switch_collection(collection_name, create_if_missing=False)
            raw_payload = store.vector_db.get(include=["ids"])
            raw_ids = raw_payload.get("ids") if isinstance(raw_payload, dict) else []
            raw_doc_count = len(raw_ids) if isinstance(raw_ids, list) else 0
        except Exception as exc:
            logger.error(
                "knowledge_sources_raw_count_failed kb=%s collection=%s error=%s",
                normalized_id,
                collection_name,
                str(exc),
            )
    source_entries = _load_collection_source_entries(collection_name, normalized_id)
    social_entries = [
        item
        for item in source_entries
        if str(item.get("source_url") or "").strip()
        or str(item.get("source_type") or "").strip().lower() in {"url", "manual", "ocr"}
    ]
    items = [KnowledgeSourceItem(**{key: value for key, value in item.items() if key != "chunk_ids"}) for item in source_entries]
    status_counter = {"parsed": 0, "fallback": 0, "failed": 0}
    for item in source_entries:
        status_value = str(item.get("ingest_status") or "parsed")
        if status_value in status_counter:
            status_counter[status_value] += 1
    logger.info(
        "knowledge_sources_list kb=%s collection=%s exists=%s raw_docs=%s total=%s social=%s first_source_id=%s first_source_url=%s",
        normalized_id,
        collection_name,
        collection_exists,
        raw_doc_count,
        len(source_entries),
        len(social_entries),
        str(source_entries[0].get("source_id") or "") if source_entries else "",
        str(source_entries[0].get("source_url") or "") if source_entries else "",
    )
    stats = KnowledgeSourceStats(
        total=len(source_entries),
        parsed=status_counter["parsed"],
        fallback=status_counter["fallback"],
        failed=status_counter["failed"],
    )
    return KnowledgeSourceListResponse(knowledge_base_id=normalized_id, items=items, stats=stats)


@app.get("/api/knowledge/debug/snapshot", response_model=KnowledgeDebugSnapshotResponse)
def knowledge_debug_snapshot() -> KnowledgeDebugSnapshotResponse:
    """输出全部知识库与分块调试快照，便于核验导入解析结果。"""
    records = _load_knowledge_base_registry()
    debug_items: List[KnowledgeDebugBaseItem] = []
    for record in records:
        collection_name = str(record.get("collection_name") or "")
        source_entries = _load_collection_debug_entries(collection_name, str(record.get("knowledge_base_id") or "")) if collection_name else []
        last_updated_at = None
        ingest_times = [str(item.get("ingested_at") or "").strip() for item in source_entries]
        ingest_times = [value for value in ingest_times if value]
        if ingest_times:
            last_updated_at = max(ingest_times)
        source_payload = [KnowledgeDebugSourceItem(**item) for item in source_entries]
        document_count = sum([int(item.chunks_count or 0) for item in source_payload])
        debug_items.append(
            KnowledgeDebugBaseItem(
                knowledge_base_id=str(record.get("knowledge_base_id") or ""),
                name=str(record.get("name") or ""),
                collection_name=collection_name,
                document_count=document_count,
                source_count=len(source_payload),
                last_updated_at=last_updated_at,
                sources=source_payload,
            )
        )
    logger.info(
        "knowledge_debug_snapshot_generated bases=%s total_sources=%s",
        len(debug_items),
        sum([len(item.sources) for item in debug_items]),
    )
    return KnowledgeDebugSnapshotResponse(generated_at=datetime.now().isoformat(), items=debug_items)


@app.delete("/api/knowledge/bases/{knowledge_base_id}/sources/{source_id}", response_model=KnowledgeSourceDeleteResponse)
def delete_knowledge_source(knowledge_base_id: str, source_id: str) -> KnowledgeSourceDeleteResponse:
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    normalized_id = kb_info["knowledge_base_id"]
    collection_name = kb_info["collection_name"]
    source_entries = _load_collection_source_entries(collection_name, normalized_id)
    matched_entry = next((item for item in source_entries if str(item.get("source_id") or "") == str(source_id or "")), None)
    if not matched_entry:
        raise HTTPException(status_code=404, detail="来源不存在")
    chunk_ids = [str(item) for item in (matched_entry.get("chunk_ids") or []) if str(item).strip()]
    if chunk_ids:
        store = _get_knowledge_store()
        store.switch_collection(collection_name, create_if_missing=False)
        store.vector_db.delete(ids=chunk_ids)
    _delete_failed_source_entry(normalized_id, str(source_id))
    return KnowledgeSourceDeleteResponse(
        knowledge_base_id=normalized_id,
        source_id=str(source_id),
        success=True,
        deleted_chunks=len(chunk_ids),
    )


@app.patch("/api/knowledge/bases/{knowledge_base_id}/sources/{source_id}", response_model=KnowledgeSourceUpdateResponse)
def update_knowledge_source(knowledge_base_id: str, source_id: str, payload: KnowledgeSourceUpdateRequest) -> KnowledgeSourceUpdateResponse:
    """更新指定来源内容并重建该来源分块，确保修改后内容可被私有检索命中。"""
    kb_info = _resolve_knowledge_base_collection(knowledge_base_id)
    normalized_id = kb_info["knowledge_base_id"]
    collection_name = kb_info["collection_name"]
    normalized_source_id = str(source_id or "").strip()
    if not normalized_source_id:
        raise HTTPException(status_code=400, detail="source_id 不能为空")
    updated_content = str(payload.content or "").strip()
    updated_ocr_text = str(payload.ocr_text or "").strip()
    # 重试入口允许“正文 + OCR/字幕补充”拼接后一起入库，
    # 这样用户可以在不覆盖原手工整理文本的情况下补充识别结果。
    if updated_ocr_text:
        updated_content = "\n".join([item for item in [updated_content, updated_ocr_text] if item]).strip()
    if not updated_content:
        raise HTTPException(status_code=400, detail="content 不能为空")
    source_entries = _load_collection_source_entries(collection_name, normalized_id)
    matched_entry = next((item for item in source_entries if str(item.get("source_id") or "") == normalized_source_id), None)
    if not matched_entry:
        raise HTTPException(status_code=404, detail="来源不存在")
    logger.info(
        "knowledge_source_update_start kb=%s source_id=%s old_chunks=%s old_source_url=%s",
        normalized_id,
        normalized_source_id,
        len(matched_entry.get("chunk_ids") or []),
        str(matched_entry.get("source_url") or ""),
    )
    store = _get_knowledge_store()
    store.switch_collection(collection_name, create_if_missing=False)
    chunk_ids = [str(item) for item in (matched_entry.get("chunk_ids") or []) if str(item).strip()]
    if chunk_ids:
        store.vector_db.delete(ids=chunk_ids)
    # 复用原来源标识并替换正文，保证前端列表与检索引用稳定。
    retry_count = int(matched_entry.get("retry_count") or 0) + 1
    last_retry_at = datetime.now().isoformat()
    source_type = str(matched_entry.get("source_type") or "manual")
    if updated_ocr_text and not str(payload.content or "").strip():
        source_type = "ocr"
    quality_payload = validate_content_quality(updated_content, {"source_platform": str(matched_entry.get("source_platform") or "unknown")})
    source_metadata = _build_source_metadata(
        knowledge_base_id=normalized_id,
        source_url=str(payload.source_url or matched_entry.get("source_url") or "").strip(),
        source_type=source_type,
        source_platform=str(matched_entry.get("source_platform") or "unknown"),
        ingest_mode="manual",
        ingest_status="fallback",
        source_id=normalized_source_id,
        author=matched_entry.get("author"),
        expires_at=matched_entry.get("expires_at"),
        normalized_url=matched_entry.get("normalized_url"),
        resolved_url=matched_entry.get("resolved_url"),
        source_risk_level=matched_entry.get("source_risk_level"),
        extractor_layer="ocr" if source_type == "ocr" else "manual",
        quality_score=int(quality_payload.get("quality_score") or 0),
    )
    # retry_count / last_retry_at 只在“原位修复失败来源”时增长，
    # 便于后续从调试快照中看出这条来源经历过多少次人工补救。
    source_metadata["retry_count"] = retry_count
    source_metadata["last_retry_at"] = last_retry_at
    source_metadata["failure_reason"] = None
    parsed_preview_text = _build_text_preview(updated_content, 3000)
    parsed_chars = len(updated_content)
    added_chunks = store.add_documents(
        [
            {
                "content": updated_content,
                "metadata": {
                    "source": str(payload.source_url or matched_entry.get("source_url") or ""),
                    **source_metadata,
                    "parsed_content_preview": parsed_preview_text,
                    "parsed_content_chars": parsed_chars,
                },
            }
        ]
    )
    if added_chunks <= 0:
        raise HTTPException(status_code=500, detail="来源更新失败")
    _delete_failed_source_entry(normalized_id, normalized_source_id)
    logger.info(
        "knowledge_source_update_done kb=%s source_id=%s new_chunks=%s content_chars=%s",
        normalized_id,
        normalized_source_id,
        added_chunks,
        parsed_chars,
    )
    return KnowledgeSourceUpdateResponse(
        knowledge_base_id=normalized_id,
        source_id=normalized_source_id,
        success=True,
        chunks_count=added_chunks,
        metadata=source_metadata,
        parsed_content_preview=parsed_preview_text,
        parsed_content_chars=parsed_chars,
    )


@app.post("/api/knowledge/search", response_model=KnowledgeSearchResponse)
def knowledge_search(payload: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
    """知识库检索接口"""
    query = str(payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    scope = _normalize_knowledge_scope(payload.knowledge_scope)
    knowledge_base_id = str(payload.knowledge_base_id or "").strip()
    if scope == "private_only" and not knowledge_base_id:
        raise HTTPException(status_code=400, detail="private_only 模式下必须选择 knowledge_base_id")
    allow_public_fusion = scope != "private_only"
    source_evidence: List[Dict[str, Any]] = []
    rag_pipeline = _get_rag_pipeline()
    private_docs = _search_private_knowledge_docs(knowledge_base_id, query, k=14) if knowledge_base_id else []
    if private_docs:
        source_evidence = _build_source_evidence_from_docs(private_docs)
    private_evidence = (
        _build_private_knowledge_evidence(knowledge_base_id, query)
        if knowledge_base_id
        else _build_empty_evidence()
    )
    if scope == "private_only":
        evidence = private_evidence
        answer = rag_pipeline.generate_answer_from_evidence(query, evidence) if payload.generate_answer else None
        return KnowledgeSearchResponse(
            query=query,
            evidence=evidence,
            answer=answer,
            source_evidence=source_evidence,
            knowledge_debug={
                "knowledge_scope": scope,
                "allow_public_fusion": allow_public_fusion,
                "kb_context_count": len((evidence.get("body") or {}).get("items") or []),
                "source_evidence_count": len(source_evidence),
            },
        )
    if not knowledge_base_id:
        result = rag_pipeline.run(query, generate_answer=payload.generate_answer)
        evidence = result.get("evidence") or _build_empty_evidence()
        answer = result.get("answer")
        return KnowledgeSearchResponse(
            query=query,
            evidence=evidence,
            answer=answer,
            source_evidence=[],
            knowledge_debug={
                "knowledge_scope": scope,
                "allow_public_fusion": allow_public_fusion,
                "kb_context_count": 0,
                "source_evidence_count": 0,
            },
        )
    public_result = rag_pipeline.run(query, generate_answer=False)
    public_evidence = public_result.get("evidence") or _build_empty_evidence()
    merged_evidence = _merge_knowledge_evidence(private_evidence, public_evidence)
    answer = rag_pipeline.generate_answer_from_evidence(query, merged_evidence) if payload.generate_answer else None
    return KnowledgeSearchResponse(
        query=query,
        evidence=merged_evidence,
        answer=answer,
        source_evidence=source_evidence,
        knowledge_debug={
            "knowledge_scope": scope,
            "allow_public_fusion": allow_public_fusion,
            "kb_context_count": len((private_evidence.get("body") or {}).get("items") or []),
            "source_evidence_count": len(source_evidence),
        },
    )


@app.post("/api/knowledge/answer_from_evidence", response_model=KnowledgeAnswerResponse)
def knowledge_answer_from_evidence(payload: KnowledgeAnswerRequest) -> KnowledgeAnswerResponse:
    rag_pipeline = _get_rag_pipeline()
    if not payload.query or not isinstance(payload.evidence, dict):
        raise HTTPException(status_code=400, detail="证据或问题不能为空")
    answer = rag_pipeline.generate_answer_from_evidence(payload.query, payload.evidence)
    return KnowledgeAnswerResponse(query=payload.query, evidence=payload.evidence, answer=answer)
