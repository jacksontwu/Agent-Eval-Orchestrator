import * as React from "react";
import { cn } from "@/lib/utils";

export function Button({
  className,
  variant = "default",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "ghost" | "danger" }) {
  const variants: Record<string, string> = {
    default: "bg-primary text-primary-foreground hover:bg-primary/90",
    ghost:
      "border border-border bg-background hover:bg-accent hover:text-accent-foreground dark:bg-input/30 dark:border-input dark:hover:bg-input/50",
    danger: "bg-destructive text-white hover:bg-destructive/90 dark:bg-destructive/60",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md px-4 h-9 text-sm font-medium transition-all outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
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
      className={cn("bg-card text-card-foreground border border-border p-5", className)}
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
    neutral: "border-border bg-muted text-muted-foreground",
    green: "border-emerald-600/20 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    red: "border-red-600/20 bg-red-500/10 text-red-600 dark:text-red-400",
    amber: "border-amber-600/20 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    blue: "border-blue-600/20 bg-blue-500/10 text-blue-600 dark:text-blue-400",
  };
  return (
    <span
      className={cn(
        "inline-flex w-fit items-center justify-center border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "w-full min-w-0 rounded-md border border-input bg-transparent px-3 h-9 text-sm shadow-xs outline-none transition-[color,box-shadow] placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30",
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
