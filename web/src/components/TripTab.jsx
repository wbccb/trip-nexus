import { Alert, Button, Card, Divider, Dropdown, Form, Input, Modal, Space, Spin, Tag, Tooltip } from "antd"
import { DndContext, PointerSensor, closestCenter, useSensor, useSensors } from "@dnd-kit/core"
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { normalizeTripDays } from "../utils/tripUtils.js"

function getConstraintStatusColor(status) {
  if (status === "met") {
    return "green"
  }
  if (status === "violated") {
    return "red"
  }
  return "gold"
}

function buildConstraintSummary(constraintsUsed) {
  // TripTab 不重复展示原始枚举值，而是把 constraints_used 压缩成用户可读摘要，
  // 让用户快速确认“这次行程到底按什么约束生成/修改的”。
  const budgetLabelMap = {
    economy: "经济",
    balanced: "均衡",
    comfortable: "舒适",
  }
  const intensityLabelMap = {
    leisure: "休闲",
    standard: "标准",
    extreme: "特种兵",
  }
  const paceLabelMap = {
    cultural: "文化探索",
    efficient: "打卡效率",
    family_friendly: "亲子友好",
  }
  if (!constraintsUsed || typeof constraintsUsed !== "object") {
    return ""
  }
  const special = constraintsUsed.special_constraints || {}
  const parts = [
    `预算：${budgetLabelMap[constraintsUsed.budget_level] || budgetLabelMap.balanced}`,
    `强度：${intensityLabelMap[constraintsUsed.intensity] || intensityLabelMap.standard}`,
    `节奏：${paceLabelMap[constraintsUsed.pace] || paceLabelMap.cultural}`,
  ]
  if (Number.isFinite(Number(special.walking_limit_km)) && Number(special.walking_limit_km) > 0) {
    parts.push(`步行上限：${Number(special.walking_limit_km)}km`)
  }
  if (special.need_nap) {
    parts.push("午休：需要")
  }
  if (special.accessibility) {
    parts.push("无障碍：需要")
  }
  return parts.join(" | ")
}

function getConflictTagColor(severity) {
  return severity === "error" ? "red" : "gold"
}

function getConflictSeverityIcon(severity) {
  return severity === "error" ? "⛔" : "⚠"
}

function SortableTripItem({ item, onEdit, onDelete, onCardClick, isSelected, itemRef }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.id,
  })
  const handleRef = useCallback(
    (node) => {
      setNodeRef(node)
      if (itemRef) {
        itemRef(node)
      }
    },
    [itemRef, setNodeRef],
  )
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
  }
  const ratingValueRaw = item?.rating
  const ratingValue = Number.isFinite(ratingValueRaw) ? ratingValueRaw : Number(ratingValueRaw)
  const tagList = Array.isArray(item?.tags) ? item.tags : item?.tags ? [item.tags] : []
  const tagTokens = []
  if (Number.isFinite(ratingValue)) {
    tagTokens.push(`⭐${ratingValue.toFixed(1)}`)
  }
  tagList.filter(Boolean).forEach((tag) => tagTokens.push(`🏷️${tag}`))
  if (item?.duration) {
    tagTokens.push(`⏱️${item.duration}`)
  }
  const recommendReason = item?.recommend_reason || item?.reason || ""
  const rawSources = item?.sources || item?.source || []
  const sourceList = Array.isArray(rawSources) ? rawSources : [rawSources]
  const normalizedSources = sourceList
    .map((source) => {
      if (!source) {
        return null
      }
      if (typeof source === "string") {
        return { title: source, url: source }
      }
      if (typeof source === "object") {
        return {
          title: source.title || source.name || source.url || source.source || "来源链接",
          url: source.url || source.source || "",
        }
      }
      return null
    })
    .filter(Boolean)
  return (
    <div
      ref={handleRef}
      style={style}
      className={`trip-card${isSelected ? " is-selected" : ""}${isDragging ? " is-dragging" : ""}`}
    >
      <div className="trip-card-left" {...attributes} {...listeners}>
        <div className="trip-card-order">{item.orderLabel}</div>
      </div>
      <div className="trip-card-body" onClick={onCardClick}>
        <div className="trip-card-title">
          <div className="trip-card-name">{item.attraction || "未提供"}</div>
          <Tag color="blue">{item.time || "未提供"}</Tag>
        </div>
        <div className="trip-card-meta">{item.address || "未提供地址"}</div>
        <div className="trip-card-meta">
          {item.transport || "交通未提供"} · {item.duration || "停留时间未提供"}
        </div>
        {tagTokens.length > 0 && (
          <div className="trip-card-tags">
            {tagTokens.map((tag) => (
              <span key={tag}>{tag}</span>
            ))}
          </div>
        )}
        {recommendReason && <div className="trip-card-reason">推荐理由：{recommendReason}</div>}
        {normalizedSources.length > 0 && (
          <div className="trip-card-sources">
            {normalizedSources.map((source, index) => (
              <div key={`${source.url || source.title}-${index}`}>
                来源：
                {source.url ? (
                  <a href={source.url} target="_blank" rel="noreferrer">
                    {source.title}
                  </a>
                ) : (
                  source.title
                )}
              </div>
            ))}
          </div>
        )}
        {item.note && <div className="trip-card-note">{item.note}</div>}
      </div>
      <div className="trip-card-actions">
        <Button size="small" onClick={onEdit}>
          编辑
        </Button>
        <Button size="small" danger onClick={onDelete}>
          删除
        </Button>
      </div>
    </div>
  )
}

export default function TripTab({
  conflictReport,
  lockedDays,
  loadingTrip,
  onApplyAlternative,
  onConflictReportChange,
  onLockedDaysChange,
  onSelectAlternative,
  tripDays,
  tripResult,
  selectedAlternative,
  onTripChange,
  onReplanDay,
  selectedPoiId,
  onSelectPoi,
}) {
  const [draftTrip, setDraftTrip] = useState(tripResult || null)
  const [editingItem, setEditingItem] = useState(null)
  const [replanDialog, setReplanDialog] = useState({ open: false, day: null, timeRange: null })
  const [replanInstruction, setReplanInstruction] = useState("")
  const [replanSubmitting, setReplanSubmitting] = useState(false)
  const [lastAgentEscalation, setLastAgentEscalation] = useState(null)
  const [editForm] = Form.useForm()
  const cardRefs = useRef({})
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }))
  useEffect(() => {
    setDraftTrip(tripResult || null)
  }, [tripResult])
  const alternatives = Array.isArray(conflictReport?.alternatives) ? conflictReport.alternatives : []
  const previewAlternative =
    Number.isInteger(selectedAlternative) && selectedAlternative >= 0 ? alternatives[selectedAlternative] : null
  // 方案卡片切换只影响当前预览，不立即改写正式行程，避免用户误触后直接落库。
  const displayTrip = useMemo(() => {
    if (previewAlternative?.trip_data) {
      return previewAlternative.trip_data
    }
    return draftTrip
  }, [draftTrip, previewAlternative])
  const normalizedDays = useMemo(() => normalizeTripDays(displayTrip), [displayTrip])
  const sortedTripDays = normalizedDays
  const constraintsUsed = displayTrip?.constraints_used || null
  const constraintSummary = useMemo(() => buildConstraintSummary(constraintsUsed), [constraintsUsed])
  const constraintStatuses = Array.isArray(displayTrip?.constraints_satisfied)
    ? displayTrip.constraints_satisfied
    : []
  const activeConflicts = Array.isArray(conflictReport?.conflicts) ? conflictReport.conflicts : []
  const conflictsByDay = useMemo(() => {
    // 按天聚合冲突，供“天标题旁的小红点/tooltip”复用，
    // 避免每次渲染都在 JSX 内重复扫描 conflict 列表。
    const grouped = {}
    activeConflicts.forEach((item) => {
      const key = String(item?.day || "")
      if (!key) {
        return
      }
      if (!grouped[key]) {
        grouped[key] = []
      }
      grouped[key].push(item)
    })
    return grouped
  }, [activeConflicts])
  const handleUpdateTrip = useCallback(
    (nextTrip) => {
      setDraftTrip(nextTrip)
      if (onTripChange) {
        onTripChange(nextTrip)
      }
    },
    [onTripChange],
  )
  const handleDragEnd = useCallback(
    (event) => {
      if (previewAlternative) {
        return
      }
      const { active, over } = event
      if (!active?.id || !over?.id || !draftTrip?.daily_plan) {
        return
      }
      const [activeDay] = String(active.id).split("-")
      const [overDay] = String(over.id).split("-")
      if (activeDay !== overDay) {
        return
      }
      const dayItems = Array.isArray(draftTrip.daily_plan[activeDay])
        ? [...draftTrip.daily_plan[activeDay]]
        : []
      const activeIndex = dayItems.findIndex((_, index) => `${activeDay}-${index}` === active.id)
      const overIndex = dayItems.findIndex((_, index) => `${activeDay}-${index}` === over.id)
      if (activeIndex < 0 || overIndex < 0 || activeIndex === overIndex) {
        return
      }
      const nextItems = arrayMove(dayItems, activeIndex, overIndex)
      const nextTrip = {
        ...draftTrip,
        daily_plan: {
          ...draftTrip.daily_plan,
          [activeDay]: nextItems,
        },
      }
      handleUpdateTrip(nextTrip)
    },
    [draftTrip, handleUpdateTrip, previewAlternative],
  )
  const handleDeleteItem = useCallback(
    (dayKey, index) => {
      if (previewAlternative || !draftTrip?.daily_plan) {
        return
      }
      const dayItems = Array.isArray(draftTrip.daily_plan[dayKey])
        ? [...draftTrip.daily_plan[dayKey]]
        : []
      dayItems.splice(index, 1)
      const nextTrip = {
        ...draftTrip,
        daily_plan: {
          ...draftTrip.daily_plan,
          [dayKey]: dayItems,
        },
      }
      handleUpdateTrip(nextTrip)
    },
    [draftTrip, handleUpdateTrip, previewAlternative],
  )
  const handleOpenEdit = useCallback(
    (dayKey, index, item) => {
      if (previewAlternative) {
        return
      }
      setEditingItem({ dayKey, index })
      editForm.setFieldsValue({
        attraction: item?.attraction || "",
        time: item?.time || "",
        address: item?.address || "",
        transport: item?.transport || "",
        duration: item?.duration || "",
        note: item?.note || item?.description || item?.introduction || "",
      })
    },
    [editForm, previewAlternative],
  )
  const setCardRef = useCallback((poiId, node) => {
    if (!poiId) {
      return
    }
    if (node) {
      cardRefs.current[poiId] = node
    } else if (cardRefs.current[poiId]) {
      delete cardRefs.current[poiId]
    }
  }, [])
  const handleSelectPoi = useCallback(
    (poiId) => {
      if (onSelectPoi) {
        onSelectPoi(poiId)
      }
    },
    [onSelectPoi],
  )
  const handleCardClick = useCallback(
    (poiId) => {
      handleSelectPoi(poiId)
    },
    [handleSelectPoi],
  )
  const handleEditOk = useCallback(async () => {
    if (previewAlternative) {
      setEditingItem(null)
      return
    }
    if (!editingItem || !draftTrip?.daily_plan) {
      setEditingItem(null)
      return
    }
    const values = await editForm.validateFields()
    const dayItems = Array.isArray(draftTrip.daily_plan[editingItem.dayKey])
      ? [...draftTrip.daily_plan[editingItem.dayKey]]
      : []
    if (!dayItems[editingItem.index]) {
      setEditingItem(null)
      return
    }
    dayItems[editingItem.index] = {
      ...dayItems[editingItem.index],
      attraction: values.attraction,
      time: values.time,
      address: values.address,
      transport: values.transport,
      duration: values.duration,
      note: values.note,
    }
    const nextTrip = {
      ...draftTrip,
      daily_plan: {
        ...draftTrip.daily_plan,
        [editingItem.dayKey]: dayItems,
      },
    }
    handleUpdateTrip(nextTrip)
    setEditingItem(null)
  }, [draftTrip, editForm, editingItem, handleUpdateTrip, previewAlternative])
  const handleEditCancel = useCallback(() => {
    setEditingItem(null)
  }, [])
  const lockedDaySet = useMemo(() => {
    if (lockedDays instanceof Set) {
      return lockedDays
    }
    return new Set(Array.isArray(lockedDays) ? lockedDays : [])
  }, [lockedDays])
  const toggleLockedDay = useCallback(
    (dayKey) => {
      if (!onLockedDaysChange || previewAlternative) {
        return
      }
      const dayNumber = Number(dayKey)
      const nextLockedDays = new Set(Array.from(lockedDaySet))
      if (nextLockedDays.has(dayNumber)) {
        nextLockedDays.delete(dayNumber)
      } else {
        nextLockedDays.add(dayNumber)
      }
      onLockedDaysChange(nextLockedDays)
    },
    [lockedDaySet, onLockedDaysChange, previewAlternative],
  )
  const openReplanDialog = useCallback(
    (dayKey, timeRange = null) => {
      if (previewAlternative) {
        return
      }
      setReplanInstruction("")
      setReplanDialog({
        open: true,
        day: Number(dayKey),
        timeRange,
      })
    },
    [previewAlternative],
  )
  const handleReplanCancel = useCallback(() => {
    setReplanDialog({ open: false, day: null, timeRange: null })
    setReplanInstruction("")
  }, [])
  const handleReplanConfirm = useCallback(async () => {
    if (!onReplanDay || !replanDialog?.day) {
      return
    }
    setReplanSubmitting(true)
    try {
      const response = await onReplanDay({
        day: replanDialog.day,
        scope: {
          day: replanDialog.day,
          time_range: replanDialog.timeRange,
        },
        lockedDays: Array.from(lockedDaySet),
        replanInstruction,
      })
      if (response?.trip_data) {
        setDraftTrip(response.trip_data)
      }
      setLastAgentEscalation(response?.agent_escalation || null)
      handleReplanCancel()
    } finally {
      setReplanSubmitting(false)
    }
  }, [handleReplanCancel, lockedDaySet, onReplanDay, replanDialog, replanInstruction])
  const handleApplyAlternative = useCallback(async () => {
    if (!previewAlternative?.trip_data || !onApplyAlternative) {
      return
    }
    const nextTrip = {
      ...previewAlternative.trip_data,
      conflict_report: {
        has_conflicts: false,
        conflicts: [],
        alternatives: [],
      },
    }
    if (onConflictReportChange) {
      onConflictReportChange({ has_conflicts: false, conflicts: [], alternatives: [] })
    }
    await onApplyAlternative(nextTrip)
  }, [onApplyAlternative, onConflictReportChange, previewAlternative])
  useEffect(() => {
    if (!selectedPoiId) {
      return
    }
    const node = cardRefs.current[selectedPoiId]
    if (node?.scrollIntoView) {
      node.scrollIntoView({ behavior: "smooth", block: "center" })
    }
  }, [selectedPoiId])

  return (
    <div className="trip-layout">
      <Card title="行程详情" className="panel-card">
        <Spin spinning={loadingTrip}>
          {!tripResult && <div className="empty-tip">暂无行程结果</div>}
          {displayTrip && (
            <Space orientation="vertical" size="middle" className="full-width">
              <div className="trip-summary">
                目的地：{displayTrip.destination} · 天数：{displayTrip.days}
              </div>
              {conflictReport?.has_conflicts && (
                <Space direction="vertical" size="small" className="full-width">
                  <Alert
                    type="warning"
                    showIcon
                    message={`检测到 ${activeConflicts.length} 处行程冲突`}
                    description={
                      <Space direction="vertical" size="small" className="full-width">
                        {activeConflicts.map((item, index) => (
                          <div key={`${item?.type || "conflict"}-${item?.day || 0}-${index}`}>
                            {getConflictSeverityIcon(item?.severity)} 第 {item?.day} 天 · {item?.description}
                          </div>
                        ))}
                      </Space>
                    }
                  />
                  {alternatives.length > 0 && (
                    <Space size="middle" align="start" wrap>
                      {alternatives.map((item, index) => (
                        <Card
                          key={`${item?.label || "plan"}-${index}`}
                          size="small"
                          title={item?.label || `Plan ${index + 1}`}
                          style={{
                            width: 280,
                            borderColor: selectedAlternative === index ? "#1677ff" : undefined,
                          }}
                          extra={selectedAlternative === index ? <Tag color="blue">当前预览</Tag> : null}
                        >
                          <Space direction="vertical" size="small" className="full-width">
                            <div>{item?.strategy || "已生成替代策略"}</div>
                            <Space>
                              <Button size="small" onClick={() => onSelectAlternative && onSelectAlternative(index)}>
                                查看方案
                              </Button>
                              <Button
                                size="small"
                                type="primary"
                                onClick={handleApplyAlternative}
                                disabled={selectedAlternative !== index}
                              >
                                采用此方案
                              </Button>
                            </Space>
                          </Space>
                        </Card>
                      ))}
                    </Space>
                  )}
                </Space>
              )}
              {(constraintSummary || constraintStatuses.length > 0) && (
                <Card size="small" title="约束满足状态">
                  <Space orientation="vertical" size="small" className="full-width">
                    {constraintSummary && <div className="trip-summary">{constraintSummary}</div>}
                    {constraintStatuses.length > 0 && (
                      <Space size="small" wrap>
                        {constraintStatuses.map((item, index) => (
                          <Tag key={`${item?.label || "constraint"}-${index}`} color={getConstraintStatusColor(item?.status)}>
                            {item?.status === "met" ? "✓" : item?.status === "violated" ? "⚠" : "~"} {item?.label || "约束项"}
                            {item?.detail ? `：${item.detail}` : ""}
                          </Tag>
                        ))}
                      </Space>
                    )}
                  </Space>
                </Card>
              )}
              {lastAgentEscalation?.escalated && (
                <Alert
                  type="info"
                  showIcon
                  message="本次局部重排触发了相邻天联动"
                  description={lastAgentEscalation?.message || "系统已按最小改动原则联动微调相邻天。"}
                />
              )}
              <Divider />
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                <div className="trip-day-list">
                  {sortedTripDays.map((day) => {
                    const dayKey = String(day.day)
                    const items = Array.isArray(day.items) ? day.items : []
                    const sortableIds = items.map((_, index) => `${dayKey}-${index}`)
                    const isLocked = lockedDaySet.has(Number(dayKey))
                    const replanMenuItems = [
                      { key: "full_day", label: "重排整天" },
                      { key: "morning", label: "重排上午" },
                      { key: "afternoon", label: "重排下午" },
                      { key: "evening", label: "重排晚间" },
                    ]
                    return (
                      <div key={dayKey} className="trip-day-block" style={isLocked ? { opacity: 0.78 } : undefined}>
                        <div className="trip-day-header">
                          <div className="trip-day-title">
                            第 {dayKey} 天
                            {isLocked && (
                              <Tag color="default" style={{ marginLeft: 8 }}>
                                锁定保护
                              </Tag>
                            )}
                            {Array.isArray(conflictsByDay[dayKey]) && conflictsByDay[dayKey].length > 0 && (
                              <Tooltip
                                title={conflictsByDay[dayKey]
                                  .map((item) => `${getConflictSeverityIcon(item?.severity)} ${item?.description}`)
                                  .join("\n")}
                              >
                                <Tag color={getConflictTagColor(conflictsByDay[dayKey][0]?.severity)} style={{ marginLeft: 8 }}>
                                  {getConflictSeverityIcon(conflictsByDay[dayKey][0]?.severity)} {conflictsByDay[dayKey].length} 个冲突
                                </Tag>
                              </Tooltip>
                            )}
                          </div>
                          <div className="trip-day-actions">
                            <Button size="small" onClick={() => toggleLockedDay(dayKey)} disabled={Boolean(previewAlternative)}>
                              {isLocked ? "解锁" : "锁定"}
                            </Button>
                            <Dropdown
                              menu={{
                                items: replanMenuItems,
                                onClick: ({ key }) => openReplanDialog(dayKey, key === "full_day" ? null : key),
                              }}
                              disabled={Boolean(previewAlternative) || isLocked}
                              trigger={["click"]}
                            >
                              <Button size="small">重规划当日</Button>
                            </Dropdown>
                          </div>
                        </div>
                        <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
                          <div className="trip-day-cards">
                            {items.length === 0 && <div className="empty-tip">暂无行程安排</div>}
                            {items.map((item, index) => {
                              const itemId = `${dayKey}-${index}`
                              return (
                                <SortableTripItem
                                  key={itemId}
                                  item={{
                                    ...item,
                                    id: itemId,
                                    orderLabel: `${index + 1}`,
                                    note: item.note || item.description || item.introduction || "",
                                  }}
                                  itemRef={(node) => setCardRef(itemId, node)}
                                  isSelected={selectedPoiId === itemId}
                                  onCardClick={() => handleCardClick(itemId)}
                                  onEdit={() => handleOpenEdit(dayKey, index, item)}
                                  onDelete={() => handleDeleteItem(dayKey, index)}
                                />
                              )
                            })}
                          </div>
                        </SortableContext>
                      </div>
                    )
                  })}
                </div>
              </DndContext>
            </Space>
          )}
        </Spin>
      </Card>
      <Modal title="编辑行程项" open={Boolean(editingItem)} onOk={handleEditOk} onCancel={handleEditCancel}>
        <Form form={editForm} layout="vertical">
          <Form.Item label="地点名称" name="attraction" rules={[{ required: true, message: "请输入地点名称" }]}>
            <Input />
          </Form.Item>
          <Form.Item label="时间" name="time">
            <Input />
          </Form.Item>
          <Form.Item label="地址" name="address">
            <Input />
          </Form.Item>
          <Form.Item label="交通方式" name="transport">
            <Input />
          </Form.Item>
          <Form.Item label="停留时间" name="duration">
            <Input />
          </Form.Item>
          <Form.Item label="备注" name="note">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={`重排第 ${replanDialog?.day || ""} 天${replanDialog?.timeRange === "morning" ? "上午" : replanDialog?.timeRange === "afternoon" ? "下午" : replanDialog?.timeRange === "evening" ? "晚间" : ""}`}
        open={Boolean(replanDialog?.open)}
        onOk={handleReplanConfirm}
        onCancel={handleReplanCancel}
        okText="开始重排"
        cancelText="取消"
        confirmLoading={replanSubmitting}
      >
        <Space direction="vertical" size="middle" className="full-width">
          <Alert
            type="info"
            showIcon
            message="局部重排会尽量保持未选中的时段和未锁定天不变"
            description={`当前锁定天：${Array.from(lockedDaySet).sort((a, b) => a - b).join("、") || "无"}`}
          />
          <Input.TextArea
            rows={4}
            value={replanInstruction}
            onChange={(event) => setReplanInstruction(event.target.value)}
            placeholder="可选补充要求，例如：下午改成室内活动，减少步行。"
          />
        </Space>
      </Modal>
    </div>
  )
}
