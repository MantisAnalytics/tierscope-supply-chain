import { Moon, Sun, UserRound } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useOpsSummary } from "@/api/ops";
import { useStats } from "@/api/queries";
import { Logo } from "@/components/Logo";
import { MetaLogo } from "@/components/MetaLogo";
import { Button } from "@/components/ui/button";
import { rememberedSearch } from "@/features/network/view";
import { cn } from "@/lib/utils";
import { useAuthor } from "./author";
import { GlobalSearch } from "./GlobalSearch";
import { useTheme } from "./theme";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/agent", label: "Agent" },
  { to: "/reports", label: "Reports" },
  { to: "/data", label: "Data" },
];

/** The Agent item: a purple-to-white gradient button with the Meta logo; the label sits on the purple end. */
const AGENT = ({ isActive }: { isActive: boolean }) =>
  cn(
    "mx-1 flex items-center gap-1.5 rounded-md bg-gradient-to-r from-agent-from from-45% to-agent-to py-1.5 pr-2 pl-2.5 text-sm font-semibold text-agent-fg shadow-sm transition-[filter] hover:brightness-110",
    isActive && "ring-2 ring-brand ring-offset-1 ring-offset-surface",
  );

function Count({ n, label }: { n: number | undefined; label: string }) {
  if (n === undefined || n <= 0) return null;
  return (
    <span className="rounded-full bg-brand px-1.5 text-[11px] leading-4 font-semibold text-brand-fg tabular" aria-label={`${n} ${label}`}>
      {n}
    </span>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const { theme, toggle } = useTheme();
  const { author, editAuthor } = useAuthor();
  const { data: stats } = useStats();
  const pending = stats ? Object.values(stats.proposals).reduce((a, b) => a + b, 0) : undefined;
  const { data: ops } = useOpsSummary();
  useLocation(); // re-render on navigation so the Overview link carries the last view

  return (
    <div className="flex h-full min-w-[1280px] flex-col">
      <header className="chrome flex h-14 shrink-0 items-center gap-6 border-b px-5">
        <NavLink to="/" className="flex items-center gap-2.5" aria-label="Tierscope by Mantis Analytics, home">
          <Logo className="size-7" />
          <span className="flex items-baseline gap-1.5">
            <span className="text-[17px] font-semibold tracking-tight">Tierscope</span>
            <span className="text-xs text-muted">by Mantis Analytics</span>
          </span>
        </NavLink>
        <nav aria-label="Main" className="flex items-center gap-1">
          {NAV.map((item) =>
            item.to === "/agent" ? (
              <NavLink key={item.to} to={item.to} className={AGENT}>
                <MetaLogo className="size-4" />
                {item.label}
                <Count n={ops?.agent_needs} label="disruptions waiting for you" />
              </NavLink>
            ) : (
              <NavLink
                key={item.to}
                to={item.to === "/" ? `/${rememberedSearch()}` : item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-muted transition-colors hover:text-fg",
                    isActive && "bg-surface-2 font-medium text-fg",
                  )
                }
              >
                {item.label}
                {item.to === "/data" && <Count n={pending} label="pending proposals" />}
              </NavLink>
            ),
          )}
        </nav>
        <GlobalSearch className="ml-auto w-80" />
        <Button variant="ghost" size="icon" onClick={toggle} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
          {theme === "dark" ? <Sun /> : <Moon />}
        </Button>
        <Button variant="outline" size="sm" onClick={editAuthor} title="Name recorded with your changes">
          <UserRound />
          {author ?? "Set name"}
        </Button>
      </header>
      <main className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
