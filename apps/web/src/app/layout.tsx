import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AnalyticsProvider } from "@/lib/analytics-provider";
import { SiteHeader } from "@/ui/site-header";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "SaaS Template", template: "%s · SaaS Template" },
  description: "Open-source SaaS starter: auth, organizations, billing, and observability.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <ClerkProvider signInUrl="/sign-in" signUpUrl="/sign-up" afterSignOutUrl="/">
      <html lang="en">
        <body>
          <a href="#main" className="skip-link">
            Skip to content
          </a>
          <SiteHeader />
          <main id="main" className="container" tabIndex={-1}>
            {children}
          </main>
          <AnalyticsProvider />
        </body>
      </html>
    </ClerkProvider>
  );
}
