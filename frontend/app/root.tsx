import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { Toaster } from "sonner";
import {
  CirclePlus,
  ClipboardList,
  HardDrive,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Shield,
  Sun,
  Users,
  type LucideIcon,
} from "lucide-react";
import { currentUser, hasPermission, logout } from "@/lib/auth";
import { queryClient } from "@/lib/query";
import { cn } from "@/lib/utils";

type NavItem = {
  to: string;
  label: string;
  permission: string;
  icon: LucideIcon;
  end?: boolean;
};

const baseNavItems: NavItem[] = [
  { to: "/", label: "任务", end: true, permission: "tasks.read_own", icon: ClipboardList },
  { to: "/create", label: "新建任务", permission: "tasks.create", icon: CirclePlus },
  { to: "/workers", label: "机器", permission: "workers.read", icon: HardDrive },
];

const adminNavItems: NavItem[] = [
  { to: "/users", label: "用户", permission: "users.manage", icon: Users },
  { to: "/groups", label: "组", permission: "groups.manage", icon: Shield },
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

function useSidebar() {
  const [isCollapsed, setIsCollapsed] = useState(() => {
    try {
      return localStorage.getItem("sidebar") === "collapsed";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem("sidebar", isCollapsed ? "collapsed" : "expanded");
    } catch {}
  }, [isCollapsed]);

  return { isCollapsed, toggle: () => setIsCollapsed((value) => !value) };
}

export default function Root() {
  const { isDark, toggle } = useTheme();
  return (
    <QueryClientProvider client={queryClient}>
      <AuthedShell isDark={isDark} toggleTheme={toggle} />
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

function AuthedShell({ isDark, toggleTheme }: { isDark: boolean; toggleTheme: () => void }) {
  const { isCollapsed, toggle } = useSidebar();
  const userQuery = useQuery({ queryKey: ["me"], queryFn: currentUser, retry: false });
  const user = userQuery.data;
  const navItems = [...baseNavItems, ...adminNavItems].filter((item) =>
    hasPermission(user, item.permission),
  );

  if (userQuery.isLoading) {
    return (
      <div className="min-h-screen">
        <main className="mx-auto max-w-6xl px-6 py-8 text-sm text-muted-foreground">加载中…</main>
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-[#2b2b2b] bg-[#1b1b1b] text-zinc-200 shadow-xl transition-[width] duration-200",
          isCollapsed ? "w-14" : "w-64",
        )}
      >
        <div className="flex h-16 items-center gap-3 border-b border-[#2b2b2b] px-3">
          {!isCollapsed && (
            <span className="min-w-0 flex-1 truncate font-mono text-base font-semibold tracking-normal text-white">
              Agent Eval Orchestrator
            </span>
          )}
          <button
            type="button"
            onClick={toggle}
            title={isCollapsed ? "展开抽屉" : "折叠抽屉"}
            aria-label={isCollapsed ? "展开抽屉" : "折叠抽屉"}
            className="inline-flex size-8 shrink-0 items-center justify-center rounded-md text-zinc-300 transition-colors hover:bg-[#2a2a2a] hover:text-white"
          >
            {isCollapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-2 py-4">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                title={isCollapsed ? item.label : undefined}
                className={({ isActive }) =>
                  cn(
                    "flex h-10 items-center gap-3 rounded-md px-2 text-sm transition-colors",
                    isCollapsed && "justify-center px-0",
                    isActive
                      ? "bg-[#303030] text-white"
                      : "text-zinc-300 hover:bg-[#262626] hover:text-white",
                  )
                }
              >
                <Icon className="size-4 shrink-0" />
                {!isCollapsed && <span className="truncate">{item.label}</span>}
              </NavLink>
            );
          })}
        </nav>

        <div className="border-t border-[#2b2b2b] p-2">
          {!isCollapsed && (
            <div className="mb-2 truncate px-2 text-xs text-zinc-400">{user?.username}</div>
          )}
          <div className={cn("flex gap-1", isCollapsed && "flex-col")}>
            <button
              type="button"
              onClick={toggleTheme}
              title="切换主题"
              aria-label="切换主题"
              className="inline-flex size-9 items-center justify-center rounded-md text-zinc-300 transition-colors hover:bg-[#2a2a2a] hover:text-white"
            >
              {isDark ? <Moon className="size-4" /> : <Sun className="size-4" />}
            </button>
            <button
              type="button"
              onClick={logout}
              title="退出登录"
              aria-label="退出登录"
              className="inline-flex size-9 items-center justify-center rounded-md text-zinc-300 transition-colors hover:bg-[#2a2a2a] hover:text-white"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>

      <main
        className={cn(
          "min-h-screen transition-[padding-left] duration-200",
          isCollapsed ? "pl-14" : "pl-64",
        )}
      >
        <div className="mx-auto max-w-6xl px-6 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
