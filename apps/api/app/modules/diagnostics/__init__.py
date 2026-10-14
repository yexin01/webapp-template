"""Integration diagnostics for operators: each service's configuration, plus a
minimal live check against it (spec §5.2, DEC-006). Probes come from the
modules that own each integration; this module never imports them."""
