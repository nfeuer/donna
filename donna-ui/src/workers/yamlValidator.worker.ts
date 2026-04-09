// donna-ui/src/workers/yamlValidator.worker.ts
// Vite module worker. Consumed via:
//   new Worker(new URL("./yamlValidator.worker.ts", import.meta.url), { type: "module" })
import yaml from "yaml";

export interface ValidationRequest {
  id: number;
  source: string;
}

export interface ValidationResult {
  id: number;
  ok: boolean;
  data: unknown;
  error: { message: string; line?: number; column?: number } | null;
}

self.addEventListener("message", (event: MessageEvent<ValidationRequest>) => {
  const { id, source } = event.data;
  try {
    const data = yaml.parse(source);
    const result: ValidationResult = { id, ok: true, data: data ?? {}, error: null };
    (self as unknown as Worker).postMessage(result);
  } catch (err) {
    const e = err as { message?: string; linePos?: Array<{ line: number; col: number }> };
    const pos = e.linePos?.[0];
    const result: ValidationResult = {
      id,
      ok: false,
      data: null,
      error: {
        message: e.message ?? "Invalid YAML",
        line: pos?.line,
        column: pos?.col,
      },
    };
    (self as unknown as Worker).postMessage(result);
  }
});

export {};
