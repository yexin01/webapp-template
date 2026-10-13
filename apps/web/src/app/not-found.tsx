import Link from "next/link";

export const metadata = { title: "Page not found" };

export default function NotFound() {
  return (
    <section className="state state-error">
      <h1>Page not found</h1>
      <p>The page you are looking for does not exist or has moved.</p>
      <div className="button-row">
        <Link className="button button-primary" href="/">
          Go home
        </Link>
        <Link className="button" href="/dashboard">
          Dashboard
        </Link>
      </div>
    </section>
  );
}
