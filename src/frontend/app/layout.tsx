import type { Metadata } from "next";
import "./globals.css";
import "./auth.css";
import "./documents.css";
import { AuthProvider } from "../components/auth-provider";

export const metadata: Metadata = {
  title: "Digital Memory — Your workspace",
  description: "A personal space for notes, moments, and their sources.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><AuthProvider>{children}</AuthProvider></body></html>;
}
