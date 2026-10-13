import { Suspense } from "react";

import { BillingPanel } from "@/components/billing-panel";
import { LoadingState } from "@/components/state-views";

export const metadata = { title: "Billing" };

export default function BillingPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading billing…" />}>
      <BillingPanel />
    </Suspense>
  );
}
