# Allowed facts for the root sketch of `New.grh`

The target is `DirichletLNonvanishingRightOfHalf`: for every positive modulus `q`, Dirichlet character `χ` modulo `q`, and complex `ρ` with `Re ρ > 1/2`, the value `L(ρ, χ)` is nonzero unless `χ` is principal and `ρ = 1`.

The reviewer may use the following prerequisite, which is already proved in `src/New/Endgame/OneSidedCriterion.lean`:

- `entireL_ne_zero_of_primeError_le`: for a modulus `M`, a unit residue class `a` modulo `M`, and `β > 1/2`, suppose every prime `d` in class `a` is `7 mod 8` and coprime to `M`, and for each such `d` the one-sided error `primeError d M x` is eventually at most `x^(β-1)`. Then the entire Dirichlet L-function attached to any character modulo `M` has no zeros with real part greater than `β`.

The reviewer may also use standard facts about changing a Dirichlet character to a multiple of its modulus: the resulting L-function differs by finitely many Euler factors, which do not create zeros in `Re ρ > 1/2` away from the stated pole; and the elementary arithmetic of the residue class `9240 q - 1` modulo `18480 q`.

The one-sided prime error bound `eventually_primeError_le_rpow`, the main inequality `star_inequality`, and the target `New.grh` are **not** allowed facts. If needed, state their necessary content as gaps, with all parameters and hypotheses.
