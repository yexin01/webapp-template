import { Show, SignUpButton } from "@clerk/nextjs";
import Link from "next/link";

export default function HomePage() {
  return (
    <section className="hero">
      <h1>Ship your SaaS on a secure foundation</h1>
      <p className="lead">
        Authentication, organizations, Free and Pro billing, and observability — with tenant isolation
        enforced on the server.
      </p>
      <div className="button-row">
        <Show when="signed-in">
          <Link className="button button-primary" href="/dashboard">
            Open dashboard
          </Link>
        </Show>
        <Show when="signed-out">
          <SignUpButton>
            <button type="button" className="button button-primary">
              Get started free
            </button>
          </SignUpButton>
        </Show>
      </div>
    </section>
  );
}
