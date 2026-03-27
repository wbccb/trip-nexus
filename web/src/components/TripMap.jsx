import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Button, Spin } from "antd"
import { DeckGL } from "@deck.gl/react"
import { ScatterplotLayer, LineLayer, TextLayer } from "@deck.gl/layers"
import { WebMercatorViewport } from "@deck.gl/core"
import MapView from "react-map-gl/maplibre"
import { renderFlowGeojson } from "../api/index.js"
import { logDebug } from "../utils/debugLogger.js"

const logMap = (msg, data = {}) => {
  logDebug("地图", msg, data)
}

const resolveRouteColor = (colorName) => {
  if (colorName === "green") return [76, 175, 80]
  if (colorName === "red") return [244, 67, 54]
  if (colorName === "purple") return [156, 39, 176]
  if (colorName === "orange") return [255, 152, 0]
  if (colorName === "darkblue") return [25, 118, 210]
  return [33, 150, 243]
}

const BUBBLE_MIN_WIDTH = 140
const BUBBLE_MAX_WIDTH = 260
const BUBBLE_PADDING_X = 16
const BUBBLE_HEIGHT = 64
const COLLISION_PADDING = 8
const SAME_COORD_OFFSET_STEP = 0.035
const SAME_COORD_DIRECTIONS = 8

const estimateBubbleWidth = (label) => {
  const textLength = String(label || "").length
  return Math.min(BUBBLE_MAX_WIDTH, Math.max(BUBBLE_MIN_WIDTH, textLength * 14 + BUBBLE_PADDING_X * 2))
}

const getFeatureCoordinates = (feature) => {
  const coords = feature?.geometry?.coordinates
  if (!Array.isArray(coords) || coords.length < 2) {
    return null
  }
  return coords
}

export default function TripMap({ tripResult, selectedPoiId, onSelectPoi }) {
  const [mapGeojson, setMapGeojson] = useState(null)
  const [loadingMap, setLoadingMap] = useState(false)
  const [mapError, setMapError] = useState("")
  const [mapViewState, setMapViewState] = useState({
    longitude: 104.065,
    latitude: 30.657,
    zoom: 11,
    bearing: 0,
    pitch: 0,
  })
  const [isMapFullscreen, setIsMapFullscreen] = useState(false)
  const mapRequestTokenRef = useRef(0)

  const loadMapGeojson = useCallback(async (currentTrip) => {
    logMap("开始加载地图数据", { hasTrip: Boolean(currentTrip) })
    
    if (!currentTrip) {
      mapRequestTokenRef.current += 1
      setMapGeojson(null)
      setMapError("")
      setLoadingMap(false)
      logMap("没有行程数据，已清空地图")
      return
    }

    const token = Date.now()
    mapRequestTokenRef.current = token
    try {
      setLoadingMap(true)
      setMapError("")
      
      logMap("请求地图 GeoJSON", { destination: currentTrip.destination })
      const data = await renderFlowGeojson({ trip_data: currentTrip })
      
      if (mapRequestTokenRef.current !== token) {
        logMap("忽略过期的地图请求", { token, currentToken: mapRequestTokenRef.current })
        return
      }
      
      logMap("地图 GeoJSON 返回成功", { 
        points: data?.points?.features?.length,
        routes: data?.routes?.features?.length,
        bounds: data?.bounds 
      })

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
        logMap("根据边界调整视角", nextViewState)
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
      logMap("地图数据加载失败", { message: error?.message })
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

  const mapPoints = useMemo(() => {
    return mapGeojson?.points?.features || []
  }, [mapGeojson])

  const mapRoutes = useMemo(() => {
    return mapGeojson?.routes?.features || []
  }, [mapGeojson])

  const adjustedPoints = useMemo(() => {
    const overlapIndex = new Map()
    return mapPoints.map((feature) => {
      const coords = getFeatureCoordinates(feature)
      if (!coords) {
        return feature
      }
      const key = `${coords[0].toFixed(7)}_${coords[1].toFixed(7)}`
      const index = overlapIndex.get(key) || 0
      overlapIndex.set(key, index + 1)
      if (index === 0) {
        return feature
      }
      const ring = Math.floor((index - 1) / SAME_COORD_DIRECTIONS) + 1
      const angle = ((index - 1) % SAME_COORD_DIRECTIONS) * ((Math.PI * 2) / SAME_COORD_DIRECTIONS)
      const offset = SAME_COORD_OFFSET_STEP * ring
      return {
        ...feature,
        geometry: {
          ...feature.geometry,
          coordinates: [coords[0] + Math.cos(angle) * offset, coords[1] + Math.sin(angle) * offset],
        },
      }
    })
  }, [mapPoints])

  useEffect(() => {
    if (!selectedPoiId) return
    
    logMap("选中 POI 发生变化", { selectedPoiId })
    const target = adjustedPoints.find((feature) => feature?.properties?.poi_id === selectedPoiId)
    const coords = getFeatureCoordinates(target)
    
    if (!Array.isArray(coords) || coords.length < 2) return
    
    setMapViewState((prev) => ({
      ...prev,
      longitude: coords[0],
      latitude: coords[1],
      zoom: Math.max(prev.zoom || 0, 22),
      transitionDuration: 1000,
    }))
  }, [adjustedPoints, selectedPoiId])

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

  const getPoiLabel = useCallback((feature) => {
    if (feature?.properties?.is_collision_cluster) {
      return `${feature.properties.point_count || 0} 个地点`
    }
    const order = feature?.properties?.order ? `${feature.properties.order}. ` : ""
    const name = feature?.properties?.attraction || "POI"
    return `${order}${name}`.trim()
  }, [])

  const collisionPoints = useMemo(() => {
    if (!mapViewState || mapPoints.length === 0) return []
    const viewport = new WebMercatorViewport({
      width: 800,
      height: 600,
      longitude: mapViewState.longitude,
      latitude: mapViewState.latitude,
      zoom: mapViewState.zoom,
      bearing: mapViewState.bearing,
      pitch: mapViewState.pitch,
    })
    const sourcePoints = adjustedPoints.filter((feature) => Boolean(getFeatureCoordinates(feature)))
    const projectedPoints = sourcePoints.map((feature) => {
      const coords = getFeatureCoordinates(feature)
      const pixel = viewport.project(coords)
      const label = getPoiLabel(feature)
      const bubbleWidth = estimateBubbleWidth(label)
      return {
        feature,
        coords,
        left: pixel[0] - bubbleWidth / 2,
        right: pixel[0] + bubbleWidth / 2,
        top: pixel[1] - BUBBLE_HEIGHT,
        bottom: pixel[1],
      }
    })
    const groups = []
    projectedPoints.forEach((item) => {
      const matchedGroup = groups.find((group) => {
        return !(
          item.right + COLLISION_PADDING < group.left ||
          item.left - COLLISION_PADDING > group.right ||
          item.bottom + COLLISION_PADDING < group.top ||
          item.top - COLLISION_PADDING > group.bottom
        )
      })
      if (!matchedGroup) {
        groups.push({
          items: [item],
          left: item.left,
          right: item.right,
          top: item.top,
          bottom: item.bottom,
        })
        return
      }
      matchedGroup.items.push(item)
      matchedGroup.left = Math.min(matchedGroup.left, item.left)
      matchedGroup.right = Math.max(matchedGroup.right, item.right)
      matchedGroup.top = Math.min(matchedGroup.top, item.top)
      matchedGroup.bottom = Math.max(matchedGroup.bottom, item.bottom)
    })
    return groups.map((group, index) => {
      if (group.items.length === 1) {
        return group.items[0].feature
      }
      const longitude =
        group.items.reduce((sum, current) => sum + current.coords[0], 0) / group.items.length
      const latitude =
        group.items.reduce((sum, current) => sum + current.coords[1], 0) / group.items.length
      const memberPoiIds = group.items
        .map((item) => item.feature?.properties?.poi_id)
        .filter((poiId) => Boolean(poiId))
      return {
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [longitude, latitude],
        },
        properties: {
          poi_id: `collision_cluster_${index}_${memberPoiIds.join("_")}`,
          is_collision_cluster: true,
          point_count: group.items.length,
          point_count_abbreviated: group.items.length,
          member_poi_ids: memberPoiIds,
          attraction: `聚合 POI (${group.items.length})`,
        },
      }
    })
  }, [adjustedPoints, getPoiLabel, mapViewState])

  const poiLabelCharacterSet = useMemo(() => { // 汇总所有 POI 标签字符，避免 TextLayer 字符集缺失
    const chars = new Set() // 使用 Set 去重字符
    collisionPoints.forEach((feature) => {
      const label = getPoiLabel(feature) // 获取 POI 展示文本
      Array.from(label).forEach((char) => chars.add(char)) // 将每个字符写入字符集
    })
    chars.add("▼")
    return Array.from(chars) // 返回 deck.gl 可识别的字符数组
  }, [collisionPoints, getPoiLabel])

  const mapLayers = useMemo(() => {
    // logMap("开始构建图层", { 
    //   points: mapPoints.length, 
    //   visiblePoints: collisionPoints.length,
    //   routes: routeSegments.length,
    //   zoom: mapViewState.zoom,
    // })

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

    if (collisionPoints.length > 0) {
      const poiLabels = collisionPoints.filter((item) => Boolean(getFeatureCoordinates(item)))

      layers.push(
        new ScatterplotLayer({
          id: "trip-points",
          data: collisionPoints,
          getPosition: (d) => d.geometry.coordinates,
          getRadius: (d) => {
            if (d.properties?.is_collision_cluster) {
              return 14 + Math.log(d.properties.point_count) * 6
            }
            return d.properties?.poi_id === selectedPoiId ? 7 : 5
          },
          getFillColor: (d) => {
            if (d.properties?.is_collision_cluster) {
              return [30, 136, 229, 200]
            }
            return d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 200] : [76, 175, 80, 140]
          },
          getLineColor: (d) => (d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 255] : [255, 255, 255]),
          getLineWidth: (d) => (d.properties?.poi_id === selectedPoiId ? 2 : 1),
          pickable: true,
          onClick: (info) => {
            const clicked = info?.object
            if (!clicked) {
              return
            }
            if (clicked?.properties?.is_collision_cluster) {
              const coords = getFeatureCoordinates(clicked)
              if (!Array.isArray(coords)) return
              setMapViewState((prev) => ({
                ...prev,
                longitude: coords[0],
                latitude: coords[1],
                zoom: Math.min((prev.zoom || 0) + 2, 22),
                transitionDuration: 600,
              }))
              return
            }
            if (onSelectPoi) {
              onSelectPoi(clicked?.properties?.poi_id)
            }
          },
        }),
      )

      layers.push(
        new TextLayer({
          id: "trip-poi-labels",
          data: poiLabels,
          getPosition: (d) => d.geometry.coordinates,
          getText: getPoiLabel,
          getSize: 14,
          sizeUnits: "pixels",
          getColor: (d) => (d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 230] : [33, 33, 33, 230]),
          getTextAnchor: "middle",
          getAlignmentBaseline: "center",
          getPixelOffset: [0, -42],
          characterSet: poiLabelCharacterSet,
          fontFamily: "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif",
          background: true,
          getBackgroundColor: [255, 255, 255, 255],
          getBorderColor: (d) => (d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 180] : [196, 196, 196, 180]),
          getBorderWidth: 1,
          backgroundPadding: [16, 12],
        }),
      )

      layers.push(
        new TextLayer({
          id: "trip-poi-tail",
          data: poiLabels,
          getPosition: (d) => d.geometry.coordinates,
          getText: () => "▼",
          getSize: 16,
          sizeUnits: "pixels",
          getColor: [255, 255, 255, 255],
          getOutlineColor: (d) => (d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 210] : [196, 196, 196, 210]),
          getOutlineWidth: 1,
          getTextAnchor: "middle",
          getAlignmentBaseline: "center",
          getPixelOffset: [0, -22],
          characterSet: ["▼"],
          fontFamily: "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif",
        }),
      )

      layers.push(
        new TextLayer({
          id: "trip-cluster-count",
          data: collisionPoints.filter((item) => item?.properties?.is_collision_cluster),
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
  }, [
    collisionPoints,
    getPoiLabel,
    poiLabelCharacterSet,
    mapPoints.length,
    onSelectPoi,
    routeSegments,
    selectedPoiId,
    mapViewState.zoom,
  ])

  const renderMapContent = () => (
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
            <MapView
              mapStyle="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"
            />
          </DeckGL>
        </div>
      )}
      {!mapError && !mapGeojson && !loadingMap && (
        <div className="map-placeholder map-large">地图生成失败，请稍后重试</div>
      )}
    </Spin>
  )

  return (
    <>
      <div className="map-view-container" style={{ position: 'relative', height: '100%', width: '100%' }}>
         {!isMapFullscreen && renderMapContent()}
         
         {tripResult && !isMapFullscreen && (
            <div style={{ position: 'absolute', top: 10, right: 10, zIndex: 1 }}>
               <Button size="small" onClick={() => setIsMapFullscreen(true)}>全屏</Button>
            </div>
         )}
      </div>

      {isMapFullscreen && (
        <div className="map-overlay">
          <div className="map-overlay-toolbar">
            <div className="map-overlay-title">行程地图</div>
            <Button size="small" type="text" onClick={() => setIsMapFullscreen(false)}>
              退出全屏
            </Button>
          </div>
          <div className="map-overlay-body">
            {renderMapContent()}
          </div>
        </div>
      )}
    </>
  )
}
