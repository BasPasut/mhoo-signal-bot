"use client";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useWsStatus } from "@/hooks/useWebSocket";
import clsx from "clsx";

const links = [
  { href: "/",            label: "Signals",     short: "Signals"  },
  { href: "/history",     label: "History",     short: "History"  },
  { href: "/performance", label: "Performance", short: "Perf"     },
  { href: "/settings",    label: "Settings",    short: "Settings" },
];

export function Nav() {
  const pathname  = usePathname();
  const connected = useWsStatus();

  return (
    <header className="border-b border-gray-800/80 bg-gray-950/95 backdrop-blur sticky top-0 z-50">
      <div className="max-w-4xl mx-auto px-4 h-14 flex items-center justify-between gap-4">

        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 shrink-0">
          <Image
            src="/assets/logo.png"
            alt="Mhoo Signal Bot"
            width={28}
            height={28}
            className="rounded-full"
            priority
          />
          <span className="hidden sm:block font-semibold text-white text-sm tracking-tight">
            Mhoo
          </span>
        </Link>

        {/* Nav links — full labels on sm+, short on xs */}
        <nav className="flex items-center gap-0.5 flex-1 justify-center">
          {links.map(l => {
            const active = pathname === l.href;
            return (
              <Link
                key={l.href}
                href={l.href}
                className={clsx(
                  "px-3 py-1.5 rounded-lg text-sm transition-colors whitespace-nowrap",
                  active
                    ? "bg-gray-800 text-white font-medium"
                    : "text-gray-500 hover:text-gray-200 hover:bg-gray-800/60"
                )}
              >
                <span className="hidden sm:inline">{l.label}</span>
                <span className="sm:hidden">{l.short}</span>
              </Link>
            );
          })}
        </nav>

        {/* Live status */}
        <div className="flex items-center gap-1.5 shrink-0">
          <span className={clsx(
            "w-2 h-2 rounded-full transition-colors",
            connected ? "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.5)]" : "bg-gray-700"
          )} />
          <span className="text-xs text-gray-500 hidden sm:block">
            {connected ? "Live" : "Offline"}
          </span>
        </div>

      </div>
    </header>
  );
}
