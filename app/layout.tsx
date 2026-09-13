import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Axiom — Agent Control Room",
  description: "A personal control room for orchestrating AI agents.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
