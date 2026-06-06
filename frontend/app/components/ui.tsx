import * as React from "react";
import { cn } from "@/lib/utils";

export function Button({
  className,
  variant = "default",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "ghost" | "danger" }) {
  const variants: Record<string, string> = {
    default: "bg-indigo-500 hover:bg-indigo-400 text-white",
    ghost: "bg-transparent hover:bg-white/10 text-slate-200 border border-white/15",
    danger: "bg-rose-600 hover:bg-rose-500 text-white",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition disabled:opacity-50",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}

export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border border-white/10 bg-white/[0.03] p-5 shadow-sm", className)}
      {...props}
    />
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "green" | "red" | "amber" | "blue";
}) {
  const tones: Record<string, string> = {
    neutral: "bg-slate-500/20 text-slate-300",
    green: "bg-emerald-500/20 text-emerald-300",
    red: "bg-rose-500/20 text-rose-300",
    amber: "bg-amber-500/20 text-amber-300",
    blue: "bg-sky-500/20 text-sky-300",
  };
  return (
    <span className={cn("inline-block rounded-full px-2 py-0.5 text-xs font-medium", tones[tone])}>
      {children}
    </span>
  );
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full rounded-md border border-white/15 bg-white/[0.04] px-3 py-1.5 text-sm outline-none focus:border-indigo-400",
        className,
      )}
      {...props}
    />
  );
}

export function statusTone(status: string): "neutral" | "green" | "red" | "amber" | "blue" {
  switch (status) {
    case "finished":
    case "succeeded":
    case "online":
      return "green";
    case "failed":
    case "errored":
    case "sync_failed":
    case "offline":
      return "red";
    case "running":
    case "assigned":
      return "blue";
    case "queued":
    case "syncing":
    case "pending":
      return "amber";
    default:
      return "neutral";
  }
}
