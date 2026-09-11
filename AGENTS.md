# local-connect-status

Reporting for the Local Connect apps. This repository owns the catalogue, the collector, the
evidence rules, the dashboard and its own tests. It does not own the products.

- Never modify a product repository from here. Read them through the bare mirrors in
  `.cache/mirrors`; verify against trees extracted at exact revisions.
- Evidence is written only by runners and `scripts/record_observation.py`. Never edit
  `data/records.jsonl` or anything under `site/` by hand.
- A status label must be derivable from records by `lcstatus/rules.py`. If you want the
  dashboard to say something new, add a condition and a check to `catalogue.json`; do not
  add prose that asserts it.
- A commit title, a contract document, or a substring in source is a hint, never proof.
- Do not run anything that needs the GPU, sends mail or invitations, or touches a developer's
  checkout.
