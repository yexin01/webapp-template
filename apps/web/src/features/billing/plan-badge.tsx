export function PlanBadge({ plan }: { plan: "free" | "pro" }) {
  return (
    <span className={`badge badge-${plan}`}>
      <span className="visually-hidden">Current plan: </span>
      {plan === "pro" ? "Pro" : "Free"}
    </span>
  );
}
