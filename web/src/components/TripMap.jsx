import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Button, Spin } from "antd"
import { DeckGL } from "@deck.gl/react"
import { ScatterplotLayer, LineLayer, TextLayer } from "@deck.gl/layers"
import { WebMercatorViewport } from "@deck.gl/core"
import Map from "react-map-gl/maplibre"
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

  const loadMapGeojson = useCallback(async (currentTrip) => {
    logMap("loadMapGeojson called", { hasTrip: Boolean(currentTrip) })
    
    if (!currentTrip) {
      mapRequestTokenRef.current += 1
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
      
      logMap("fetching geojson...", { destination: currentTrip.destination })
      const data = await renderTripGeojson({ trip_data: currentTrip })
      
      if (mapRequestTokenRef.current !== token) {
        logMap("request stale, ignoring result")
        return
      }
      
      logMap("geojson received", { 
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
        logMap("fitting bounds", nextViewState)
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
      logMap("error loading map", error)
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

  useEffect(() => {
    if (!selectedPoiId) return
    
    logMap("selectedPoiId changed", { selectedPoiId })
    const target = mapPoints.find((feature) => feature?.properties?.poi_id === selectedPoiId)
    const coords = target?.geometry?.coordinates
    
    if (!Array.isArray(coords) || coords.length < 2) return
    
    setMapViewState((prev) => ({
      ...prev,
      longitude: coords[0],
      latitude: coords[1],
      zoom: Math.max(prev.zoom || 0, 13),
      transitionDuration: 1000,
    }))
  }, [mapPoints, selectedPoiId])

  const clusterIndex = useMemo(() => {
    const index = new Supercluster({ radius: 50, maxZoom: 16 })
    index.load(mapPoints)
    return index
  }, [mapPoints])

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

  const mapLayers = useMemo(() => {
    logMap("building layers", { 
      points: clusters.length, 
      routes: routeSegments.length,
      zoom: mapViewState.zoom
    })

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
      const poiLabels = clusters.filter((item) => !item?.properties?.cluster)
      
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
            if (onSelectPoi) {
              onSelectPoi(info.object?.properties?.poi_id)
            }
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

      layers.push(
        new TextLayer({
          id: "trip-poi-labels",
          data: poiLabels,
          getPosition: (d) => d.geometry.coordinates,
          getText: (d) => {
            const order = d.properties?.order ? `${d.properties.order}. ` : ""
            const name = d.properties?.attraction || "POI"
            return `${order}${name}`
          },
          getSize: (d) => (d.properties?.poi_id === selectedPoiId ? 14 : 12),
          getColor: (d) => (d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 230] : [33, 33, 33, 230]),
          getTextAnchor: "start",
          getAlignmentBaseline: "center",
          getPixelOffset: [10, 0],
          maxWidth: 240,
          sizeScale: 1,
          background: true,
          getBackgroundColor: (d) =>
            d.properties?.poi_id === selectedPoiId ? [255, 255, 255, 230] : [255, 255, 255, 200],
          getBorderColor: (d) =>
            d.properties?.poi_id === selectedPoiId ? [255, 87, 34, 230] : [180, 180, 180, 200],
          getBorderWidth: 1,
        }),
      )
    }
    return layers
  }, [clusterCounts, clusters, onSelectPoi, routeSegments, selectedPoiId, mapViewState.zoom])

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
