import { OrganizationSwitcher, SignInButton, SignUpButton, UserButton } from "@clerk/nextjs";
import Link from "next/link";

import { OperatorOnly } from "./operator-only";
import { SignedInOnly, SignedOutOnly, SignInUnavailable } from "./signed-in-state";

export function SiteHeader() {
  return (
    <header className="site-header">
      <nav aria-label="Main" className="nav">
        <Link href="/" className="brand">
          SaaS Template
        </Link>
        <SignedInOnly>
          <ul className="nav-links">
            <li>
              <Link href="/dashboard">Dashboard</Link>
            </li>
            <li>
              <Link href="/pro">Pro feature</Link>
            </li>
            <li>
              <Link href="/billing">Billing</Link>
            </li>
            <OperatorOnly>
              <li>
                <Link href="/integrations">Integrations</Link>
              </li>
            </OperatorOnly>
          </ul>
        </SignedInOnly>
        <div className="nav-actions">
          <SignedInOnly>
            <OrganizationSwitcher
              afterSelectOrganizationUrl="/dashboard"
              afterSelectPersonalUrl="/dashboard"
            />
            <UserButton />
          </SignedInOnly>
          <SignedOutOnly>
            <SignInButton>
              <button type="button" className="button">
                Sign in
              </button>
            </SignInButton>
            <SignUpButton>
              <button type="button" className="button button-primary">
                Sign up
              </button>
            </SignUpButton>
          </SignedOutOnly>
          <SignInUnavailable />
        </div>
      </nav>
    </header>
  );
}
