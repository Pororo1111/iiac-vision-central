export type FleetState = "ok" | "deploying" | "failed" | "offline";
export type FleetNode = { id: string; state: FleetState; applying: boolean };
export const stateColor: Record<FleetState, string> = { ok: "#18a86b", deploying: "#2f7df6", failed: "#e0444f", offline: "#9aa7b0" };
