-- Billing reconciliation bookkeeping (BILL-19).
--
-- The scheduled reconcile-billing job checks a bounded batch of customers per
-- run, least recently checked first, so a run stays within platform time limits
-- at any number of customers and every customer is still checked in turn.
-- Webhook events flagged as needing reconciliation are found by customer.

ALTER TABLE app.billing_customers ADD COLUMN reconciled_at timestamptz;
CREATE INDEX billing_customers_reconcile_idx ON app.billing_customers (reconciled_at NULLS FIRST, user_id);

CREATE INDEX billing_events_flagged_idx ON app.billing_events (stripe_customer_id)
  WHERE status = 'needs_reconciliation';

-- The worker may record when it checked a customer, and nothing else about it.
GRANT UPDATE (reconciled_at) ON app.billing_customers TO app_worker;
