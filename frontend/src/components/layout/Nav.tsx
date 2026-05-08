"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useWsStatus } from "@/hooks/useWebSocket";
import clsx from "clsx";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/history", label: "History" },
  { href: "/settings", label: "Settings" },
];

export function Nav() {
  const pathname = usePathname();
  const connected = useWsStatus();

  return (
    <header className="border-b border-gray-800 bg-gray-950 sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between">
        <div className="flex items-center gap-6">
          <span className="font-semibold text-white tracking-tight">
            Signal Bot
          </span>
          <nav className="flex items-center gap-1">
            {links.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className={clsx(
                  "px-3 py-1.5 rounded-lg text-sm transition-colors",
                  pathname === l.href
                    ? "bg-gray-800 text-white"
                    : "text-gray-400 hover:text-gray-100 hover:bg-gray-800/60"
                )}
              >
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span
            className={clsx(
              "w-2 h-2 rounded-full",
              connected ? "bg-emerald-400" : "bg-gray-600"
            )}
          />
          <span className="text-gray-500">
            {connected ? "Live" : "Connecting..."}
          </span>
        </div>
      </div>
    </header>
  );
}
