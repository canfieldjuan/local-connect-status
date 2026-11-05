# Recording an install gap

Contract 10 G1-G8's collector-proof revision is accepted at `00e6338`; direct acceptance is retained in
`contracts/10-installed-promotion-acceptance.md`. This implementation is not permission to
merge, deploy LCS or access/write the live evidence store.

The promotion recorder accepts `--install-gap app=manifest.json` for a new gap, or
`--gap-record app=record-id` to retry a retained gap explicitly. These are promotion-only,
mutually exclusive per app. Ordinary promotion commands retain their existing behavior.
The recorder never executes a smoke command or installs a package.

The JSON manifest is a closed object with `version: 2`, `app`, `repo`, `platform: "linux"`,
`candidate_revision`, `last_recorded_promotion_id`, `history_attested_complete: true`, and a
nonempty `unrecorded_installs` array. Each event has `revision`, aware `installed_at`,
`package_sha256`, an absolute-path-to-SHA-256 `program_hashes` map, `receipts`, and
`installed_observation_record_ids`. An event may
also have `package`, an absolute retained-package path. The final event requires that path
and is the actual previous install. Receipt references have exactly `format`, absolute `path`
and `sha256`. Duplicate JSON keys and unsupported fields/formats are refused.

Supported retained receipt adapters:

| `format` | Required evidence |
|---|---|
| `installed-v1` | Closed JSON object: integer `version: 1`, `app`, `repo`, `platform: "linux"`, `installed: true`, `revision`, `installed_at`, `package_sha256`, `program_hashes`. Every identity field must agree with the event. This is an installed observation, not a builder manifest. |
| `ew-package` | Email Watcher package receipt: `source` plus either `package_sha256`/`program_hashes` (root-relative program keys are normalized structurally), or `deb_sha256`/`desktop_sha256`/`engine_sha256`. These two layouts are mutually exclusive. It proves package identity only. |
| `ew-acceptance` | Optional supplemental human evidence: matching `source`, aware `verified_at`, literal `installed_bytes_match: true`, `linux_installed_acceptance: "PASS"`. It carries no installed hashes and cannot provide installed proof, even with a package receipt. |

Collector-witnessed events cite exact retained `installed_observation` IDs. The rows must bind
the same app/repository/platform, complete program-path/hash map and event time interval. Empty
observed revisions and unrelated pin/history errors do not invalidate complete installed hashes.
The recorder checks all observed identities in the predecessor-to-candidate interval, including
returns such as B/C/B. Human attestation cannot omit or reorder them. Consecutive samples of one
event may coalesce; an interior predecessor or earlier candidate identity cannot be discarded.

Events unseen by the collector require a complete `installed-v1` receipt. Each event separately
needs package/source binding through that receipt or `ew-package`. If a retained Debian package
is the sole package evidence, the existing `dpkg-deb` structured field reader must find its full
revision in `X-Built-From`, and its digest/payload must match the event. This is the only supported
package-embedded source layout. Unsupported source metadata refuses; a plain tar needs a receipt.
Package bytes are passed on stdin and are never executed or installed.

The EW layouts were inventoried from retained package/acceptance artifacts before adapter code.
Builder flags, health-only receipts and database verification alone are not installed-byte
proof. Unsupported older layouts fail closed; their presence in a folder does not qualify them.
Tests use public synthetic copies of these layouts, never the original retained documents.

The gap uses the existing `installed_demo` envelope. Its decoder fixes `verdict="pass"`,
empty condition/task IDs, and a closed source without `check`. This means verified history,
not passing smoke or promotion proof. The source hashes the human-reviewed `--artifact` file,
contains the typed gap payload and binds the exact promotion gate IDs. The fallback is derived
from the last event; a separately supplied rollback path must agree with it.

An explicit retry rechecks collector IDs/completeness, receipts, fallback bytes, installed bytes, current gates, artifact
and observer. Reuse the original observation time and inputs. If its exact linked promotion is
already latest, the recorder returns that retained ID. A changed predecessor or input refuses.
A crash after appending a gap may leave an orphan; it cannot affect installed labels. Retain
that record and retry with its ID rather than editing the JSONL file.

All CLI checks and appends use the existing collection lock. The recorder validates every
member of a requested coupled promotion before appending any gap. Read-only rules validate
gap identity/linkage and the same pure collector-proof decision from the historical publication
prefix; they do not reopen receipt paths or run products. Later ticks do not alter that interval.
Previously deployed readers can decode the compatible envelope; they must not be used to
create gap promotions. Actual historical qualification and human recording remain later gates.
