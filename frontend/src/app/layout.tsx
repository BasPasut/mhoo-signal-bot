import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/layout/Nav";

export const metadata: Metadata = {
  title: "Signal Bot",
  description: "Binance futures trading signal bot",
  viewport: "width=device-width, initial-scale=1",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-gray-950 text-gray-100 min-h-screen">
        <Nav />
        <main className="max-w-7xl mx-auto px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
