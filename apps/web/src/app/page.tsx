import { SignUpButton } from "@clerk/nextjs";
import Link from "next/link";

import { SignedInOnly, SignedOutOnly } from "@/ui/signed-in-state";

export default function HomePage() {
  return (
    <section className="hero">
      <h1>Ship your SaaS on a secure foundation</h1>
      <p className="lead">
        Authentication, organizations, Free and Pro billing, and observability — with tenant isolation
        enforced on the server.
      </p>
      <div className="button-row">
        <SignedInOnly>
          <Link className="button button-primary" href="/dashboard">
            Open dashboard
          </Link>
          <Link className="button" href="/integrations">
            Check integrations
          </Link>
        </SignedInOnly>
        <SignedOutOnly>
          <SignUpButton>
            <button type="button" className="button button-primary">
              Get started free
            </button>
          </SignUpButton>
        </SignedOutOnly>
      </div>
    </section>
  );
}
