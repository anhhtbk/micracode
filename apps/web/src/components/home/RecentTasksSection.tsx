"use client";

import {
  CheckCircle2,
  ClipboardList,
  ExternalLink,
  Globe2,
  Loader2,
  RefreshCw,
  Rocket,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";

import {
  deleteProject,
  promoteDeployment,
  type DeploymentRecord,
  type ProjectRecord,
} from "@/lib/api/projects";
import { cn } from "@/lib/utils";

type Tab = "recent" | "deployed";

export interface RecentTasksSectionProps {
  projects: ProjectRecord[];
  error: string | null;
  className?: string;
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffMs = Date.now() - then;
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return `${sec} seconds ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} ${min === 1 ? "minute" : "minutes"} ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} ${hr === 1 ? "hour" : "hours"} ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} ${day === 1 ? "day" : "days"} ago`;
  const mo = Math.round(day / 30);
  if (mo < 12) return `${mo} ${mo === 1 ? "month" : "months"} ago`;
  const yr = Math.round(mo / 12);
  return `${yr} ${yr === 1 ? "year" : "years"} ago`;
}

function shortId(id: string): string {
  const stripped = id.replace(/[^a-z0-9]/gi, "");
  return `EMT - ${stripped.slice(0, 6) || id.slice(0, 6)}`;
}

export function RecentTasksSection({
  projects,
  error,
  className,
}: RecentTasksSectionProps) {
  const [tab, setTab] = useState<Tab>("recent");
  const router = useRouter();
  const [isRefreshing, startRefresh] = useTransition();

  return (
    <section className={cn("flex w-full flex-col", className)}>
      <div className="flex items-center justify-between border-b border-[#1b1b1e] pb-3">
        <div className="flex items-center gap-3 text-sm font-medium">
          <button
            type="button"
            onClick={() => setTab("recent")}
            className={cn(
              "inline-flex items-center gap-2 rounded-md px-1 py-1 transition-all duration-200 ease-in-out",
              tab === "recent" ? "text-white" : "text-zinc-500 hover:text-zinc-300",
            )}
          >
            <ClipboardList className="size-4" />
            Recent Tasks
          </button>
          <span className="text-zinc-700">|</span>
          <button
            type="button"
            onClick={() => setTab("deployed")}
            className={cn(
              "inline-flex items-center gap-2 rounded-md px-1 py-1 transition-all duration-200 ease-in-out",
              tab === "deployed" ? "text-white" : "text-zinc-500 hover:text-zinc-300",
            )}
          >
            <Globe2 className="size-4" />
            Deployed Apps
          </button>
        </div>
        <button
          type="button"
          onClick={() => startRefresh(() => router.refresh())}
          className="inline-flex size-8 items-center justify-center rounded-md text-zinc-400 transition-all duration-200 ease-in-out hover:bg-[#1b1b1e] hover:text-white"
          aria-label="Refresh"
        >
          <RefreshCw className={cn("size-4", isRefreshing && "animate-spin")} />
        </button>
      </div>

      {error ? (
        <div className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          {error}
        </div>
      ) : tab === "recent" ? (
        <RecentTable projects={projects} onChanged={() => router.refresh()} />
      ) : (
        <DeployedAppsTable
          projects={projects}
          onChanged={() => router.refresh()}
        />
      )}
    </section>
  );
}

function RecentTable({
  projects,
  onChanged,
}: {
  projects: ProjectRecord[];
  onChanged: () => void;
}) {
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (projects.length === 0) {
    return (
      <EmptyState
        title="No tasks yet"
        description="Start a new project above and it will appear here."
      />
    );
  }

  async function handleDelete(p: ProjectRecord) {
    if (pendingId) return;
    const vercelWarning = p.vercel_project_name
      ? `\n\nThis will also delete the Vercel project "${p.vercel_project_name}" and take its production URL offline.`
      : "";
    const ok = window.confirm(
      `Delete project "${p.name}"? This will permanently remove its files and history.${vercelWarning}`,
    );
    if (!ok) return;
    setPendingId(p.id);
    setErrorMsg(null);
    try {
      await deleteProject(p.id);
      onChanged();
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : "Failed to delete project",
      );
    } finally {
      setPendingId(null);
    }
  }

  return (
    <div className="mt-2">
      {errorMsg ? (
        <div className="mb-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {errorMsg}
        </div>
      ) : null}
      <div className="grid grid-cols-[140px_1fr_200px_40px] items-center gap-4 border-b border-[#1b1b1e] px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">
        <span>ID</span>
        <span>Task</span>
        <span>Last Modified</span>
        <span />
      </div>
      <ul className="flex flex-col">
        {projects.map((p) => {
          const isDeleting = pendingId === p.id;
          return (
            <li key={p.id} className="group relative">
              <Link
                href={`/projects/${p.id}`}
                className={cn(
                  "grid grid-cols-[140px_1fr_200px_40px] items-center gap-4 rounded-md px-4 py-4 text-sm transition-all duration-200 ease-in-out hover:bg-[#1b1b1e]",
                  isDeleting && "pointer-events-none opacity-50",
                )}
              >
                <span className="font-mono text-xs text-zinc-400">
                  {shortId(p.id)}
                </span>
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="truncate font-medium text-white">{p.name}</span>
                  <span className="truncate text-xs text-zinc-500">
                    {p.template || "Generated project"}
                  </span>
                </span>
                <RelativeTime
                  iso={p.updated_at}
                  className="text-sm text-zinc-400"
                />
                <span className="flex justify-end">
                  <button
                    type="button"
                    aria-label={`Delete ${p.name}`}
                    title="Delete project"
                    disabled={isDeleting}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      void handleDelete(p);
                    }}
                    className="inline-flex size-8 items-center justify-center rounded-md text-zinc-500 transition-all duration-200 ease-in-out hover:bg-red-500/10 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isDeleting ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Trash2 className="size-4" />
                    )}
                  </button>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function RelativeTime({ iso, className }: { iso: string; className?: string }) {
  const [text, setText] = useState<string>("");
  useEffect(() => {
    setText(formatRelative(iso));
    const id = setInterval(() => setText(formatRelative(iso)), 30_000);
    return () => clearInterval(id);
  }, [iso]);
  return (
    <span className={className} suppressHydrationWarning>
      {text}
    </span>
  );
}

function DeployedAppsTable({
  projects,
  onChanged,
}: {
  projects: ProjectRecord[];
  onChanged: () => void;
}) {
  const deployed = projects.filter(
    (p) => (p.deployments?.length ?? 0) > 0,
  );
  if (deployed.length === 0) {
    return (
      <EmptyState
        title="No deployed apps yet"
        description="Deploy a project to Vercel and its versions will show up here."
      />
    );
  }
  return (
    <ul className="mt-2 flex flex-col gap-3">
      {deployed.map((p) => (
        <DeployedProjectCard key={p.id} project={p} onChanged={onChanged} />
      ))}
    </ul>
  );
}

function DeployedProjectCard({
  project,
  onChanged,
}: {
  project: ProjectRecord;
  onChanged: () => void;
}) {
  const deployments = [...(project.deployments ?? [])].reverse();
  const current = deployments.find((d) => d.is_current_production);
  // Only show the stable alias when Vercel actually returned one — never
  // synthesise it from project name (Vercel may append a suffix).
  const aliasUrl = current?.alias_url || null;

  const [pendingId, setPendingId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function handlePromote(d: DeploymentRecord) {
    if (pendingId || d.is_current_production) return;
    setPendingId(d.id);
    setErrorMsg(null);
    try {
      await promoteDeployment(project.id, d.id);
      onChanged();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Promote failed");
    } finally {
      setPendingId(null);
    }
  }

  return (
    <li className="rounded-xl border border-[#1b1b1e] bg-[#0f0f11] p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-col">
          <Link
            href={`/projects/${project.id}`}
            className="truncate text-sm font-medium text-white hover:underline"
          >
            {project.name}
          </Link>
          {aliasUrl ? (
            <a
              href={aliasUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex w-fit items-center gap-1 text-xs text-zinc-400 hover:text-white"
            >
              {aliasUrl.replace(/^https?:\/\//, "")}
              <ExternalLink className="size-3" />
            </a>
          ) : null}
        </div>
        <span className="shrink-0 text-[10px] uppercase tracking-wider text-zinc-500">
          {deployments.length} version{deployments.length === 1 ? "" : "s"}
        </span>
      </div>

      {errorMsg ? (
        <div className="mt-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {errorMsg}
        </div>
      ) : null}

      <ul className="mt-3 flex flex-col divide-y divide-[#1b1b1e] border-t border-[#1b1b1e]">
        {deployments.map((d) => {
          const isBusy = pendingId === d.id;
          return (
            <li
              key={d.id}
              className="grid grid-cols-[1fr_auto_auto] items-center gap-3 py-2 text-xs"
            >
              <div className="flex min-w-0 items-center gap-2">
                {d.is_current_production ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-300">
                    <CheckCircle2 className="size-3" />
                    Production
                  </span>
                ) : (
                  <span className="inline-flex items-center rounded-full bg-zinc-800 px-2 py-0.5 text-[10px] text-zinc-400">
                    {d.target}
                  </span>
                )}
                <a
                  href={d.url}
                  target="_blank"
                  rel="noreferrer"
                  className="truncate font-mono text-zinc-300 hover:text-white"
                  title={d.url}
                >
                  {d.url.replace(/^https?:\/\//, "")}
                </a>
              </div>
              <RelativeTime iso={d.created_at} className="text-zinc-500" />
              <div className="flex items-center gap-1">
                {!d.is_current_production && d.target === "production" ? (
                  <button
                    type="button"
                    onClick={() => void handlePromote(d)}
                    disabled={isBusy || pendingId !== null}
                    className="inline-flex h-7 items-center gap-1 rounded-md border border-zinc-700 px-2 text-[11px] text-zinc-200 transition hover:bg-zinc-800 disabled:opacity-50"
                    title="Promote this version to production"
                  >
                    {isBusy ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <Rocket className="size-3" />
                    )}
                    Promote
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </li>
  );
}

function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="mt-6 flex flex-col items-center justify-center rounded-xl border border-dashed border-[#333336] bg-[#1b1b1e]/40 px-6 py-12 text-center">
      <p className="text-sm font-medium text-white">{title}</p>
      <p className="mt-1 text-xs text-zinc-400">{description}</p>
    </div>
  );
}
