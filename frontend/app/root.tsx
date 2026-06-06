import { NavLink, Outlet } from "react-router-dom";
import { QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { queryClient } from "@/lib/query";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "任务", end: true },
  { to: "/create", label: "新建任务" },
  { to: "/workers", label: "机器" },
];

export default function Root() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen">
        <header className="border-b border-white/10 bg-white/[0.02]">
          <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
            <span className="text-sm font-semibold tracking-wide text-indigo-300">
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
                      "rounded-md px-3 py-1.5 text-sm transition",
                      isActive ? "bg-white/10 text-white" : "text-slate-400 hover:text-slate-100",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-8">
          <Outlet />
        </main>
      </div>
      <Toaster richColors position="top-right" theme="dark" />
    </QueryClientProvider>
  );
}
