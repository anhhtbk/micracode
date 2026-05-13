"use client";

import { Rocket } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError, deployToVercel } from "@/lib/api/projects";

type Status = "idle" | "deploying" | "success" | "error";

export interface DeployVercelButtonProps {
  projectId: string;
}

export function DeployVercelButton({ projectId }: DeployVercelButtonProps) {
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string>("");
  const [deployUrl, setDeployUrl] = useState<string>("");

  // Auto-dismiss the toast a few seconds after a terminal status.
  useEffect(() => {
    if (status !== "success" && status !== "error") return;
    const t = window.setTimeout(() => {
      setStatus("idle");
      setMessage("");
    }, 8000);
    return () => window.clearTimeout(t);
  }, [status]);

  async function handleDeploy() {
    if (status === "deploying") return;
    setStatus("deploying");
    setMessage("Uploading project to Vercel…");
    setDeployUrl("");
    try {
      const result = await deployToVercel(projectId);
      setDeployUrl(result.url);
      setStatus("success");
      setMessage("Deployment created.");
    } catch (err) {
      const detail =
        err instanceof ApiError ? err.message : (err as Error).message;
      setStatus("error");
      setMessage(detail || "Deploy failed.");
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={handleDeploy}
        disabled={status === "deploying"}
        className="inline-flex h-8 items-center gap-1.5 rounded-md bg-black px-3 text-sm font-medium text-zinc-50 ring-1 ring-zinc-700 transition hover:bg-zinc-900 disabled:opacity-60"
        title="Deploy to Vercel"
      >
        <Rocket className="size-4" />
        {status === "deploying" ? "Deploying…" : "Deploy"}
      </button>

      {message ? (
        <div
          className={
            "absolute right-0 top-10 z-40 w-72 rounded-md border px-2.5 py-1.5 text-xs shadow-lg " +
            (status === "error"
              ? "border-red-900/60 bg-red-950/80 text-red-100"
              : "border-zinc-800 bg-zinc-950/95 text-zinc-100")
          }
        >
          {message}
          {status === "success" && deployUrl ? (
            <>
              {" "}
              <a
                href={deployUrl}
                target="_blank"
                rel="noreferrer"
                className="font-medium underline hover:text-zinc-50"
              >
                Open ↗
              </a>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
