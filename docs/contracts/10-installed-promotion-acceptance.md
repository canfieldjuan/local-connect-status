# Contract 10 install-gap amendment — operator acceptance

Accepted contract: PR 36 at `6b50df39b053a4c34fa597ae43d2f698b3754da8`.

Direct operator instruction, verbatim:

> I accept the Contract 10 amendment in PR 36 at 6b50df3. Start the recorder implementation in a separate PR, including oversight’s decoder constraint. No merge, LCS cutover, or live records yet.

This records acceptance of G1-G8 without changing the accepted contract text. The implementation PR is stacked on PR 36, which remains unmerged. Oversight’s decoder constraint fixes the gap verdict and forbids condition/check attribution by construction.

Acceptance authorizes isolated recorder implementation and fixture verification only. Neither PR may merge; LCS cutover, live store access, smoke/gap/promotion writes and product actions remain held. Separate direct operator authorization is required for the cutover and recording stages.

## Re-acceptance of the collector-proof revision

Accepted contract: PR 36 at `00e6338c4f1413be382b3e71d4eeb5572ba98215`.

Direct operator reply, verbatim:

> I accept

The immediately preceding assistant message identified PR 36 at `00e6338` as ready for re-acceptance after oversight PASS, a completed connector review with no findings, and zero unresolved threads. This reply accepts that exact revised contract and authorizes the previously assigned PR 37 recorder revision using isolated fixtures. It is not acceptance of PR 37 or authority to merge, deploy, access the live store or write live records.

The earlier acceptance remains historical; this revision governs the implementation. Both PRs remain unmerged to main. LCS cutover and real recording require separate operator authority.
