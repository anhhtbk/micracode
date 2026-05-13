/**
 * Typed fetch client for the project CRUD + hydration endpoints.
 *
 * Works on both the server (React Server Components) and the client:
 * callers just need to pass `cache: "no-store"` from RSCs when they
 * want fresh data on every render.
 */

import type { FileSystemTree } from "@micracode/shared";

import { env } from "@/lib/env";

export type PromptRole = "user" | "assistant" | "system" | "tool";

export interface DeploymentRecord {
  id: string;
  url: string;
  alias_url?: string | null;
  target: "production" | "preview";
  created_at: string;
  is_current_production: boolean;
}

export interface ProjectRecord {
  id: string;
  name: string;
  template: string;
  created_at: string;
  updated_at: string;
  vercel_project_name?: string | null;
  deployments?: DeploymentRecord[];
}

export interface CreateProjectBody {
  name: string;
  template?: string;
}

export interface PromptRecord {
  id: string;
  role: PromptRole;
  content: string;
  created_at: string;
  snapshot_id?: string | null;
}

export interface SnapshotRecord {
  id: string;
  created_at: string;
  user_prompt: string;
  kind: "pre-turn";
}

export interface ProjectFilesResponse {
  tree: FileSystemTree;
}

export interface UpdateProjectFileBody {
  path: string;
  content: string;
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  /** Next.js `cache` + `next.revalidate` options. */
  next?: { revalidate?: number | false; tags?: string[] };
  cache?: RequestCache;
}

function baseUrl(opts?: ApiClientOptions): string {
  return opts?.baseUrl ?? env.API_BASE_URL;
}

async function request<T>(
  path: string,
  init: RequestInit & { next?: { revalidate?: number | false; tags?: string[] } },
  opts?: ApiClientOptions,
): Promise<T> {
  const fetchImpl = opts?.fetchImpl ?? fetch;
  const res = await fetchImpl(`${baseUrl(opts)}${path}`, {
    ...init,
    cache: opts?.cache ?? init.cache ?? "no-store",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(
      `${init.method ?? "GET"} ${path} failed: ${res.status} ${body}`,
      res.status,
    );
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function listProjects(opts?: ApiClientOptions): Promise<ProjectRecord[]> {
  return request<ProjectRecord[]>("/v1/projects", { method: "GET" }, opts);
}

export function createProject(
  body: CreateProjectBody,
  opts?: ApiClientOptions,
): Promise<ProjectRecord> {
  return request<ProjectRecord>(
    "/v1/projects",
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}

export function getProject(
  id: string,
  opts?: ApiClientOptions,
): Promise<ProjectRecord> {
  return request<ProjectRecord>(
    `/v1/projects/${encodeURIComponent(id)}`,
    { method: "GET" },
    opts,
  );
}

export async function deleteProject(
  id: string,
  opts?: ApiClientOptions,
): Promise<void> {
  await request<void>(
    `/v1/projects/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    opts,
  );
}

export function getProjectFiles(
  id: string,
  opts?: ApiClientOptions,
): Promise<ProjectFilesResponse> {
  return request<ProjectFilesResponse>(
    `/v1/projects/${encodeURIComponent(id)}/files`,
    { method: "GET" },
    opts,
  );
}

export function updateProjectFile(
  id: string,
  body: UpdateProjectFileBody,
  opts?: ApiClientOptions,
): Promise<void> {
  return request<void>(
    `/v1/projects/${encodeURIComponent(id)}/files`,
    { method: "PUT", body: JSON.stringify(body) },
    opts,
  );
}

export function getProjectDownloadUrl(
  id: string,
  opts?: ApiClientOptions,
): string {
  return `${baseUrl(opts)}/v1/projects/${encodeURIComponent(id)}/download`;
}

export function getProjectPrompts(
  id: string,
  opts?: ApiClientOptions,
): Promise<PromptRecord[]> {
  return request<PromptRecord[]>(
    `/v1/projects/${encodeURIComponent(id)}/prompts`,
    { method: "GET" },
    opts,
  );
}

export function popLastAssistantPrompt(
  id: string,
  opts?: ApiClientOptions,
): Promise<{ popped: boolean }> {
  return request<{ popped: boolean }>(
    `/v1/projects/${encodeURIComponent(id)}/prompts/pop-assistant`,
    { method: "POST" },
    opts,
  );
}

export function listSnapshots(
  id: string,
  opts?: ApiClientOptions,
): Promise<SnapshotRecord[]> {
  return request<SnapshotRecord[]>(
    `/v1/projects/${encodeURIComponent(id)}/snapshots`,
    { method: "GET" },
    opts,
  );
}

export async function restoreSnapshot(
  id: string,
  snapshotId: string,
  opts?: ApiClientOptions,
): Promise<void> {
  await request<void>(
    `/v1/projects/${encodeURIComponent(id)}/snapshots/${encodeURIComponent(snapshotId)}/restore`,
    { method: "POST" },
    opts,
  );
}

export interface VercelDeployBody {
  target?: "production" | "preview";
}

export interface VercelDeployResult {
  id: string;
  url: string;
  alias_url?: string | null;
  inspector_url?: string | null;
}

export function deployToVercel(
  id: string,
  body: VercelDeployBody = {},
  opts?: ApiClientOptions,
): Promise<VercelDeployResult> {
  return request<VercelDeployResult>(
    `/v1/projects/${encodeURIComponent(id)}/deploy/vercel`,
    { method: "POST", body: JSON.stringify(body) },
    opts,
  );
}

export interface PromoteResult {
  ok: boolean;
  project: ProjectRecord;
}

export function promoteDeployment(
  id: string,
  deploymentId: string,
  opts?: ApiClientOptions,
): Promise<PromoteResult> {
  return request<PromoteResult>(
    `/v1/projects/${encodeURIComponent(id)}/deployments/${encodeURIComponent(deploymentId)}/promote`,
    { method: "POST" },
    opts,
  );
}

export async function deleteSnapshot(
  id: string,
  snapshotId: string,
  opts?: ApiClientOptions,
): Promise<void> {
  await request<void>(
    `/v1/projects/${encodeURIComponent(id)}/snapshots/${encodeURIComponent(snapshotId)}`,
    { method: "DELETE" },
    opts,
  );
}
