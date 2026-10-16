import type { components } from "@rushbase/api-client";

type Schemas = components["schemas"];

export type User = Schemas["UserOut"];
export type Role = User["role"];
export type SystemStatus = Schemas["StatusOut"];
export type StageBacklog = Schemas["StageBacklogOut"];
export type Worker = Schemas["WorkerOut"];
export type Batch = Schemas["BatchOut"];
export type SetupStatus = Schemas["SetupStatus"];
export type BackupInfo = Schemas["BackupInfoOut"];
export type Health = Schemas["HealthResponse"];
export type EnrollmentCreated = Schemas["EnrollmentOut"];
export type LoginResult = Schemas["LoginOut"];

/** Pipeline order shown in the UI; stages the server does not report are skipped. */
export const STAGE_ORDER = [
  "probe",
  "hash",
  "preview",
  "shots",
  "audio",
  "embed",
  "vision",
  "classify",
  "finalize",
] as const;
export type StageName = (typeof STAGE_ORDER)[number];
