const LEVEL_ORDER = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

function resolveCurrentLevel() {
  try {
    const storageLevel =
      typeof window !== "undefined"
        ? window.localStorage.getItem("TRIPNEXUS_LOG_LEVEL")
        : "";
    return String(
      storageLevel || import.meta.env.VITE_APP_LOG_LEVEL || "warn",
    ).toLowerCase();
  } catch {
    return String(import.meta.env.VITE_APP_LOG_LEVEL || "warn").toLowerCase();
  }
}

function summarizeValue(value, head = 24, tail = 24) {
  if (value === null || value === undefined) {
    return "";
  }
  const text =
    typeof value === "string" ? value : JSON.stringify(value, null, 0);
  if (text.length <= head + tail + 3) {
    return text;
  }
  return `${text.slice(0, head)}...${text.slice(-tail)}`;
}

function normalizePayload(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return payload;
  }
  return Object.fromEntries(
    Object.entries(payload)
      .filter(([key]) => key !== "__level")
      .map(([key, value]) => [key, summarizeValue(value)]),
  );
}

export function logDebug(scope, message, payload) {
  const level = payload?.__level || "info";
  const currentLevel = resolveCurrentLevel();
  if ((LEVEL_ORDER[level] || 20) < (LEVEL_ORDER[currentLevel] || 30)) {
    return;
  }
  const normalizedPayload = normalizePayload(payload);
  const prefix = `【${scope}】${message}`;
  if (level === "debug") {
    console.debug(prefix, normalizedPayload);
    return;
  }
  if (level === "warn") {
    console.warn(prefix, normalizedPayload);
    return;
  }
  if (level === "error") {
    console.error(prefix, normalizedPayload);
    return;
  }
  console.info(prefix, normalizedPayload);
}
