import { useCallback, useEffect, useRef, useState } from "react"
import { Form, message } from "antd"
import { buildAgentStreamUrl, runAgent } from "../api/index.js"
import {
  AGENT_SEQUENCE_STORAGE_KEY,
  AGENT_THREAD_STORAGE_KEY,
  DEFAULT_DEVICE_ID,
  DEFAULT_USER_ID,
} from "../constants/appConfig.js"

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

export function useAgent({ onApplyPatch }) {
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
        if (onApplyPatch) {
          onApplyPatch(detail?.patch || [])
        }
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
  }, [onApplyPatch])

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

  return {
    agentForm,
    agentState,
    agentConnecting,
    handleRunAgent,
    handleReconnectAgent,
  }
}
