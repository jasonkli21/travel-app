import type { Metadata } from "next";
import AuthControls from "../components/auth-controls";
import "./globals.css";

export const metadata: Metadata = {
  title: "Personal Travel",
  description: "Local-first personal travel planner"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body><AuthControls />{children}</body>
    </html>
  );
}
