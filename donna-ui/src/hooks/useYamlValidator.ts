// donna-ui/src/hooks/useYamlValidator.ts
import { useEffect, useRef, useState } from "react";
import type { ValidationResult } from "../workers/yamlValidator.worker";

export interface YamlValidationState {
  validating: boolean;
  ok: boolean;
  data: unknown;
  error: ValidationResult["error"];
}

const DEBOUNCE_MS = 200;

/**
 * Debounced YAML validation via a module Web Worker.
 * Returns a stable state object reflecting the most recent validation.
 *
 * The worker is created once per hook instance and terminated on unmount.
 * Out-of-order responses are dropped by matching request ids.
 */
export function useYamlValidator(source: string): YamlValidationState {
  const [state, setState] = useState<YamlValidationState>({
    validating: false,
    ok: true,
    data: {},
    error: null,
  });
  const workerRef = useRef<Worker | null>(null);
  const nextId = useRef(0);
  const latestId = useRef(0);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const worker = new Worker(
      new URL("../workers/yamlValidator.worker.ts", import.meta.url),
      { type: "module" },
    );
    workerRef.current = worker;
    worker.addEventListener("message", (event: MessageEvent<ValidationResult>) => {
      const msg = event.data;
      if (msg.id !== latestId.current) return; // stale
      setState({
        validating: false,
        ok: msg.ok,
        data: msg.data ?? {},
        error: msg.error,
      });
    });
    return () => {
      worker.terminate();
      workerRef.current = null;
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    };
  }, []);

  useEffect(() => {
    if (!workerRef.current) return;
    setState((s) => ({ ...s, validating: true }));
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = window.setTimeout(() => {
      const id = ++nextId.current;
      latestId.current = id;
      workerRef.current?.postMessage({ id, source });
    }, DEBOUNCE_MS);
  }, [source]);

  return state;
}
