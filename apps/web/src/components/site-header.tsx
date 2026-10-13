import { OrganizationSwitcher, Show, SignInButton, SignUpButton, UserButton } from "@clerk/nextjs";
import Link from "next/link";

export function SiteHeader() {
  return (
    <header className="site-header">
      <nav aria-label="Main" className="nav">
        <Link href="/" className="brand">
          SaaS Template
        </Link>
        <Show when="signed-in">
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
          </ul>
        </Show>
        <div className="nav-actions">
          <Show when="signed-in">
            <OrganizationSwitcher
              afterSelectOrganizationUrl="/dashboard"
              afterSelectPersonalUrl="/dashboard"
            />
            <UserButton />
          </Show>
          <Show when="signed-out">
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
          </Show>
        </div>
      </nav>
    </header>
  );
}
