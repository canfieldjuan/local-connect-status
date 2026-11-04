# Contract 10 install-gap amendment — operator acceptance

Accepted contract: PR 36 at `6b50df39b053a4c34fa597ae43d2f698b3754da8`.

Direct operator instruction, verbatim:

> I accept the Contract 10 amendment in PR 36 at 6b50df3. Start the recorder implementation in a separate PR, including oversight’s decoder constraint. No merge, LCS cutover, or live records yet.

This records acceptance of G1-G8 without changing the accepted contract text. The implementation PR is stacked on PR 36, which remains unmerged. Oversight’s decoder constraint fixes the gap verdict and forbids condition/check attribution by construction.

Acceptance authorizes isolated recorder implementation and fixture verification only. Neither PR may merge; LCS cutover, live store access, smoke/gap/promotion writes and product actions remain held. Separate direct operator authorization is required for the cutover and recording stages.
