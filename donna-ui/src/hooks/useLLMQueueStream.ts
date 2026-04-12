import { useEffect, useRef, useState } from "react";
import {
  createQueueSSEUrl,
  type LLMQueueStatusData,
} from "../api/llmGateway";

interface UseLLMQueueStreamResult {
  data: LLMQueueStatusData | null;
  connected: boolean;
}

/**
 * SSE hook for real-time LLM queue status.
 * Opens an EventSource on mount, parses JSON events into typed state,
 * auto-reconnects with exponential backoff, and closes on unmount.
 */
export function useLLMQueueStream(): UseLLMQueueStreamResult {
  const [data, setData] = useState<LLMQueueStatusData | null>(null);
  const [connected, setConnected] = useState(false);
  const retryDelay = useRef(1000);

  useEffect(() => {
    let es: EventSource | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;
    let unmounted = false;

    function connect() {
      if (unmounted) return;

      const url = createQueueSSEUrl();
      es = new EventSource(url);

      es.onopen = () => {
        if (unmounted) return;
        setConnected(true);
        retryDelay.current = 1000; // Reset backoff on successful connect
      };

      es.onmessage = (event) => {
        if (unmounted) return;
        try {
          const parsed = JSON.parse(event.data) as LLMQueueStatusData;
          setData(parsed);
        } catch {
          // Ignore malformed events
        }
      };

      es.onerror = () => {
        if (unmounted) return;
        setConnected(false);
        es?.close();
        es = null;

        // Exponential backoff: 1s, 2s, 4s, 8s, max 30s
        retryTimeout = setTimeout(() => {
          retryDelay.current = Math.min(retryDelay.current * 2, 30000);
          connect();
        }, retryDelay.current);
      };
    }

    connect();

    return () => {
      unmounted = true;
      es?.close();
      if (retryTimeout) clearTimeout(retryTimeout);
    };
  }, []);

  return { data, connected };
}
