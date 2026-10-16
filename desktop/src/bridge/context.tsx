import { createContext, useContext, type ReactNode } from "react";

import type { Bridge } from "./bridge";

const BridgeContext = createContext<Bridge | null>(null);

export function BridgeProvider({ bridge, children }: { bridge: Bridge; children: ReactNode }) {
  return <BridgeContext.Provider value={bridge}>{children}</BridgeContext.Provider>;
}

/** The host bridge (Tauri commands or the mock). */
// eslint-disable-next-line react-refresh/only-export-components
export function useBridge(): Bridge {
  const bridge = useContext(BridgeContext);
  if (!bridge) throw new Error("BridgeProvider is missing");
  return bridge;
}
