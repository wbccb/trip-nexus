import { useCallback, useEffect, useState } from "react";
import { message } from "antd";
import {
  createKnowledgeBase,
  deleteKnowledgeSource,
  deleteKnowledgeBase,
  getKnowledgeDebugSnapshot,
  ingestKnowledgeUrl,
  listKnowledgeBases,
  listKnowledgeSources,
  preprocessKnowledgeUrl,
  searchKnowledge,
  updateKnowledgeSource,
  uploadKnowledgeDocument,
} from "../api/index.js";

const KNOWLEDGE_BASE_STORAGE_KEY = "tripnexus_selected_knowledge_base_id";

export function useKnowledge() {
  const [knowledgeQuery, setKnowledgeQuery] = useState("");
  const [knowledgeResult, setKnowledgeResult] = useState(null);
  const [loadingKnowledge, setLoadingKnowledge] = useState(false);
  const [knowledgeBases, setKnowledgeBases] = useState([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState(() => {
    if (typeof window === "undefined") {
      return "";
    }
    return String(
      window.localStorage.getItem(KNOWLEDGE_BASE_STORAGE_KEY) || "",
    );
  });
  const [knowledgeGenerateQuery, setKnowledgeGenerateQuery] = useState("");
  const [knowledgeScope, setKnowledgeScope] = useState("private_plus_public");
  const [loadingKnowledgeBases, setLoadingKnowledgeBases] = useState(false);
  const [uploadingKnowledge, setUploadingKnowledge] = useState(false);
  const [knowledgeSources, setKnowledgeSources] = useState([]);
  const [sourceStats, setSourceStats] = useState({
    total: 0,
    parsed: 0,
    fallback: 0,
    failed: 0,
  });
  const [loadingKnowledgeSources, setLoadingKnowledgeSources] = useState(false);
  const [ingestingKnowledge, setIngestingKnowledge] = useState(false);
  const [lastIngestResult, setLastIngestResult] = useState(null);
  // preprocess 与真实 ingest 分开维护状态：
  // 前者用于输入 URL 时的即时预判，后者用于点击“导入社交内容”后的正式导入结果。
  const [preprocessingKnowledgeUrl, setPreprocessingKnowledgeUrl] =
    useState(false);
  const [knowledgeUrlPreprocessResult, setKnowledgeUrlPreprocessResult] =
    useState(null);
  const [knowledgeDebugSnapshot, setKnowledgeDebugSnapshot] = useState(null);
  const [loadingKnowledgeDebugSnapshot, setLoadingKnowledgeDebugSnapshot] =
    useState(false);

  const refreshKnowledgeSources = useCallback(
    async (knowledgeBaseId) => {
      const normalizedId = String(
        knowledgeBaseId || selectedKnowledgeBaseId || "",
      ).trim();
      console.info("[knowledge] refreshSources:start", {
        requestedKnowledgeBaseId: String(knowledgeBaseId || ""),
        selectedKnowledgeBaseId: String(selectedKnowledgeBaseId || ""),
        normalizedId,
      });
      if (!normalizedId) {
        setKnowledgeSources([]);
        setSourceStats({ total: 0, parsed: 0, fallback: 0, failed: 0 });
        console.warn("[knowledge] refreshSources:skip-empty-kb");
        return;
      }
      try {
        setLoadingKnowledgeSources(true);
        const data = await listKnowledgeSources(normalizedId);
        const items = Array.isArray(data?.items) ? data.items : [];
        const socialCount = items.filter((item) =>
          Boolean(
            String(item?.source_url || "").trim() ||
            ["url", "manual", "ocr"].includes(
              String(item?.source_type || "").toLowerCase(),
            ),
          ),
        ).length;
        setKnowledgeSources(items);
        setSourceStats(
          data?.stats || {
            total: items.length,
            parsed: 0,
            fallback: 0,
            failed: 0,
          },
        );
        console.info("[knowledge] refreshSources:done", {
          knowledgeBaseId: normalizedId,
          total: items.length,
          socialCount,
          firstSourceId: String(items[0]?.source_id || ""),
        });
      } catch (error) {
        message.error(`来源列表加载失败：${error.message}`);
        setKnowledgeSources([]);
        setSourceStats({ total: 0, parsed: 0, fallback: 0, failed: 0 });
        console.error("[knowledge] refreshSources:error", {
          knowledgeBaseId: normalizedId,
          error: String(error?.message || error),
        });
      } finally {
        setLoadingKnowledgeSources(false);
      }
    },
    [selectedKnowledgeBaseId],
  );

  const refreshKnowledgeBases = useCallback(async () => {
    try {
      setLoadingKnowledgeBases(true);
      const data = await listKnowledgeBases();
      const items = Array.isArray(data?.items) ? data.items : [];
      setKnowledgeBases(items);
      console.info("[knowledge] refreshBases:done", {
        total: items.length,
        selectedKnowledgeBaseId: String(selectedKnowledgeBaseId || ""),
      });
      if (items.length === 0) {
        setSelectedKnowledgeBaseId("");
        return;
      }
      const hasCurrent = items.some(
        (item) => item.knowledge_base_id === selectedKnowledgeBaseId,
      );
      if (!hasCurrent) {
        setSelectedKnowledgeBaseId(items[0].knowledge_base_id);
      }
    } catch (error) {
      message.error(`知识库列表加载失败：${error.message}`);
    } finally {
      setLoadingKnowledgeBases(false);
    }
  }, [selectedKnowledgeBaseId]);

  useEffect(() => {
    refreshKnowledgeBases();
  }, [refreshKnowledgeBases, refreshKnowledgeSources]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    if (!selectedKnowledgeBaseId) {
      window.localStorage.removeItem(KNOWLEDGE_BASE_STORAGE_KEY);
      return;
    }
    // 持久化当前知识库选择，保证页面重载后仍可恢复对应来源列表。
    window.localStorage.setItem(
      KNOWLEDGE_BASE_STORAGE_KEY,
      String(selectedKnowledgeBaseId),
    );
  }, [selectedKnowledgeBaseId]);

  useEffect(() => {
    refreshKnowledgeSources(selectedKnowledgeBaseId);
  }, [refreshKnowledgeSources, selectedKnowledgeBaseId]);

  const handleKnowledgeSearch = useCallback(async () => {
    try {
      if (!knowledgeQuery.trim()) {
        message.warning("请输入检索问题");
        return;
      }
      if (knowledgeScope === "private_only" && !selectedKnowledgeBaseId) {
        message.warning("仅使用私有知识时，请先选择知识库");
        return;
      }
      setLoadingKnowledge(true);
      const data = await searchKnowledge(
        knowledgeQuery.trim(),
        false,
        selectedKnowledgeBaseId || null,
        knowledgeScope || "private_plus_public",
      );
      setKnowledgeResult(data || null);
    } catch (error) {
      message.error(`知识库检索失败：${error.message}`);
    } finally {
      setLoadingKnowledge(false);
    }
  }, [knowledgeQuery, knowledgeScope, selectedKnowledgeBaseId]);

  const handleCreateKnowledgeBase = useCallback(
    async (name) => {
      const trimmedName = String(name || "").trim();
      if (!trimmedName) {
        message.warning("请输入知识库名称");
        return null;
      }
      const result = await createKnowledgeBase(trimmedName);
      await refreshKnowledgeBases();
      if (result?.knowledge_base_id) {
        setSelectedKnowledgeBaseId(result.knowledge_base_id);
      }
      return result || null;
    },
    [refreshKnowledgeBases],
  );

  const handleDeleteKnowledgeBase = useCallback(
    async (knowledgeBaseId) => {
      const normalizedId = String(knowledgeBaseId || "").trim();
      if (!normalizedId) {
        return;
      }
      await deleteKnowledgeBase(normalizedId);
      await refreshKnowledgeBases();
      await refreshKnowledgeSources("");
    },
    [refreshKnowledgeBases],
  );

  const handleUploadKnowledgeDocument = useCallback(
    async (file) => {
      if (!selectedKnowledgeBaseId) {
        message.warning("请先选择知识库");
        return null;
      }
      if (!file) {
        return null;
      }
      try {
        setUploadingKnowledge(true);
        const result = await uploadKnowledgeDocument(
          selectedKnowledgeBaseId,
          file,
        );
        message.success(`上传成功，已入库 ${result?.chunks || 0} 个分块`);
        await refreshKnowledgeBases();
        await refreshKnowledgeSources(selectedKnowledgeBaseId);
        return result || null;
      } catch (error) {
        message.error(`上传失败：${error.message}`);
        return null;
      } finally {
        setUploadingKnowledge(false);
      }
    },
    [refreshKnowledgeBases, refreshKnowledgeSources, selectedKnowledgeBaseId],
  );

  const handleIngestKnowledgeUrl = useCallback(
    async (payload) => {
      if (!selectedKnowledgeBaseId) {
        message.warning("请先选择知识库");
        return null;
      }
      const sourceUrl = String(payload?.url || "").trim();
      if (!sourceUrl) {
        message.warning("请输入来源链接");
        return null;
      }
      try {
        setIngestingKnowledge(true);
        const result = await ingestKnowledgeUrl(
          selectedKnowledgeBaseId,
          payload,
        );
        console.info("[knowledge] ingestUrl:done", {
          knowledgeBaseId: String(selectedKnowledgeBaseId || ""),
          ingestStatus: String(result?.ingest_status || ""),
          chunksCount: Number(result?.chunks_count || 0),
          sourceId: String(result?.metadata?.source_id || ""),
        });
        setLastIngestResult(result || null);
        if (result?.success) {
          const statusText =
            result?.ingest_status === "fallback"
              ? "降级导入成功"
              : "解析导入成功";
          message.success(
            `${statusText}，已入库 ${result?.chunks_count || 0} 个分块`,
          );
        } else {
          message.warning(
            `导入失败：${result?.metadata?.ingest_error_code || "请改用手动模式"}`,
          );
        }
        await refreshKnowledgeBases();
        await refreshKnowledgeSources(selectedKnowledgeBaseId);
        return result || null;
      } catch (error) {
        message.error(`链接导入失败：${error.message}`);
        setLastIngestResult({
          success: false,
          ingest_status: "failed",
          chunks_count: 0,
          metadata: {
            ingest_error_code: "REQUEST_FAILED",
          },
        });
        return null;
      } finally {
        setIngestingKnowledge(false);
      }
    },
    [refreshKnowledgeBases, refreshKnowledgeSources, selectedKnowledgeBaseId],
  );

  const handlePreprocessKnowledgeUrl = useCallback(async (url) => {
    const sourceUrl = String(url || "").trim();
    if (!sourceUrl) {
      setKnowledgeUrlPreprocessResult(null);
      return null;
    }
    try {
      setPreprocessingKnowledgeUrl(true);
      // 这里故意把预处理结果缓存到 hook state，而不是只作为临时返回值使用，
      // 因为 KnowledgeTab 需要持续读取这些信号来驱动 tag、提示文案和默认模式切换。
      const result = await preprocessKnowledgeUrl({ url: sourceUrl });
      setKnowledgeUrlPreprocessResult(result || null);
      return result || null;
    } catch (error) {
      setKnowledgeUrlPreprocessResult(null);
      return null;
    } finally {
      setPreprocessingKnowledgeUrl(false);
    }
  }, []);

  const handleDeleteKnowledgeSource = useCallback(
    async (sourceId) => {
      const normalizedSourceId = String(sourceId || "").trim();
      if (!selectedKnowledgeBaseId || !normalizedSourceId) {
        return null;
      }
      await deleteKnowledgeSource(selectedKnowledgeBaseId, normalizedSourceId);
      console.info("[knowledge] deleteSource:done", {
        knowledgeBaseId: String(selectedKnowledgeBaseId || ""),
        sourceId: normalizedSourceId,
      });
      await refreshKnowledgeBases();
      await refreshKnowledgeSources(selectedKnowledgeBaseId);
      return true;
    },
    [refreshKnowledgeBases, refreshKnowledgeSources, selectedKnowledgeBaseId],
  );

  const handleUpdateKnowledgeSource = useCallback(
    async (sourceId, payload) => {
      const normalizedSourceId = String(sourceId || "").trim();
      const updatedContent = String(payload?.content || "").trim();
      if (!selectedKnowledgeBaseId || !normalizedSourceId) {
        return null;
      }
      if (!updatedContent) {
        message.warning("请输入更新后的正文内容");
        return null;
      }
      // 更新来源正文后立即刷新来源列表，确保重载前后数据一致。
      const result = await updateKnowledgeSource(
        selectedKnowledgeBaseId,
        normalizedSourceId,
        {
          content: updatedContent,
          source_url: String(payload?.source_url || "").trim() || null,
        },
      );
      console.info("[knowledge] updateSource:done", {
        knowledgeBaseId: String(selectedKnowledgeBaseId || ""),
        sourceId: normalizedSourceId,
        chunksCount: Number(result?.chunks_count || 0),
      });
      await refreshKnowledgeBases();
      await refreshKnowledgeSources(selectedKnowledgeBaseId);
      return result || null;
    },
    [refreshKnowledgeBases, refreshKnowledgeSources, selectedKnowledgeBaseId],
  );

  const handleLoadKnowledgeDebugSnapshot = useCallback(async () => {
    try {
      setLoadingKnowledgeDebugSnapshot(true);
      const data = await getKnowledgeDebugSnapshot();
      setKnowledgeDebugSnapshot(data || null);
      return data || null;
    } catch (error) {
      message.error(`调试数据加载失败：${error.message}`);
      return null;
    } finally {
      setLoadingKnowledgeDebugSnapshot(false);
    }
  }, []);

  return {
    handleCreateKnowledgeBase,
    handleDeleteKnowledgeBase,
    handleDeleteKnowledgeSource,
    handleIngestKnowledgeUrl,
    handleKnowledgeSearch,
    handleLoadKnowledgeDebugSnapshot,
    handlePreprocessKnowledgeUrl,
    handleUpdateKnowledgeSource,
    handleUploadKnowledgeDocument,
    ingestingKnowledge,
    knowledgeBases,
    knowledgeDebugSnapshot,
    knowledgeGenerateQuery,
    knowledgeQuery,
    knowledgeResult,
    knowledgeScope,
    knowledgeSources,
    lastIngestResult,
    knowledgeUrlPreprocessResult,
    loadingKnowledge,
    loadingKnowledgeBases,
    loadingKnowledgeSources,
    loadingKnowledgeDebugSnapshot,
    preprocessingKnowledgeUrl,
    refreshKnowledgeBases,
    refreshKnowledgeSources,
    selectedKnowledgeBaseId,
    setKnowledgeGenerateQuery,
    setKnowledgeQuery,
    setKnowledgeScope,
    setSelectedKnowledgeBaseId,
    sourceStats,
    uploadingKnowledge,
  };
}
