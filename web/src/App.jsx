import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Button,
  Card,
  Checkbox,
  Drawer,
  Divider,
  Form,
  Input,
  InputNumber,
  Layout,
  Modal,
  Space,
  Spin,
  Tabs,
  Table,
  Tag,
  Typography,
  message,
} from "antd"
import { DeckGL } from "@deck.gl/react"
import { ScatterplotLayer, LineLayer, TextLayer } from "@deck.gl/layers"
import { WebMercatorViewport } from "@deck.gl/core"
import Map from "react-map-gl/maplibre"
import Supercluster from "supercluster"
import KnowledgeTab from "./components/KnowledgeTab.jsx"
import SessionSider from "./components/SessionSider.jsx"
import TripTab from "./components/TripTab.jsx"
import {
  buildAgentStreamUrl,
  getSessionHistory,
  getSessionTrip,
  renderTripGeojson,
  runAgent,
  sendChatMessage,
} from "./api/index.js"
import {
  AGENT_SEQUENCE_STORAGE_KEY,
  AGENT_THREAD_STORAGE_KEY,
  DEFAULT_DEVICE_ID,
  DEFAULT_USER_ID,
  SESSION_STORAGE_KEY,
} from "./constants/appConfig.js"
import { useKnowledge } from "./hooks/useKnowledge.js"
import { useSessions } from "./hooks/useSessions.js"
import { useTrip } from "./hooks/useTrip.js"

const { Header, Content } = Layout
const createAgentNodes = () => ({
  planner: { status: "idle", tasks: [] },
  checker: { status: "idle", tasks: [] },
  optimizer: { status: "idle", tasks: [] },
  map_rag: { status: "idle", tasks: [] },
  executor: { status: "idle", tasks: [] },
})
const resolveAgentNode = (taskType) => {
  if (taskType === "tool_call") {
    return "checker"
  }
  if (taskType === "trip_generate") {
    return "optimizer"
  }
  if (taskType === "map_render" || taskType === "trip_summarize") {
    return "map_rag"
  }
  return taskType || "executor"
}
const resolveStatusColor = (status) => {
  if (status === "running" || status === "planned") {
    return "processing"
  }
  if (status === "done") {
    return "success"
  }
  if (status === "failed") {
    return "error"
  }
  if (status === "paused") {
    return "warning"
  }
  return "default"
}
const resolveRouteColor = (colorName) => {
  if (colorName === "green") {
    return [76, 175, 80]
  }
  if (colorName === "red") {
    return [244, 67, 54]
  }
  if (colorName === "purple") {
    return [156, 39, 176]
  }
  if (colorName === "orange") {
    return [255, 152, 0]
  }
  if (colorName === "darkblue") {
    return [25, 118, 210]
  }
  return [33, 150, 243]
}

export default function App() {
  const {
    activeSessionId,
    deleteSessionById,
    loadSessions,
    loadingSessions,
    selectSession,
    sessions,
    setActiveSessionId,
    startNewSession,
  } = useSessions()
  const {
    handleTripSubmit,
    handleReplanDay,
    loadingTrip,
    persistTripResult,
    tripDays,
    tripResult,
    updateTripResult,
  } = useTrip({
    activeSessionId,
    refreshSessions: loadSessions,
    setActiveSessionId,
  })
  const {
    handleKnowledgeSearch,
    knowledgeQuery,
    knowledgeResult,
    loadingKnowledge,
    setKnowledgeQuery,
  } = useKnowledge()
  const [chatMessages, setChatMessages] = useState([])
  const [chatInput, setChatInput] = useState("")
  const [loadingChatHistory, setLoadingChatHistory] = useState(false)
  const [sendingChat, setSendingChat] = useState(false)
  const [isSessionDrawerOpen, setIsSessionDrawerOpen] = useState(false)
  const [isTripModalOpen, setIsTripModalOpen] = useState(false)
  const [mapHtml, setMapHtml] = useState("")
  const [mapGeojson, setMapGeojson] = useState(null)
  const [loadingMap, setLoadingMap] = useState(false)
  const [mapError, setMapError] = useState("")
  const [selectedPoiId, setSelectedPoiId] = useState("")
  const [mapViewState, setMapViewState] = useState({
    longitude: 104.065,
    latitude: 30.657,
    zoom: 11,
    bearing: 0,
    pitch: 0,
  })
  const [isMapFullscreen, setIsMapFullscreen] = useState(false)
  const mapRequestTokenRef = useRef(0)
  const [tripForm] = Form.useForm()
  const [agentForm] = Form.useForm()
  const [agentState, setAgentState] = useState(() => ({
    threadId: "",
    status: "idle",
    nodes: createAgentNodes(),
    tasks: {},
    queue: [],
    retries: {},
    events: [],
    lastSequence: 0,
  }))
  const [agentConnecting, setAgentConnecting] = useState(false)
  const agentEventSourceRef = useRef(null)
  const agentReconnectTimerRef = useRef(null)
  const agentLastSequenceRef = useRef(0)
  const agentThreadIdRef = useRef("")
  const agentStatusRef = useRef("idle")
  const sessionTitle = useMemo(() => {
    const activeSession = sessions.find((item) => item.session_id === activeSessionId)
    return "当前选择的会话：" + (activeSession?.name || activeSessionId || "未选择会话")
  }, [activeSessionId, sessions])
  const promptTemplate = useMemo(
    () =>
      "请根据以下信息生成行程：目的地 {destination}，天数 {days} 天，预算 {budget}，偏好 {preference}。请给出每天安排、交通方式、停留时长与地址。",
    []
  )
  const mapPoints = useMemo(() => {
    if (!mapGeojson?.points?.features) {
      return []
    }
    return mapGeojson.points.features
  }, [mapGeojson])
  const mapRoutes = useMemo(() => {
    if (!mapGeojson?.routes?.features) {
      return []
    }
    return mapGeojson.routes.features
  }, [mapGeojson])
  useEffect(() => {
    if (!selectedPoiId) {
      return
    }
    const exists = mapPoints.some((feature) => feature?.properties?.poi_id === selectedPoiId)
    if (!exists) {
      setSelectedPoiId("")
    }
  }, [mapPoints, selectedPoiId])
  const clusterIndex = useMemo(() => {
    const index = new Supercluster({ radius: 50, maxZoom: 16 })
    index.load(mapPoints)
    return index
  }, [mapPoints])
  const clusters = useMemo(() => {
    if (!clusterIndex || !mapViewState) {
      return []
    }
    const viewport = new WebMercatorViewport({
      width: 800,
      height: 600,
      longitude: mapViewState.longitude,
      latitude: mapViewState.latitude,
      zoom: mapViewState.zoom,
      bearing: mapViewState.bearing,
      pitch: mapViewState.pitch,
    })
    const bounds = viewport.getBounds()
    return clusterIndex.getClusters(
      [bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1]],
      Math.round(mapViewState.zoom),
    )
  }, [clusterIndex, mapViewState])
  const clusterCounts = useMemo(
    () => clusters.filter((item) => item?.properties?.cluster),
    [clusters],
  )
  const routeSegments = useMemo(() => {
    const segments = []
    mapRoutes.forEach((feature) => {
      const coords = feature?.geometry?.coordinates
      if (Array.isArray(coords) && coords.length >= 2) {
        for (let i = 0; i < coords.length - 1; i += 1) {
          segments.push({
            source: coords[i],
            target: coords[i + 1],
            color: resolveRouteColor(feature?.properties?.color),
          })
        }
      }
    })
    return segments
  }, [mapRoutes])
  const handleSelectPoi = useCallback((poiId) => {
    if (!poiId) {
      return
    }
    setSelectedPoiId(String(poiId))
  }, [])
  const mapLayers = useMemo(() => {
    const layers = []
    if (routeSegments.length > 0) {
      layers.push(
        new LineLayer({
          id: "trip-routes",
          data: routeSegments,
          getSourcePosition: (d) => d.source,
          getTargetPosition: (d) => d.target,
          getColor: (d) => d.color,
          getWidth: 3,
          opacity: 0.7,
        }),
      )
    }
    if (clusters.length > 0) {
      layers.push(
        new ScatterplotLayer({
          id: "trip-points",
          data: clusters,
          getPosition: (d) => d.geometry.coordinates,
          getRadius: (d) => {
            if (d.properties.cluster) {
              return 14 + Math.log(d.properties.point_count) * 6
            }
            return d.properties?.poi_id === selectedPoiId ? 14 : 10
          },
          getFillColor: (d) => {
            if (d.properties.cluster) {
              return [30, 136, 229, 200]
            }
            return d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 230] : [76, 175, 80, 210]
          },
          getLineColor: (d) => (d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 255] : [255, 255, 255]),
          getLineWidth: (d) => (d.properties?.poi_id === selectedPoiId ? 2 : 1),
          pickable: true,
          onClick: (info) => {
            if (!info?.object || info.object?.properties?.cluster) {
              return
            }
            handleSelectPoi(info.object?.properties?.poi_id)
          },
        }),
      )
      layers.push(
        new TextLayer({
          id: "trip-cluster-count",
          data: clusterCounts,
          getPosition: (d) => d.geometry.coordinates,
          getText: (d) => String(d.properties.point_count_abbreviated || d.properties.point_count || ""),
          getSize: 12,
          getColor: [255, 255, 255],
          getTextAnchor: "middle",
          getAlignmentBaseline: "center",
        }),
      )
    }
    return layers
  }, [clusterCounts, clusters, handleSelectPoi, routeSegments, selectedPoiId])
  // 根据消息 ID 更新聊天内容
  const updateChatMessageById = useCallback((messageId, nextContent) => {
    // 更新消息列表
    setChatMessages((prev) =>
      // 映射生成新数组
      prev.map((item) =>
        // 匹配指定消息
        item.id === messageId
          ? {
              // 保留原字段
              ...item,
              // 更新内容
              content: nextContent,
            }
          : item
      )
    )
  }, [])
  // 应用 Agent 的上下文补丁
  const applyAgentPatch = useCallback((patchOps) => {
    // 校验 patch 列表
    if (!Array.isArray(patchOps) || patchOps.length === 0) {
      return
    }
    // 逐条应用 patch
    patchOps.forEach((op) => {
      // 校验 patch 结构
      if (!op || typeof op.path !== "string") {
        return
      }
      // 解析路径分段
      const segments = op.path.split("/").filter(Boolean)
      // 仅处理 shared_context 变更
      if (segments[0] !== "shared_context") {
        return
      }
      // 读取 task id
      const taskId = segments[1]
      // 读取输出 key
      const key = segments.slice(2).join("/")
      // 行程草案更新
      if (taskId === "t4" && key === "draft_trip" && op.value) {
        updateTripResult(op.value)
        return
      }
      // 地图 HTML 更新
      if (taskId === "t5" && key === "map_payload" && op.value?.map_html) {
        setMapHtml(op.value.map_html)
        setMapError("")
        setLoadingMap(false)
      }
    })
  }, [updateTripResult])
  const loadSequenceMap = useCallback(() => {
    try {
      return JSON.parse(localStorage.getItem(AGENT_SEQUENCE_STORAGE_KEY) || "{}")
    } catch (error) {
      return {}
    }
  }, [])
  const persistSequence = useCallback((threadId, sequence) => {
    if (!threadId) {
      return
    }
    const sequenceMap = loadSequenceMap()
    sequenceMap[threadId] = sequence
    localStorage.setItem(AGENT_SEQUENCE_STORAGE_KEY, JSON.stringify(sequenceMap))
    localStorage.setItem(AGENT_THREAD_STORAGE_KEY, threadId)
  }, [loadSequenceMap])
  const closeAgentStream = useCallback(() => {
    if (agentReconnectTimerRef.current) {
      clearTimeout(agentReconnectTimerRef.current)
      agentReconnectTimerRef.current = null
    }
    if (agentEventSourceRef.current) {
      agentEventSourceRef.current.close()
      agentEventSourceRef.current = null
    }
    setAgentConnecting(false)
  }, [])
  const handleAgentEvent = useCallback((payload) => {
    const eventType = payload?.event || ""
    const nextSequence = Number.isFinite(payload?.sequence) ? payload.sequence : 0
    setAgentState((prev) => {
      const nextTasks = { ...(prev.tasks || {}) }
      const nextNodes = { ...(prev.nodes || {}) }
      const nextRetries = { ...(prev.retries || {}) }
      const nextQueue = Array.isArray(prev.queue) ? [...prev.queue] : []
      const nextEvents = Array.isArray(prev.events) ? [...prev.events] : []
      const detail = payload?.payload || {}
      if (eventType === "context_patch") {
        applyAgentPatch(detail?.patch || [])
      }
      if (eventType === "plan_created") {
        Object.keys(nextNodes).forEach((key) => {
          nextNodes[key] = { ...nextNodes[key], status: "planned", tasks: [] }
        })
        Object.keys(nextTasks).forEach((key) => {
          delete nextTasks[key]
        })
        const planTasks = Array.isArray(detail?.tasks) ? detail.tasks : []
        planTasks.forEach((task) => {
          const node = resolveAgentNode(task?.task_type)
          const taskId = task?.task_id || ""
          if (!taskId) {
            return
          }
          nextTasks[taskId] = {
            task_id: taskId,
            task_type: task?.task_type,
            tool: task?.tool,
            description: task?.description,
            node,
            status: "planned",
          }
          if (nextNodes[node]) {
            nextNodes[node] = {
              ...nextNodes[node],
              tasks: [...(nextNodes[node]?.tasks || []), taskId],
            }
          }
        })
      }
      if (eventType === "batch_start") {
        const batchTasks = Array.isArray(detail?.tasks) ? detail.tasks : []
        nextQueue.splice(0, nextQueue.length, ...batchTasks)
      }
      if (eventType === "task_start") {
        const taskId = detail?.task_id
        const task = nextTasks[taskId]
        if (task) {
          nextTasks[taskId] = { ...task, status: "running" }
        }
        const node = payload?.node
        if (node && nextNodes[node]) {
          nextNodes[node] = { ...nextNodes[node], status: "running" }
        }
      }
      if (eventType === "task_end") {
        const taskId = detail?.task_id
        const success = detail?.success !== false
        const task = nextTasks[taskId]
        if (task) {
          nextTasks[taskId] = { ...task, status: success ? "done" : "failed" }
        }
        const node = payload?.node
        if (node && nextNodes[node]) {
          const nodeTasks = nextNodes[node]?.tasks || []
          const nodeFailed = nodeTasks.some((id) => nextTasks[id]?.status === "failed")
          const nodeRunning = nodeTasks.some((id) => nextTasks[id]?.status === "running")
          const nodeDone = nodeTasks.length > 0 && nodeTasks.every((id) => nextTasks[id]?.status === "done")
          const nodeStatus = nodeFailed ? "failed" : nodeRunning ? "running" : nodeDone ? "done" : "planned"
          nextNodes[node] = { ...nextNodes[node], status: nodeStatus }
        }
      }
      if (eventType === "task_retry") {
        const taskId = detail?.task_id
        const retryCount = Number.isFinite(detail?.retry_count) ? detail.retry_count : 0
        if (taskId) {
          nextRetries[taskId] = retryCount
        }
      }
      if (eventType === "replan") {
        Object.keys(nextNodes).forEach((key) => {
          nextNodes[key] = { ...nextNodes[key], status: "planned", tasks: [] }
        })
        Object.keys(nextTasks).forEach((key) => {
          delete nextTasks[key]
        })
        nextQueue.splice(0, nextQueue.length)
      }
      nextEvents.push({
        id: `${payload?.sequence || Date.now()}-${eventType}`,
        ...payload,
      })
      const trimmedEvents = nextEvents.slice(-200)
      const nextStatus = payload?.status || prev.status
      return {
        ...prev,
        status: nextStatus,
        nodes: nextNodes,
        tasks: nextTasks,
        queue: nextQueue,
        retries: nextRetries,
        events: trimmedEvents,
        lastSequence: nextSequence || prev.lastSequence,
      }
    })
  }, [applyAgentPatch])
  const connectAgentStream = useCallback((threadId) => {
    if (!threadId) {
      return
    }
    closeAgentStream()
    const streamUrl = buildAgentStreamUrl(threadId, agentLastSequenceRef.current)
    const eventSource = new EventSource(streamUrl)
    agentEventSourceRef.current = eventSource
    setAgentConnecting(true)
    eventSource.onopen = () => {
      setAgentConnecting(false)
    }
    eventSource.onmessage = (event) => {
      if (!event?.data) {
        return
      }
      let payload = null
      try {
        payload = JSON.parse(event.data)
      } catch (error) {
        return
      }
      if (!payload) {
        return
      }
      const sequence = Number.isFinite(payload?.sequence) ? payload.sequence : 0
      agentLastSequenceRef.current = sequence
      persistSequence(threadId, sequence)
      handleAgentEvent(payload)
      if (payload?.event === "loop_end") {
        closeAgentStream()
      }
    }
    eventSource.onerror = () => {
      if (agentStatusRef.current === "done" || agentStatusRef.current === "failed") {
        closeAgentStream()
        return
      }
      closeAgentStream()
      setAgentConnecting(true)
      agentReconnectTimerRef.current = setTimeout(() => {
        connectAgentStream(threadId)
      }, 2000)
    }
  }, [buildAgentStreamUrl, closeAgentStream, handleAgentEvent, persistSequence])
  const handleRunAgent = useCallback(async (resume) => {
    try {
      const values = await agentForm.validateFields()
      const userInput = {
        destination: values?.destination,
        days: values?.days,
        budget: values?.budget,
        preference: values?.preference,
      }
      const agentConfig = {
        poi_query: values?.poi_query,
        poi_top_k: values?.poi_top_k,
        weather_days: values?.weather_days,
        manual_rag_review: values?.manual_rag_review || false,
      }
      const payload = {
        user_id: DEFAULT_USER_ID,
        device_id: DEFAULT_DEVICE_ID,
        thread_id: resume ? agentState.threadId : undefined,
        user_intent: "generate_trip",
        user_input: userInput,
        agent_config: agentConfig,
        resume: !!resume,
      }
      const data = await runAgent(payload)
      const nextThreadId = data?.thread_id
      if (!nextThreadId) {
        message.error("Agent 启动失败，缺少 thread_id")
        return
      }
      agentThreadIdRef.current = nextThreadId
      const nextSequence = resume ? (agentState.lastSequence || 0) : 0
      agentLastSequenceRef.current = nextSequence
      persistSequence(nextThreadId, nextSequence)
      localStorage.setItem(AGENT_THREAD_STORAGE_KEY, nextThreadId)
      setAgentState((prev) => ({
        ...prev,
        threadId: nextThreadId,
        status: "running",
        nodes: resume ? prev.nodes : createAgentNodes(),
        tasks: resume ? prev.tasks : {},
        queue: resume ? prev.queue : [],
        retries: resume ? prev.retries : {},
        events: resume ? prev.events : [],
        lastSequence: resume ? prev.lastSequence : nextSequence,
      }))
      connectAgentStream(nextThreadId)
    } catch (error) {
      message.error(`Agent 启动失败：${error.message}`)
    }
  }, [agentForm, agentState.lastSequence, agentState.threadId, connectAgentStream, persistSequence])
  const handleReconnectAgent = useCallback(() => {
    if (!agentState.threadId) {
      message.warning("暂无可恢复的 Agent 线程")
      return
    }
    agentThreadIdRef.current = agentState.threadId
    agentLastSequenceRef.current = agentState.lastSequence || 0
    connectAgentStream(agentState.threadId)
  }, [agentState.lastSequence, agentState.threadId, connectAgentStream])
  useEffect(() => {
    agentStatusRef.current = agentState.status
  }, [agentState.status])
  useEffect(() => {
    const savedThreadId = localStorage.getItem(AGENT_THREAD_STORAGE_KEY)
    if (!savedThreadId) {
      return
    }
    const sequenceMap = loadSequenceMap()
    const savedSequence = Number.isFinite(sequenceMap?.[savedThreadId]) ? sequenceMap[savedThreadId] : 0
    agentThreadIdRef.current = savedThreadId
    agentLastSequenceRef.current = savedSequence
    setAgentState((prev) => ({
      ...prev,
      threadId: savedThreadId,
      lastSequence: savedSequence,
    }))
  }, [loadSequenceMap])
  useEffect(() => () => closeAgentStream(), [closeAgentStream])

  const loadChatHistory = useCallback(async (sessionId) => {
    if (!sessionId) {
      setChatMessages([])
      updateTripResult(null)
      return
    }
    try {
      setLoadingChatHistory(true)
      const [historyData, tripData] = await Promise.all([
        getSessionHistory(sessionId),
        getSessionTrip(sessionId),
      ])
      const normalized = Array.isArray(historyData)
        ? historyData.map((item, index) => ({
            id: `${sessionId}-${item.timestamp || Date.now()}-${index}`,
            role: item.role,
            content: item.content,
          }))
        : []
      setChatMessages(normalized)
      if (tripData?.trip_data) {
        updateTripResult(tripData.trip_data)
      } else {
        updateTripResult(null)
      }
    } catch (error) {
      message.error(`聊天记录加载失败：${error.message}`)
      setChatMessages([])
      updateTripResult(null)
    } finally {
      setLoadingChatHistory(false)
    }
  }, [updateTripResult])

  useEffect(() => {
    loadChatHistory(activeSessionId)
  }, [activeSessionId, loadChatHistory])

  const loadMapGeojson = useCallback(async (currentTrip) => {
    if (!currentTrip) {
      mapRequestTokenRef.current += 1
      setMapHtml("")
      setMapGeojson(null)
      setMapError("")
      setLoadingMap(false)
      return
    }
    const token = Date.now()
    mapRequestTokenRef.current = token
    try {
      setLoadingMap(true)
      setMapError("")
      setMapHtml("")
      const data = await renderTripGeojson({ trip_data: currentTrip })
      if (mapRequestTokenRef.current !== token) {
        return
      }
      setMapGeojson(data || null)
      if (data?.bounds && Array.isArray(data.bounds) && data.bounds.length === 4) {
        const viewport = new WebMercatorViewport({
          width: 800,
          height: 600,
        })
        const nextViewState = viewport.fitBounds(
          [
            [data.bounds[0], data.bounds[1]],
            [data.bounds[2], data.bounds[3]],
          ],
          { padding: 40 },
        )
        setMapViewState((prev) => ({
          ...prev,
          longitude: nextViewState.longitude,
          latitude: nextViewState.latitude,
          zoom: nextViewState.zoom,
        }))
      } else if (data?.center) {
        setMapViewState((prev) => ({
          ...prev,
          longitude: data.center.longitude || prev.longitude,
          latitude: data.center.latitude || prev.latitude,
        }))
      }
      setLoadingMap(false)
    } catch (error) {
      setMapHtml("")
      setMapGeojson(null)
      setMapError(`地图加载失败：${error.message}`)
      setLoadingMap(false)
    }
  }, [])

  useEffect(() => {
    loadMapGeojson(tripResult)
    return () => {
      mapRequestTokenRef.current += 1
    }
  }, [tripResult, loadMapGeojson])

  // 聊天消息点击发送
  const handleSendChat = async () => {
    const value = chatInput.trim()
    if (!value || sendingChat) {
      return
    }
    const userMessageId = `${Date.now()}-user`
    setChatMessages((prev) => [
      ...prev,
      {
        id: userMessageId,
        role: "user",
        content: value,
      },
    ])
    setChatInput("")
    try {
      setSendingChat(true)
      const payload = {
        user_id: DEFAULT_USER_ID,
        device_id: DEFAULT_DEVICE_ID,
        session_id: activeSessionId,
        message: value,
      }
      const data = await sendChatMessage(payload)
      if (data?.session_id && data.session_id !== activeSessionId) {
        setActiveSessionId(data.session_id)
        localStorage.setItem(SESSION_STORAGE_KEY, data.session_id)
      }
      // 更新AI返回的消息到聊天框中
      if (data?.response) {
        setChatMessages((prev) => [
          ...prev,
          {
            id: `${Date.now()}-assistant`,
            role: "assistant",
            content: data.response,
          },
        ])
      }
      // 更新中间布局的行程详情
      if (data?.trip_data) {
        updateTripResult(data.trip_data)
      }
      // 重新加载会话列表
      if (loadSessions) {
        await loadSessions()
      }
    } catch (error) {
      message.error(`消息发送失败：${error.message}`)
    } finally {
      setSendingChat(false)
    }
  }

  // 处理行程表单提交
  const handleTripFormSubmit = async (values) => {
    // 生成提示词文本
    const prompt = promptTemplate
      // 替换目的地
      .replace("{destination}", values.destination)
      // 替换天数
      .replace("{days}", values.days)
      // 替换预算
      .replace("{budget}", values.budget || "未填写")
      // 替换偏好
      .replace("{preference}", values.preference || "未填写")
    // 构造用户消息 ID
    const userMessageId = `${Date.now()}-user-trip`
    // 构造助手消息 ID
    const assistantMessageId = `${Date.now()}-assistant-trip`
    // 写入用户消息
    setChatMessages((prev) => [
      // 保留历史消息
      ...prev,
      {
        // 用户消息 ID
        id: userMessageId,
        // 角色标识
        role: "user",
        // 提示词内容
        content: prompt,
      },
      {
        // 助手消息 ID
        id: assistantMessageId,
        // 角色标识
        role: "assistant",
        // 初始内容
        content: "行程生成中...",
      },
    ])
    // 关闭弹窗
    setIsTripModalOpen(false)
    // 重置表单
    tripForm.resetFields()
    // 初始化流式文本
    let streamingText = ""
    // 调用行程生成（流式）
    await handleTripSubmit(values, {
      // 流开始回调
      onStreamStart: () => {
        // 更新为加载提示
        updateChatMessageById(assistantMessageId, "行程生成中...")
      },
      // 流增量回调
      onStreamDelta: (nextText) => {
        // 缓存最新文本
        streamingText = nextText || ""
        // 更新聊天内容
        updateChatMessageById(assistantMessageId, streamingText || "行程生成中...")
      },
      // 流结束回调
      onStreamEnd: () => {
        // 若无流内容则使用默认提示
        if (!streamingText) {
          // 更新聊天内容
          updateChatMessageById(
            assistantMessageId,
            `已生成 ${values.destination} 的 ${values.days} 天游程，请查看中间行程详情。`
          )
        }
      },
      // 行程数据回调
      onTripData: () => {
        // 若流内容为空则提示完成
        if (!streamingText) {
          // 更新聊天内容
          updateChatMessageById(
            assistantMessageId,
            `已生成 ${values.destination} 的 ${values.days} 天游程，请查看中间行程详情。`
          )
        }
      },
    })
  }
  const agentNodeRows = useMemo(() => {
    const nodes = agentState.nodes || {}
    return Object.keys(nodes).map((key) => ({
      key,
      node: key,
      status: nodes[key]?.status || "idle",
      taskCount: (nodes[key]?.tasks || []).length,
    }))
  }, [agentState.nodes])
  const agentTaskRows = useMemo(() => {
    const tasks = agentState.tasks || {}
    return Object.keys(tasks).map((taskId) => ({
      key: taskId,
      task_id: taskId,
      task_type: tasks[taskId]?.task_type,
      tool: tasks[taskId]?.tool,
      node: tasks[taskId]?.node,
      status: tasks[taskId]?.status,
      description: tasks[taskId]?.description,
      retry: agentState.retries?.[taskId] || 0,
    }))
  }, [agentState.retries, agentState.tasks])
  const agentEventRows = useMemo(() => {
    const events = agentState.events || []
    return events.map((event) => ({
      key: event.id,
      sequence: event.sequence,
      event: event.event,
      node: event.node,
      status: event.status,
      payload: event.payload,
    }))
  }, [agentState.events])
  const agentNodeColumns = useMemo(() => [
    {
      title: "节点",
      dataIndex: "node",
      key: "node",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (value) => <Tag color={resolveStatusColor(value)}>{value}</Tag>,
    },
    {
      title: "任务数",
      dataIndex: "taskCount",
      key: "taskCount",
    },
  ], [])
  const agentTaskColumns = useMemo(() => [
    {
      title: "任务 ID",
      dataIndex: "task_id",
      key: "task_id",
    },
    {
      title: "类型",
      dataIndex: "task_type",
      key: "task_type",
    },
    {
      title: "节点",
      dataIndex: "node",
      key: "node",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (value) => <Tag color={resolveStatusColor(value)}>{value}</Tag>,
    },
    {
      title: "重试",
      dataIndex: "retry",
      key: "retry",
    },
  ], [])
  const agentEventColumns = useMemo(() => [
    {
      title: "序号",
      dataIndex: "sequence",
      key: "sequence",
    },
    {
      title: "事件",
      dataIndex: "event",
      key: "event",
    },
    {
      title: "节点",
      dataIndex: "node",
      key: "node",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (value) => <Tag color={resolveStatusColor(value)}>{value}</Tag>,
    },
    {
      title: "Payload",
      dataIndex: "payload",
      key: "payload",
      render: (value) => JSON.stringify(value || {}),
    },
  ], [])

  return (
    <Layout className="app-root">
      <Header className="app-header">
        <div className="header-left">
          <Typography.Title level={4} className="app-title">
            AI 行程助手
          </Typography.Title>
          <Typography.Text className="app-subtitle">
            目的地规划 · 生成行程 · 地图概览
          </Typography.Text>
        </div>
      </Header>
      <Content className="app-content">
        <div className="app-body">
          <div className="app-left">
            <div className="chat-panel">
              <div className="chat-header">
                <div className="chat-title">
                  <div className="chat-title-main">
                    <div>AI 助手</div>
                    <div className="chat-actions">
                  <Button size="small" onClick={() => setIsTripModalOpen(true)}>
                    行程输入
                  </Button>
                  <Button size="small" onClick={() => setIsSessionDrawerOpen(true)}>
                    会话列表
                  </Button>
                </div>
                    </div>
                  <div className="chat-title-sub">{sessionTitle}</div>
                </div>
              </div>
              <div className="chat-history">
                {loadingChatHistory && chatMessages.length === 0 && (
                  <div className="empty-tip">聊天记录加载中...</div>
                )}
                {!loadingChatHistory && chatMessages.length === 0 && (
                  <div className="empty-tip">暂无聊天记录</div>
                )}
                {chatMessages.map((item) => (
                  <div key={item.id} className={`chat-message ${item.role}`}>
                    <div className="chat-bubble">{item.content}</div>
                  </div>
                ))}
              </div>
              <div className="chat-input-floating">
                <Input.TextArea
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={(event) => {
                    // Enter 触发发送
                    if (event.key === "Enter" && !event.shiftKey) {
                      // 阻止默认换行
                      event.preventDefault()
                      // 发送聊天消息
                      handleSendChat()
                    }
                  }}
                  placeholder="输入要咨询的问题或想法"
                  autoSize={{ minRows: 1, maxRows: 3 }}
                />
                <Button type="primary" onClick={handleSendChat} loading={sendingChat}>
                  发送
                </Button>
              </div>
            </div>
          </div>
          <div className="app-main">
            <Tabs
              defaultActiveKey="trip"
              items={[
                {
                  key: "trip",
                  label: "行程详情",
                  children: (
                    <TripTab
                      loadingTrip={loadingTrip}
                      tripDays={tripDays}
                      tripResult={tripResult}
                      selectedPoiId={selectedPoiId}
                      onSelectPoi={handleSelectPoi}
                      onTripChange={async (nextTrip) => {
                        updateTripResult(nextTrip)
                        await persistTripResult(nextTrip)
                      }}
                      onReplanDay={handleReplanDay}
                    />
                  ),
                },
                {
                  key: "knowledge",
                  label: "旅行灵感",
                  children: (
                    <KnowledgeTab
                      knowledgeQuery={knowledgeQuery}
                      knowledgeResult={knowledgeResult}
                      loadingKnowledge={loadingKnowledge}
                      onChangeQuery={setKnowledgeQuery}
                      onSearch={handleKnowledgeSearch}
                    />
                  ),
                },
                {
                  key: "agent",
                  label: "Agent 状态",
                  children: (
                    <Space direction="vertical" size="large" className="full-width">
                      <Card
                        title="Agent 控制"
                        extra={<Tag color={resolveStatusColor(agentState.status)}>{agentState.status}</Tag>}
                      >
                        <Form
                          form={agentForm}
                          layout="vertical"
                          initialValues={{
                            destination: "",
                            days: 2,
                            budget: "",
                            preference: "",
                            poi_query: "热门景点",
                            poi_top_k: 5,
                            weather_days: 3,
                            manual_rag_review: false,
                          }}
                        >
                          <Form.Item
                            label="目的地"
                            name="destination"
                            rules={[{ required: true, message: "请输入目的地" }]}
                          >
                            <Input placeholder="例如：上海" />
                          </Form.Item>
                          <Form.Item
                            label="天数"
                            name="days"
                            rules={[{ required: true, message: "请输入行程天数" }]}
                          >
                            <InputNumber min={1} max={30} className="full-width" />
                          </Form.Item>
                          <Form.Item label="预算" name="budget">
                            <Input placeholder="例如：2000" />
                          </Form.Item>
                          <Form.Item label="偏好" name="preference">
                            <Input placeholder="例如：美食、人文" />
                          </Form.Item>
                          <Divider />
                          <Form.Item label="POI 查询关键词" name="poi_query">
                            <Input placeholder="例如：热门景点" />
                          </Form.Item>
                          <Form.Item label="POI 数量" name="poi_top_k">
                            <InputNumber min={1} max={20} className="full-width" />
                          </Form.Item>
                          <Form.Item label="天气天数" name="weather_days">
                            <InputNumber min={1} max={7} className="full-width" />
                          </Form.Item>
                          <Form.Item name="manual_rag_review" valuePropName="checked">
                            <Checkbox>启用 RAG 人工复核</Checkbox>
                          </Form.Item>
                          <Space>
                            <Button type="primary" onClick={() => handleRunAgent(false)}>
                              启动 Agent
                            </Button>
                            <Button onClick={() => handleRunAgent(true)} disabled={!agentState.threadId}>
                              恢复执行
                            </Button>
                            <Button onClick={handleReconnectAgent} loading={agentConnecting}>
                              重连流
                            </Button>
                          </Space>
                          <Divider />
                          <Space direction="vertical" size="small">
                            <div>线程 ID：{agentState.threadId || "未启动"}</div>
                            <div>最新序号：{agentState.lastSequence || 0}</div>
                          </Space>
                        </Form>
                      </Card>
                      <Card title="执行队列">
                        {agentState.queue.length === 0 && <div className="empty-tip">暂无执行队列</div>}
                        {agentState.queue.length > 0 && (
                          <Space wrap>
                            {agentState.queue.map((taskId) => (
                              <Tag key={taskId}>{taskId}</Tag>
                            ))}
                          </Space>
                        )}
                      </Card>
                      <Card title="节点状态">
                        <Table
                          dataSource={agentNodeRows}
                          columns={agentNodeColumns}
                          pagination={false}
                          size="small"
                        />
                      </Card>
                      <Card title="任务列表">
                        <Table
                          dataSource={agentTaskRows}
                          columns={agentTaskColumns}
                          pagination={{ pageSize: 6 }}
                          size="small"
                        />
                      </Card>
                      <Card title="事件流">
                        <Table
                          dataSource={agentEventRows}
                          columns={agentEventColumns}
                          pagination={{ pageSize: 6 }}
                          size="small"
                        />
                      </Card>
                    </Space>
                  ),
                },
              ]}
            />
          </div>
          <div className="app-right">
            <Card
              title="地图概览"
              className="panel-card map-card"
              extra={
                tripResult ? (
                  <Button size="small" type="text" onClick={() => setIsMapFullscreen(true)}>
                    全屏
                  </Button>
                ) : null
              }
            >
              {!tripResult && (
                <div className="map-placeholder map-large">
                  地图占位（后续可替换为地图 iframe/图片）
                </div>
              )}
              {tripResult && (
                <div className="map-frame">
                  <div className="map-meta">
                    <div className="trip-summary">
                      目的地：{tripResult.destination} · 天数：{tripResult.days}
                    </div>
                    <div className="poi-meta">
                      已规划天数：{tripDays.length} · 行程项：{tripDays.reduce((sum, day) => sum + day.items.length, 0)}
                    </div>
                  </div>
                  <div className="map-view">
                    <Spin spinning={loadingMap} style={{ height: "100%" }}>
                      {mapError && <div className="map-placeholder map-large">{mapError}</div>}
                      {!mapError && mapGeojson && (
                        <div className="map-canvas">
                          <DeckGL
                            layers={mapLayers}
                            viewState={mapViewState}
                            controller
                            onViewStateChange={(event) => setMapViewState(event.viewState)}
                          >
                            <Map
                              reuseMaps
                              mapStyle="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
                            />
                          </DeckGL>
                        </div>
                      )}
                      {!mapError && !mapGeojson && !loadingMap && (
                        <div className="map-placeholder map-large">地图生成失败，请稍后重试</div>
                      )}
                    </Spin>
                  </div>
                </div>
              )}
            </Card>
          </div>
        </div>
      </Content>
      {isMapFullscreen && (
        <div className="map-overlay">
          <div className="map-overlay-toolbar">
            <div className="map-overlay-title">行程地图</div>
            <Button size="small" type="text" onClick={() => setIsMapFullscreen(false)}>
              退出全屏
            </Button>
          </div>
          <div className="map-overlay-body">
            <Spin spinning={loadingMap} style={{ height: "100%" }}>
              {mapError && <div className="map-placeholder map-large">{mapError}</div>}
              {!mapError && mapGeojson && (
                <div className="map-canvas">
                  <DeckGL
                    layers={mapLayers}
                    viewState={mapViewState}
                    controller
                    onViewStateChange={(event) => setMapViewState(event.viewState)}
                  >
                    <Map
                      reuseMaps
                      mapStyle="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
                    />
                  </DeckGL>
                </div>
              )}
              {!mapError && !mapGeojson && !loadingMap && (
                <div className="map-placeholder map-large">地图生成失败，请稍后重试</div>
              )}
            </Spin>
          </div>
        </div>
      )}
      <Drawer
        title="会话列表"
        placement="left"
        width={320}
        open={isSessionDrawerOpen}
        onClose={() => setIsSessionDrawerOpen(false)}
      >
        <SessionSider
          className="session-drawer"
          sessions={sessions}
          activeSessionId={activeSessionId}
          loadingSessions={loadingSessions}
          onCreateSession={startNewSession}
          onDeleteSession={deleteSessionById}
          onSelectSession={(sessionId) => {
            selectSession(sessionId)
            setIsSessionDrawerOpen(false)
          }}
        />
      </Drawer>
      <Modal
        title="行程输入"
        open={isTripModalOpen}
        onCancel={() => setIsTripModalOpen(false)}
        onOk={() => tripForm.submit()}
        okText="生成行程"
        confirmLoading={loadingTrip}
      >
        <Form form={tripForm} layout="vertical" onFinish={handleTripFormSubmit}>
          <Form.Item
            label="目的地"
            name="destination"
            rules={[{ required: true, message: "请输入目的地" }]}
          >
            <Input placeholder="例如：成都" />
          </Form.Item>
          <Form.Item label="天数" name="days" rules={[{ required: true, message: "请输入天数" }]}>
            <InputNumber min={1} className="full-width" placeholder="例如：3" />
          </Form.Item>
          <Form.Item label="预算" name="budget">
            <Input placeholder="例如：3000 元" />
          </Form.Item>
          <Form.Item label="偏好" name="preference">
            <Input placeholder="例如：人文、美食" />
          </Form.Item>
        </Form>
      </Modal>
    </Layout>
  )
}
