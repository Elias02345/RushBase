/** Readiness 0 (server unreachable), 50 (up, database down) or 100 (all good). */
export function readiness(serverUp: boolean, databaseUp: boolean): number {
  if (!serverUp) return 0;
  return databaseUp ? 100 : 50;
}
