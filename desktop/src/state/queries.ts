import { useQuery } from "@tanstack/react-query";

import { cliData } from "../bridge";
import { useBridge } from "../bridge/context";
import type { GpuStats, Overview, PlatformInfo, Settings } from "../bridge/types";

const OVERVIEW_REFRESH_MS = 20_000;
const GPU_REFRESH_MS = 4_000;

/** Backlog, estimate, hardware and guided issues of this PC (`rushbase overview`). */
export function useOverview() {
  const bridge = useBridge();
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => cliData<Overview>(bridge, ["overview"]),
    refetchInterval: OVERVIEW_REFRESH_MS,
    retry: false,
  });
}

/** Live GPU telemetry (`null` without nvidia-smi). */
export function useGpuStats() {
  const bridge = useBridge();
  return useQuery<GpuStats | null>({
    queryKey: ["gpu"],
    queryFn: () => bridge.gpuStats(),
    refetchInterval: GPU_REFRESH_MS,
  });
}

export function useSettings() {
  const bridge = useBridge();
  return useQuery<Settings>({ queryKey: ["settings"], queryFn: () => bridge.getSettings() });
}

export function usePlatform() {
  const bridge = useBridge();
  return useQuery<PlatformInfo>({
    queryKey: ["platform"],
    queryFn: () => bridge.platform(),
    staleTime: Infinity,
  });
}
