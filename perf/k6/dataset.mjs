// perf/server.py's synthetic dataset, as scenarios.js addresses it. No k6 modules
// here, so the API test suite runs this under Node and checks it against the
// server's own identifiers (tests/integration/test_perf_harness.py).

// Zero-padded like the server's identifiers; a longer index keeps every digit.
const padded = (index, width) => String(index).padStart(width, "0");

/**
 * User `index`: its token, which only the local perf server accepts, its
 * organization (index % orgs), and whether it is Pro (even indexes).
 */
export function syntheticUser(index, orgs) {
  return {
    token: `perf-token-perf_user_${padded(index, 4)}`,
    org: `org_perf${padded(index % orgs, 3)}`,
    pro: index % 2 === 0,
  };
}

/**
 * PERF-06's customer for the events of `group`: the customers of the odd user
 * indexes below `users` in turn, which the server seeds with no subscription. A
 * deployed preview's mocked Stripe starts empty, so it could not re-read a
 * stored subscription. `users` must not exceed the server's --users, or events
 * name unknown customers.
 */
export function customerFor(group, users) {
  return `cus_perf${padded(2 * (group % Math.floor(users / 2)) + 1, 4)}`;
}
