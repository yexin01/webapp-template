-- A user's Stripe customer can be replaced after Stripe deleted it (BILL-04).
--
-- Stripe deletes a customer and cancels its subscriptions, and cannot create a customer under that ID again, so
-- every checkout of the user was rejected for good. The worker now points billing_customers.stripe_customer_id at
-- a new customer (the user's own row, in place). The subscriptions that ended under the old customer keep its ID
-- as history, so they can no longer reference the one current ID: the reference is dropped. The application stores
-- a subscription only for a customer it has linked (BillingService._sync_customer).

-- squawk-ignore ban-drop-constraint
ALTER TABLE app.subscriptions DROP CONSTRAINT subscriptions_stripe_customer_id_fkey;

GRANT UPDATE (stripe_customer_id) ON app.billing_customers TO app_worker;
