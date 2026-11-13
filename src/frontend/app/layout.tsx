import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Digital Memory — Your workspace",
  description: "Explore a synthetic collection of notes, moments, and their sources.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
