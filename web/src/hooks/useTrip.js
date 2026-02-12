import { useCallback, useMemo, useState } from "react"
import { message } from "antd"
import { generateTrip } from "../api/index.js"
import { DEFAULT_DEVICE_ID, DEFAULT_USER_ID, SESSION_STORAGE_KEY } from "../constants/appConfig.js"
import { normalizeTripDays } from "../utils/tripUtils.js"

export function useTrip({ activeSessionId, refreshSessions, setActiveSessionId }) {
  const [tripResult, setTripResult] = useState(null)
  const [loadingTrip, setLoadingTrip] = useState(false)
  const tripDays = useMemo(() => normalizeTripDays(tripResult), [tripResult])

  const handleTripSubmit = useCallback(
    async (values) => {
      try {
        setLoadingTrip(true)
        const payload = {
          user_id: DEFAULT_USER_ID,
          device_id: DEFAULT_DEVICE_ID,
          session_id: activeSessionId,
          destination: values.destination,
          days: values.days,
          budget: values.budget || "",
          preference: values.preference || "",
          context_texts: [],
        }
        const data = await generateTrip(payload)
        if (data?.session_id) {
          setActiveSessionId(data.session_id)
          localStorage.setItem(SESSION_STORAGE_KEY, data.session_id)
        }
        setTripResult(data?.trip_data || null)
        if (refreshSessions) {
          await refreshSessions()
        }
      } catch (error) {
        message.error(`行程生成失败：${error.message}`)
      } finally {
        setLoadingTrip(false)
      }
    },
    [activeSessionId, refreshSessions, setActiveSessionId]
  )

  return {
    handleTripSubmit,
    loadingTrip,
    tripDays,
    tripResult,
  }
}
