# Recording an install gap

Contract 10 G1-G8 is accepted at `6b50df3`; the direct acceptance is retained in
`contracts/10-installed-promotion-acceptance.md`. This implementation is not permission to
merge, deploy LCS or access/write the live evidence store.

The promotion recorder accepts `--install-gap app=manifest.json` for a new gap, or
`--gap-record app=record-id` to retry a retained gap explicitly. These are promotion-only,
mutually exclusive per app. Ordinary promotion commands retain their existing behavior.
The recorder never executes a smoke command or installs a package.

The JSON manifest is a closed object with `version: 1`, `app`, `repo`, `platform: "linux"`,
`candidate_revision`, `last_recorded_promotion_id`, `history_attested_complete: true`, and a
nonempty `unrecorded_installs` array. Each event has `revision`, aware `installed_at`,
`package_sha256`, an absolute-path-to-SHA-256 `program_hashes` map, and `receipts`. An event may
also have `package`, an absolute retained-package path. The final event requires that path
and is the actual previous install. Receipt references have exactly `format`, absolute `path`
and `sha256`. Duplicate JSON keys and unsupported fields/formats are refused.

Supported retained receipt adapters:

| `format` | Required evidence |
|---|---|
| `installed-v1` | Closed JSON object: integer `version: 1`, `app`, `repo`, `platform: "linux"`, `installed: true`, `revision`, `installed_at`, `package_sha256`, `program_hashes`. Every identity field must agree with the event. This is an installed observation, not a builder manifest. |
| `ew-package` | Email Watcher package receipt: `source` plus either `package_sha256`/`program_hashes` (root-relative program keys are normalized structurally), or `deb_sha256`/`desktop_sha256`/`engine_sha256`. These two layouts are mutually exclusive. It proves package identity only. |
| `ew-acceptance` | Email Watcher acceptance receipt: matching `source`, aware `verified_at`, literal `installed_bytes_match: true`, `linux_installed_acceptance: "PASS"`. Requires a matching `ew-package` receipt in the same event. Other historical acceptance metadata is ignored. |

The EW layouts were inventoried from retained package/acceptance artifacts before adapter code.
Builder flags, health-only receipts and database verification alone are not installed-byte
proof. Unsupported older layouts fail closed; their presence in a folder does not qualify them.
Tests use public synthetic copies of these layouts, never the original retained documents.

The gap uses the existing `installed_demo` envelope. Its decoder fixes `verdict="pass"`,
empty condition/task IDs, and a closed source without `check`. This means verified history,
not passing smoke or promotion proof. The source hashes the human-reviewed `--artifact` file,
contains the typed gap payload and binds the exact promotion gate IDs. The fallback is derived
from the last event; a separately supplied rollback path must agree with it.

An explicit retry rechecks receipts, fallback bytes, installed bytes, current gates, artifact
and observer. Reuse the original observation time and inputs. If its exact linked promotion is
already latest, the recorder returns that retained ID. A changed predecessor or input refuses.
A crash after appending a gap may leave an orphan; it cannot affect installed labels. Retain
that record and retry with its ID rather than editing the JSONL file.

All CLI checks and appends use the existing collection lock. The recorder validates every
member of a requested coupled promotion before appending any gap. Read-only rules validate
gap identity/linkage from stored records; they do not reopen receipt paths or run products.
Previously deployed readers can decode the compatible envelope; they must not be used to
create gap promotions. Actual historical qualification and human recording remain later gates.
