import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { Toaster } from "sonner";
import {
  CirclePlus,
  ClipboardList,
  ChevronLeft,
  ChevronDown,
  ChevronUp,
  HardDrive,
  LogOut,
  Menu,
  Moon,
  Shield,
  Sun,
  UserRound,
  UsersRound,
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
  { to: "/", label: "查看任务", end: true, permission: "tasks.read_own", icon: ClipboardList },
  { to: "/create", label: "新建任务", permission: "tasks.create", icon: CirclePlus },
  { to: "/workers", label: "机器管理", permission: "workers.read", icon: HardDrive },
];

const permissionNavItems: NavItem[] = [
  { to: "/users", label: "用户管理", permission: "users.manage", icon: UserRound },
  { to: "/groups", label: "用户组管理", permission: "groups.manage", icon: UsersRound },
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
  const location = useLocation();
  const userQuery = useQuery({ queryKey: ["me"], queryFn: currentUser, retry: false });
  const user = userQuery.data;
  const navItems = baseNavItems.filter((item) =>
    hasPermission(user, item.permission),
  );
  const permissionItems = permissionNavItems.filter((item) => hasPermission(user, item.permission));
  const permissionEntry = permissionItems[0];
  const isPermissionActive = permissionItems.some((item) =>
    location.pathname.startsWith(item.to),
  );
  const [permissionOpen, setPermissionOpen] = useState(isPermissionActive);

  useEffect(() => {
    if (isPermissionActive) {
      setPermissionOpen(true);
    }
  }, [isPermissionActive]);

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
          "fixed inset-y-0 left-0 z-40 flex flex-col overflow-x-hidden whitespace-nowrap border-r border-[#2b2b2b] bg-[#1b1b1b] text-zinc-200 shadow-xl transition-[width] duration-200",
          isCollapsed ? "w-16" : "w-64",
        )}
      >
        <div
          className={cn(
            "flex h-12 items-center border-b border-[#2b2b2b] px-2",
            isCollapsed ? "justify-center" : "justify-end",
          )}
        >
          {isCollapsed ? (
            <button
              type="button"
              onClick={toggle}
              title="展开抽屉"
              aria-label="展开抽屉"
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-full text-zinc-300 transition-colors hover:bg-[#2a2a2a] hover:text-white"
            >
              <Menu className="size-4" />
            </button>
          ) : (
            <button
              type="button"
              onClick={toggle}
              title="折叠抽屉"
              aria-label="折叠抽屉"
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-full text-zinc-300 transition-colors hover:bg-[#2a2a2a] hover:text-white"
            >
              <ChevronLeft className="size-4" />
            </button>
          )}
        </div>

        <nav className="flex-1 space-y-[10px] overflow-y-auto px-2 py-[10px]">
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
          {permissionEntry && (
            <div className="space-y-[10px]">
              <button
                type="button"
                onClick={() => {
                  if (isCollapsed) {
                    toggle();
                    setPermissionOpen(true);
                    return;
                  }
                  setPermissionOpen((value) => !value);
                }}
                title={isCollapsed ? "权限控制" : undefined}
                className={cn(
                  "flex h-10 w-full items-center gap-3 rounded-md px-2 text-left text-sm transition-colors",
                  isCollapsed && "justify-center px-0",
                  isPermissionActive
                    ? "bg-[#303030] text-white"
                    : "text-zinc-300 hover:bg-[#262626] hover:text-white",
                )}
              >
                <Shield className="size-4 shrink-0" />
                {!isCollapsed && <span className="truncate">权限控制</span>}
                {!isCollapsed &&
                  (permissionOpen ? (
                    <ChevronUp className="ml-auto size-4 shrink-0" />
                  ) : (
                    <ChevronDown className="ml-auto size-4 shrink-0" />
                  ))}
              </button>
              {!isCollapsed && permissionOpen && (
                <div className="space-y-[10px] pl-7">
                  {permissionItems.map((item) => {
                    const Icon = item.icon;
                    return (
                      <NavLink
                        key={item.to}
                        to={item.to}
                        className={({ isActive }) =>
                          cn(
                            "flex h-8 items-center gap-2 rounded-md px-2 text-xs transition-colors",
                            isActive
                              ? "bg-[#303030] text-white"
                              : "text-zinc-400 hover:bg-[#262626] hover:text-white",
                          )
                        }
                      >
                        <Icon className="size-3.5 shrink-0" />
                        <span className="truncate">{item.label}</span>
                      </NavLink>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </nav>

        <div className="border-t border-[#2b2b2b] p-2">
          <div
            className={cn(
              "flex items-center gap-2",
              isCollapsed ? "justify-center" : "justify-between",
            )}
          >
            {!isCollapsed && (
              <div className="min-w-0 truncate px-2 text-sm text-zinc-300">{user?.username}</div>
            )}
            <button
              type="button"
              onClick={logout}
              title="退出登录"
              aria-label="退出登录"
              className="inline-flex size-8 items-center justify-center rounded-full text-zinc-300 transition-colors hover:bg-[#2a2a2a] hover:text-white"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>

      <main
        className={cn(
          "min-h-screen transition-[padding-left] duration-200",
          isCollapsed ? "pl-16" : "pl-64",
        )}
      >
        <div
          className={cn(
            "fixed top-0 z-50 flex h-12 items-center border-b border-border bg-background transition-[left,width] duration-200",
            isCollapsed ? "left-16 w-[calc(100%-4rem)] px-6" : "left-64 w-[calc(100%-16rem)] px-6",
          )}
        >
          <div className="font-mono text-sm font-semibold tracking-normal text-muted-foreground">
            Agent Eval Orchestrator
          </div>
          <button
            type="button"
            onClick={toggleTheme}
            title="切换主题"
            aria-label="切换主题"
            className="ml-auto inline-flex size-8 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            {isDark ? <Moon className="size-4" /> : <Sun className="size-4" />}
          </button>
        </div>
        <div className="mx-auto max-w-6xl px-6 pb-8 pt-18">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
