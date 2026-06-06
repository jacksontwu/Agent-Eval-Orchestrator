import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { Moon, Sun } from "lucide-react";
import { queryClient } from "@/lib/query";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "任务", end: true },
  { to: "/create", label: "新建任务" },
  { to: "/workers", label: "机器" },
];

function useTheme() {
  const [isDark, setIsDark] = useState(() =>
    typeof document !== "undefined" && document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark);
    try {
      localStorage.setItem("theme", isDark ? "dark" : "light");
    } catch {}
  }, [isDark]);
  return { isDark, toggle: () => setIsDark((v) => !v) };
}

export default function Root() {
  const { isDark, toggle } = useTheme();
  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen">
        <header className="border-b border-border bg-background">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 h-14">
            <span className="font-mono text-sm font-medium tracking-tight">
              Agent Eval Orchestrator
            </span>
            <nav className="flex gap-1">
              {navItems.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cn(
                      "rounded-md px-3 py-1.5 text-sm transition-colors",
                      isActive
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
            <button
              type="button"
              onClick={toggle}
              aria-label="切换主题"
              className="ml-auto inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            >
              {isDark ? <Moon className="size-4" /> : <Sun className="size-4" />}
            </button>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">
          <Outlet />
        </main>
      </div>
      <Toaster
        position="top-right"
        theme={isDark ? "dark" : "light"}
        style={{ fontFamily: "var(--font-sans)" }}
        toastOptions={{
          classNames: {
            toast:
              "!bg-popover !text-popover-foreground !border !border-border !rounded-none !shadow-lg",
            description: "!text-muted-foreground",
            actionButton: "!bg-primary !text-primary-foreground !rounded-none",
            cancelButton: "!bg-muted !text-muted-foreground !rounded-none",
            error: "!text-red-600 dark:!text-red-400",
            success: "!text-emerald-600 dark:!text-emerald-400",
          },
        }}
      />
    </QueryClientProvider>
  );
}
