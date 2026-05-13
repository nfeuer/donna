import { useEffect, useState } from "react";
import { Pill } from "../../primitives/Pill";
import { fetchQueueStatus, type GpuMetrics } from "../../api/skillSystem";
import styles from "./SkillSystem.module.css";

export default function GpuStatusCard() {
  const [gpu, setGpu] = useState<GpuMetrics | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const status = await fetchQueueStatus();
        if (!cancelled) setGpu(status.gpu ?? null);
      } catch {
        if (!cancelled) setGpu(null);
      }
    }

    poll();
    const interval = setInterval(poll, 10_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (!gpu) return null;

  return (
    <div className={styles.gpuCard}>
      <h3 className={styles.gpuCardTitle}>GPU Status</h3>
      <div className={styles.gpuRow}>
        <span>Loaded model</span>
        <span>{gpu.loaded_model ?? "none"}</span>
      </div>
      <div className={styles.gpuRow}>
        <span>Location</span>
        <Pill variant={gpu.is_home ? "success" : "warning"}>
          {gpu.is_home ? "Home" : "Away"}
        </Pill>
      </div>
      <div className={styles.gpuRow}>
        <span>Swaps this hour</span>
        <span>{gpu.swaps_this_hour}</span>
      </div>
      <div className={styles.gpuRow}>
        <span>Swap overhead (1h)</span>
        <span>{(gpu.swap_overhead_pct_1h ?? 0).toFixed(1)}%</span>
      </div>
    </div>
  );
}
