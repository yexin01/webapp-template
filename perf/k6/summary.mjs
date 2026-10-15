// Per-stage results for the arrival-rate scenarios in scenarios.js. Each stage
// is measured over the seconds the run actually covered, so a run that aborts
// (on errors, a 401, or Ctrl+C) neither shows the stage it stopped in as a
// throughput collapse nor reports stages it never reached. No k6 modules here,
// so the API test suite runs this under Node (tests/integration/test_perf_harness.py).

const round = (value, digits = 1) => Math.round(value * 10 ** digits) / 10 ** digits;
const valuesOf = (metrics, name) => (metrics[name] ? metrics[name].values : null);

// A run that is not cut short lasts at least until its last whole iteration is
// due (see lastIterationDue); this absorbs timer rounding.
const TOLERANCE_S = 1;

/**
 * Seconds into the run at which the last whole iteration the profile offers is
 * due. k6 starts iteration k of a ramping-arrival-rate scenario once the offered
 * load reaches k, and ends the scenario after the last one. That is before the
 * planned end when the profile ends in a ramp down and offers a fractional
 * total (PERF-04 at an odd SPIKE_RATE). A constant-arrival-rate scenario runs to
 * its planned end, which is never before this time.
 */
function lastIterationDue(profile) {
  const last = Math.floor(profile.reduce((sum, s) => sum + ((s.from + s.to) / 2) * s.seconds, 0));
  let start = 0;
  let offered = 0;
  for (const { from, to, seconds } of profile) {
    const inStage = ((from + to) / 2) * seconds;
    if (last > 0 && offered + inStage >= last) {
      // x seconds into the stage, it has offered from·x + (to − from)·x²/(2·seconds).
      const need = last - offered;
      const x =
        from === to
          ? need / from
          : (from * seconds - Math.sqrt(Math.max(0, seconds * (from * from * seconds + 2 * need * (to - from))))) /
            (from - to);
      return start + x;
    }
    offered += inStage;
    start += seconds;
  }
  return 0;
}

/**
 * Achieved against offered load, stage by stage.
 *
 * @param profile [{ stage, from, to, seconds }] in run order: the offered rate
 *   ramps linearly from `from` to `to` iterations/s over `seconds`.
 * @param metrics handleSummary's data.metrics, with the per-stage selectors
 *   iteration_starts, http_reqs, http_req_failed, and http_req_duration.
 * @param elapsedSeconds how long the run lasted (data.state.testRunDurationMs / 1000).
 */
export function stageResults(profile, metrics, elapsedSeconds) {
  const planned = profile.reduce((sum, s) => sum + s.seconds, 0);
  const aborted = elapsedSeconds < lastIterationDue(profile) - TOLERANCE_S;
  let start = 0;
  const stages = profile.map(({ stage, from, to, seconds: length }) => {
    const covered = aborted ? Math.min(length, Math.max(0, elapsedSeconds - start)) : length;
    start += length;
    const reached = from + ((to - from) * covered) / length;
    const offered = ((from + reached) / 2) * covered;
    const started = valuesOf(metrics, `iteration_starts{stage:${stage}}`)?.count ?? 0;
    const requests = valuesOf(metrics, `http_reqs{stage:${stage}}`)?.count ?? 0;
    const failed = valuesOf(metrics, `http_req_failed{stage:${stage}}`);
    const latency = valuesOf(metrics, `http_req_duration{stage:${stage}}`);
    return {
      stage,
      status: covered >= length ? "complete" : covered > 0 ? "cut_short" : "not_reached",
      seconds: length,
      covered_seconds: round(covered),
      offered_iterations_per_s: from === to ? to : { from, to },
      offered_iterations: Math.round(offered),
      started_iterations: started,
      achieved_iterations_per_s: covered > 0 ? round(started / covered) : null,
      requests_per_s: covered > 0 ? round(requests / covered) : null,
      delivered: offered > 0 ? round(started / offered, 3) : null,
      error_rate: failed && failed.passes + failed.fails > 0 ? round(failed.rate, 4) : null,
      latency_ms:
        latency && requests > 0
          ? { p50: round(latency.med), p95: round(latency["p(95)"]), p99: round(latency["p(99)"]) }
          : null,
    };
  });
  return { planned_duration_s: planned, aborted, stages };
}
