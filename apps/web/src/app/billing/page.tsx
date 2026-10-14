import { Suspense } from "react";

import { BillingPanel } from "@/features/billing";
import { LoadingState } from "@/ui/state-views";

export const metadata = { title: "Billing" };

export default function BillingPage() {
  return (
    <Suspense fallback={<LoadingState label="Loading billing…" />}>
      <BillingPanel />
    </Suspense>
  );
}
