"use client";

import Link from "next/link";
import { useEffect } from "react";

import { track } from "@/lib/analytics";

type Props = { feature: string; inOrganization?: boolean };

/** The single paywall used wherever a Pro feature is denied (FE-06, BILL-21). */
export function Paywall({ feature, inOrganization = false }: Props) {
  useEffect(() => {
    track("paywall_viewed", { feature });
  }, [feature]);

  return (
    <section className="card paywall" aria-labelledby="paywall-title">
      <h2 id="paywall-title">Upgrade to Pro</h2>
      {inOrganization ? (
        <p>
          Pro features are not available inside organizations yet. Switch to your personal workspace to use
          your Pro plan.
        </p>
      ) : (
        <p>This feature is part of the Pro plan. Upgrade monthly or yearly; you can cancel anytime.</p>
      )}
      {!inOrganization && (
        <Link className="button button-primary" href="/billing">
          See plans
        </Link>
      )}
    </section>
  );
}
