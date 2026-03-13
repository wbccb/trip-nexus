import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Layout,
  Modal,
  Spin,
  Tabs,
  Typography,
  message,
} from "antd"
import { DeckGL } from "@deck.gl/react"
import { ScatterplotLayer, LineLayer, TextLayer } from "@deck.gl/layers"
import { WebMercatorViewport } from "@deck.gl/core"
import Map from "react-map-gl/maplibre"
import Supercluster from "supercluster"
import AgentTab from "./components/AgentTab.jsx"
import KnowledgeTab from "./components/KnowledgeTab.jsx"
import SessionSider from "./components/SessionSider.jsx"
import TripTab from "./components/TripTab.jsx"
import {
  getSessionHistory,
  getSessionTrip,
  renderTripGeojson,
  sendChatMessage,
} from "./api/index.js"
import {
  DEFAULT_DEVICE_ID,
  DEFAULT_USER_ID,
  SESSION_STORAGE_KEY,
} from "./constants/appConfig.js"
import { useAgent } from "./hooks/useAgent.js"
import { useKnowledge } from "./hooks/useKnowledge.js"
import { useSessions } from "./hooks/useSessions.js"
import { useTrip } from "./hooks/useTrip.js"

const { Header, Content } = Layout

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
    handleCreateKnowledgeBase,
    handleDeleteKnowledgeBase,
    handleKnowledgeSearch,
    handleUploadKnowledgeDocument,
    knowledgeBases,
    knowledgeGenerateQuery,
    knowledgeQuery,
    knowledgeResult,
    loadingKnowledge,
    loadingKnowledgeBases,
    selectedKnowledgeBaseId,
    setKnowledgeGenerateQuery,
    setKnowledgeQuery,
    setSelectedKnowledgeBaseId,
    uploadingKnowledge,
  } = useKnowledge()
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
    knowledgeGenerateQuery,
    refreshSessions: loadSessions,
    selectedKnowledgeBaseId,
    setActiveSessionId,
  })
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

  const {
    agentState,
    agentForm,
    agentConnecting,
    handleRunAgent,
    handleReconnectAgent,
  } = useAgent({ onApplyPatch: applyAgentPatch })

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
                      knowledgeBases={knowledgeBases}
                      knowledgeGenerateQuery={knowledgeGenerateQuery}
                      knowledgeQuery={knowledgeQuery}
                      knowledgeResult={knowledgeResult}
                      loadingKnowledge={loadingKnowledge}
                      loadingKnowledgeBases={loadingKnowledgeBases}
                      onCreateKnowledgeBase={handleCreateKnowledgeBase}
                      onDeleteKnowledgeBase={handleDeleteKnowledgeBase}
                      onSelectKnowledgeBase={setSelectedKnowledgeBaseId}
                      onUploadKnowledgeDocument={handleUploadKnowledgeDocument}
                      onChangeGenerateQuery={setKnowledgeGenerateQuery}
                      onChangeQuery={setKnowledgeQuery}
                      onSearch={handleKnowledgeSearch}
                      selectedKnowledgeBaseId={selectedKnowledgeBaseId}
                      uploadingKnowledge={uploadingKnowledge}
                    />
                  ),
                },
                {
                  key: "agent",
                  label: "Agent 状态",
                  children: (
                    <AgentTab
                      agentState={agentState}
                      agentForm={agentForm}
                      agentConnecting={agentConnecting}
                      onRunAgent={handleRunAgent}
                      onReconnectAgent={handleReconnectAgent}
                    />
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
