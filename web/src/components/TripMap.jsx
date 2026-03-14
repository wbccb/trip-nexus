import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Button, Spin } from "antd"
import { DeckGL } from "@deck.gl/react"
import { ScatterplotLayer, LineLayer, TextLayer, IconLayer } from "@deck.gl/layers"
import { WebMercatorViewport } from "@deck.gl/core"
import MapView from "react-map-gl/maplibre"
import Supercluster from "supercluster"
import { renderTripGeojson } from "../api/index.js"

const logMap = (msg, data = {}) => {
  console.log(`[TripMap] ${msg}`, data)
}

const resolveRouteColor = (colorName) => {
  if (colorName === "green") return [76, 175, 80]
  if (colorName === "red") return [244, 67, 54]
  if (colorName === "purple") return [156, 39, 176]
  if (colorName === "orange") return [255, 152, 0]
  if (colorName === "darkblue") return [25, 118, 210]
  return [33, 150, 243]
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
  const bubbleIconCacheRef = useRef(new Map()) // 缓存气泡图标，避免重复生成 SVG

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
      const data = await renderTripGeojson({ trip_data: currentTrip })
      
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

  const { adjustedPoints, overlapCount } = useMemo(() => {
    const overlapIndex = new Map()
    const nextPoints = mapPoints.map((feature) => {
      const coords = feature?.geometry?.coordinates
      if (!Array.isArray(coords) || coords.length < 2) {
        return feature
      }
      const key = `${coords[0].toFixed(6)}_${coords[1].toFixed(6)}`
      const index = overlapIndex.get(key) || 0
      overlapIndex.set(key, index + 1)
      if (index === 0) {
        return feature
      }
      const ring = Math.floor(index / 6) + 1
      const angle = (index % 6) * (Math.PI / 3)
      const offset = 0.00012 * ring
      const nextCoords = [coords[0] + Math.cos(angle) * offset, coords[1] + Math.sin(angle) * offset]
      return {
        ...feature,
        geometry: {
          ...feature.geometry,
          coordinates: nextCoords,
        },
      }
    })
    const totalOverlaps = Array.from(overlapIndex.values()).reduce((sum, count) => sum + Math.max(count - 1, 0), 0)
    return { adjustedPoints: nextPoints, overlapCount: totalOverlaps }
  }, [mapPoints])

  useEffect(() => {
    if (!selectedPoiId) return
    
    logMap("选中 POI 发生变化", { selectedPoiId })
    const target = adjustedPoints.find((feature) => feature?.properties?.poi_id === selectedPoiId)
    const coords = target?.geometry?.coordinates
    
    if (!Array.isArray(coords) || coords.length < 2) return
    
    setMapViewState((prev) => ({
      ...prev,
      longitude: coords[0],
      latitude: coords[1],
      zoom: Math.max(prev.zoom || 0, 13),
      transitionDuration: 1000,
    }))
  }, [adjustedPoints, selectedPoiId])

  const clusterIndex = useMemo(() => {
    const index = new Supercluster({ radius: 50, maxZoom: 16 })
    index.load(adjustedPoints)
    return index
  }, [adjustedPoints])

  const clusters = useMemo(() => {
    if (!clusterIndex || !mapViewState) return []
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

  const displayPoints = useMemo(() => {
    if (clusters.length > 0) {
      return clusters
    }
    return adjustedPoints
  }, [clusters, adjustedPoints])

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

  const getPoiLabel = useCallback((feature) => {
    const order = feature?.properties?.order ? `${feature.properties.order}. ` : ""
    const name = feature?.properties?.attraction || "POI"
    return `${order}${name}`.trim()
  }, [])

  const getBubbleIcon = useCallback(
    (feature) => { // 根据 POI 生成带文字的气泡 SVG 图标
      const label = getPoiLabel(feature) // 获取 POI 显示文案
      const isSelected = feature?.properties?.poi_id === selectedPoiId // 判断是否为选中态
      const cacheKey = `${isSelected ? "1" : "0"}_${label}` // 构造缓存键
      const cached = bubbleIconCacheRef.current.get(cacheKey) // 尝试命中缓存
      if (cached) { // 已缓存时直接返回
        return cached
      }
      const paddingX = 16 // 文本左右内边距
      const minWidth = 140 // 气泡最小宽度
      const maxWidth = 260 // 气泡最大宽度
      const fontSize = 14 // 气泡文字字号
      const textLength = label.length // 文本长度估算
      const estimatedWidth = Math.min(maxWidth, Math.max(minWidth, textLength * 14 + paddingX * 2)) // 根据长度估算宽度
      const height = 64 // 气泡整体高度
      const tailHeight = 12 // 气泡尾巴高度
      const rectHeight = height - tailHeight // 文字背景区域高度
      const centerX = estimatedWidth / 2 // 气泡水平中心
      const strokeColor = isSelected ? "#ff5722" : "#c4c4c4" // 选中/默认描边色
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${estimatedWidth}" height="${height}" viewBox="0 0 ${estimatedWidth} ${height}">
  <rect x="2" y="2" width="${estimatedWidth - 4}" height="${rectHeight - 4}" rx="10" fill="white" stroke="${strokeColor}" stroke-width="2"/>
  <path d="M ${centerX - 8} ${rectHeight} L ${centerX} ${rectHeight + tailHeight} L ${centerX + 8} ${rectHeight} Z" fill="white" stroke="${strokeColor}" stroke-width="2"/>
</svg>` // 拼接包含文字的 SVG
      const icon = { // IconLayer 所需图标结构
        url: `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`, // 转成 data URL 供渲染
        width: estimatedWidth, // 设置图标宽度
        height, // 设置图标高度
        anchorX: centerX, // 设置锚点横向位置
        anchorY: height, // 设置锚点纵向位置
      }
      bubbleIconCacheRef.current.set(cacheKey, icon) // 写入缓存
      return icon // 返回生成的图标
    },
    [getPoiLabel, selectedPoiId], // 依赖：文案函数、选中态
  )

  const poiLabelCharacterSet = useMemo(() => { // 汇总所有 POI 标签字符，避免 TextLayer 字符集缺失
    const chars = new Set() // 使用 Set 去重字符
    adjustedPoints.forEach((feature) => { // 遍历所有已调整坐标的 POI
      const label = getPoiLabel(feature) // 获取 POI 展示文本
      Array.from(label).forEach((char) => chars.add(char)) // 将每个字符写入字符集
    })
    return Array.from(chars) // 返回 deck.gl 可识别的字符数组
  }, [adjustedPoints, getPoiLabel])

  const mapLayers = useMemo(() => {
    // logMap("开始构建图层", { 
    //   points: mapPoints.length, 
    //   visiblePoints: displayPoints.length,
    //   clusters: clusters.length,
    //   routes: routeSegments.length,
    //   zoom: mapViewState.zoom,
    //   overlapCount,
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

    if (displayPoints.length > 0) {
      const poiLabels = adjustedPoints.filter((item) => Array.isArray(item?.geometry?.coordinates))

      layers.push(
        new ScatterplotLayer({
          id: "trip-points",
          data: displayPoints,
          getPosition: (d) => d.geometry.coordinates,
          getRadius: (d) => {
            if (d.properties.cluster) {
              return 14 + Math.log(d.properties.point_count) * 6
            }
            return d.properties?.poi_id === selectedPoiId ? 7 : 5
          },
          getFillColor: (d) => {
            if (d.properties.cluster) {
              return [30, 136, 229, 200]
            }
            return d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 200] : [76, 175, 80, 140]
          },
          getLineColor: (d) => (d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 255] : [255, 255, 255]),
          getLineWidth: (d) => (d.properties?.poi_id === selectedPoiId ? 2 : 1),
          pickable: true,
          onClick: (info) => {
            if (!info?.object || info.object?.properties?.cluster) {
              return
            }
            if (onSelectPoi) {
              onSelectPoi(info.object?.properties?.poi_id)
            }
          },
        }),
      )

      layers.push(
        new IconLayer({
          id: "trip-poi-bubbles",
          data: poiLabels,
          getPosition: (d) => d.geometry.coordinates,
          getIcon: getBubbleIcon, // 使用动态气泡 SVG 图标
          getSize: 64, // 以像素高度渲染气泡，确保可见
          sizeScale: 1,
          sizeUnits: "pixels",
          billboard: true,
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
          getPixelOffset: [0, -38],
          characterSet: poiLabelCharacterSet,
          fontFamily: "'PingFang SC','Microsoft YaHei','Noto Sans CJK SC',sans-serif",
          background: false,
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
  }, [
    clusterCounts,
    clusters,
    displayPoints,
    getBubbleIcon,
    getPoiLabel,
    poiLabelCharacterSet,
    mapPoints.length,
    onSelectPoi,
    adjustedPoints,
    overlapCount,
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
