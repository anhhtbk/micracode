"use client";

import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { CommandPrompt } from "@/components/home/CommandPrompt";
import { createProject } from "@/lib/api/projects";
import { cn } from "@/lib/utils";

const NAME_MAX = 120;

function deriveProjectName(prompt: string): string {
  const firstLine = prompt.split(/\r?\n/)[0]?.trim() || prompt.trim();
  const source = firstLine.length > 0 ? firstLine : prompt.trim();
  if (source.length <= NAME_MAX) return source;
  const sliced = source.slice(0, NAME_MAX - 1);
  const lastSpace = sliced.lastIndexOf(" ");
  const cutoff = lastSpace > NAME_MAX * 0.6 ? sliced.slice(0, lastSpace) : sliced;
  return `${cutoff.trimEnd()}…`;
}

export function HeroComposer({ className }: { className?: string }) {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const submit = async (override?: string) => {
    const trimmed = (override ?? prompt).trim();
    if (!trimmed || isPending) return;
    setError(null);
    try {
      const name = deriveProjectName(trimmed);
      const record = await createProject({ name });
      setPrompt("");
      const nextUrl =
        `/projects/${record.id}?prompt=${encodeURIComponent(trimmed)}` as Route;
      startTransition(() => {
        router.push(nextUrl);
        router.refresh();
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section className={cn("flex w-full max-w-3xl flex-col items-center font-medium text-[5rem]", className)}>
      <h1
        className="hero-gradient-title mt-8 text-center"
        style={{
          fontFamily:
            "'Inter', system-ui, 'SF Pro Display', 'Helvetica Neue', sans-serif",
          fontWeight: 500,
          lineHeight: 1.15,
          letterSpacing: "-0.02em",
          background: "linear-gradient(90deg, #FFFFFF 0%, #4A4A4A 100%)",
          WebkitBackgroundClip: "text",
          backgroundClip: "text",
          WebkitTextFillColor: "transparent",
          color: "transparent",
        }}
      >
        <span className="block">Let&apos;s Build</span>
        <span className="block">Something Cool</span>
      </h1>
      <style jsx>{`
        @keyframes heroFadeUp {
          from {
            opacity: 0;
            transform: translateY(15px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        .hero-gradient-title {
          animation: heroFadeUp 0.8s ease-out both;
          will-change: opacity, transform;
        }
        @media (prefers-reduced-motion: reduce) {
          .hero-gradient-title {
            animation: none;
          }
        }
      `}</style>
      <p className="mt-3 text-center text-base text-zinc-400">
        Build local-first apps and websites through simple conversations
      </p>

      <div className="mt-10 w-full">
        <CommandPrompt
          value={prompt}
          onChange={setPrompt}
          onSubmit={(val) => void submit(val)}
          disabled={isPending}
          onChipClick={(chip) => {
            setPrompt(chip.label);
          }}
        />
      </div>

      {error ? (
        <p className="mt-3 text-sm text-red-400">{error}</p>
      ) : null}
    </section>
  );
}
