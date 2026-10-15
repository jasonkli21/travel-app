import { request } from "./api-request.mjs";

export interface ShoppingTaskStatus {
  schema_version: "suite-source-task-status-v1";
  proposal_id: string;
  state: "proposed" | "validated" | "confirmed" | "executing" | "reconciling" | "applied" | "rejected" | "uncertain" | "expired";
  binding: string;
  apply_available: boolean;
  confirmation_id: string | null;
  preview: string | null;
  outcome_code: string | null;
  domain_version: string | null;
}
export interface ShoppingTaskChoices { choices: { id: string; category_label: string }[] }
export interface ShoppingProjectChoice { project_id: string; label: string; revision: number }
export interface ShoppingProjectChoices {
  schema_version: "suite-task-target-choices-v1";
  choices: ShoppingProjectChoice[];
}
const prefix = "/v1/shopping-tasks";
export const shoppingTasks = {
  capabilities: () => request<{ enabled: boolean; recent_authentication: boolean }>(`${prefix}/capabilities`),
  choices: () => request<ShoppingTaskChoices>(`${prefix}/choices`),
  projects: () => request<ShoppingProjectChoices>(`${prefix}/projects`),
  create: (body: { command_id: string; trip_id: string; expected_trip_revision: number; context_choice_id: string; shopping_project_id: string; expected_project_revision: number }) => request(`${prefix}/commands`, { method: "POST", body: JSON.stringify(body) }),
  submit: (id: string) => request<ShoppingTaskStatus>(`${prefix}/commands/${id}/submit`, { method: "POST" }),
  control: (id: string, action: "validate" | "confirm" | "apply" | "reconcile", confirmation?: { confirmation_id: string; binding: string }) => request<ShoppingTaskStatus>(`${prefix}/commands/${id}/${action}`, { method: "POST", body: confirmation ? JSON.stringify(confirmation) : undefined }),
  status: (id: string) => request<ShoppingTaskStatus>(`${prefix}/commands/${id}`),
};
