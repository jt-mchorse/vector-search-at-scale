"""Rendering a comparison the surrounding prose asserts an ordering about.

`scripts/plot_hnsw_frontier.py` selects a knee at full float precision --
``c["mean_recall_at_k"] >= recall_floor`` -- and then explains the selection in
a sentence. Rendering the floor at ``.2f`` beside a ``.3f`` recall made that
sentence state the reverse of the selection (#150)::

    floor=0.9451  knee recall=0.9455  (qualifies)
    -> "Recommended defaults (knee at recall >= 0.95): ... recall=0.946"

``0.946 < 0.95``. A *mismatched* pair is worse than a colliding one: a
collision (``0.950`` against ``0.95``) looks wrong and invites a second look,
while this looks fine and is false. Measured in `prompt-regression-suite#175`
and `ai-app-integration-tests#125` before it was measured here.

Duplicated from `prompt-regression-suite` D-012 and the siblings that followed
it rather than shared: they are separate distributions with no dependency
between them, and manufacturing one so a short formatter could be imported
would be a worse trade than the duplication. Said here so it does not look
accidental.

**What this module deliberately does not cover.** The siblings' rule is about a
*pair* -- two numbers in one sentence, which the reader compares as written.
`plot_hnsw_frontier`'s other branch prints the floor **alone**, above a table of
recalls, and claims no cell reaches it. That is an absolute claim rather than a
relative one, and no fixed width can make it safe: at a floor of ``0.9512``
every fixed width either truncates the number or pads it. That branch uses
`render_exact` instead. The two are mutually exclusive branches, so the
difference in width between them is never visible in one run.
"""

from __future__ import annotations

#: Ceiling on widening. A recall and a recall floor both live in ``[0, 1]``,
#: where the gap between adjacent doubles is at most ~2.2e-16, so 17 decimal
#: places separates any two distinct values in this module's operating region.
#: Unlike `rag-production-kit`'s D-021 -- whose operands are unbounded finite
#: floats -- the ``[0, 1]`` contract here is *enforced* (`--recall-floor` is
#: range-checked and a recall is a proportion), so the `repr` fallback really is
#: the formality it is not there.
COMPARISON_MAX_PLACES = 17


def render_exact(value: float) -> str:
    """Render `value` so it round-trips -- no fixed width, no truncation.

    For a threshold printed *alone* as a claim about a table. ``repr`` gives
    Python's shortest round-tripping form, so ``0.95`` stays ``'0.95'`` (the
    default path is byte-identical) while ``0.9512`` stays ``'0.9512'`` instead
    of becoming the ``0.95`` that made "No grid cell achieves recall >= 0.95"
    false about a table containing a cell at ``0.952``.
    """
    return repr(float(value))


def render_comparison(
    value: float,
    other: float,
    *,
    places: int,
    exact_value: bool = False,
    exact_other: bool = False,
) -> tuple[str, str]:
    """Render two numbers so an ordering stated between them stays visible.

    Widens from `places` only while the two render identically, and always
    returns both sides at the same precision.

    `places` is **required**, not defaulted. Centralising inline formatters onto
    a helper with a hardcoded width silently re-renders every call site that
    disagreed with it -- the regression `llm-eval-harness#252` shipped, and the
    one `prompt-regression-suite#177` hit again the same week when its first new
    caller published a different width.

    Both sides at the same precision is the half that matters here, and this
    repo is the portfolio's clearest case for it: the two sides were at ``.2f``
    and ``.3f``, which is not a collision risk but an *inversion* risk.

    **``exact_value`` / ``exact_other`` mark an operand as a configured
    parameter (#152, D-015)** -- something an operator typed, rather than
    something this repo measured. The loop stops as soon as the two strings
    differ, which makes the ordering visible and says nothing about whether
    either number is the one that was set. Marking an operand widens the pair
    until it reads back as itself, **still at one shared precision**.

    This is :func:`render_exact`'s property, held *inside* the pairwise widening
    rather than beside it, and that distinction was measured rather than
    argued. Composing the two -- ``render_exact`` for the floor and a fixed
    width for the recall -- is the obvious move in this repo because both
    helpers already exist, and it **reintroduces D-014's inversion**: at
    ``floor=0.9512`` against ``recall=0.95124`` it prints "knee at recall >=
    0.9512 ... recall=0.951", which states the reverse of the selection that was
    actually made. A lone claim takes ``render_exact``; a claim with a second
    number beside it takes this.

    **Both flags, symmetric, and that is deliberate.**
    ``llm-eval-harness``' D-029 shipped ``exact_other`` alone on the grounds
    that "``value`` is the measured side at all six call sites" -- a true
    statement about that repo's callers, promoted to a contract, and
    ``prompt-regression-suite``#181 falsified it within the same day with a site
    comparing two configured numbers. Only ``other`` is configured at this
    repo's one call site today. A symmetric signature makes no claim that a
    later caller can prove false, and the arms name which side is which instead.
    """
    if places < 0:
        raise ValueError(f"places must be non-negative; got {places!r}")
    for width in range(places, max(places, COMPARISON_MAX_PLACES) + 1):
        rendered = (f"{value:.{width}f}", f"{other:.{width}f}")
        # Round-tripping is monotone in width -- a wider rendering is at least
        # as close, and the intervals rounding to a given double nest -- so
        # skipping a width cannot skip past a narrower acceptable one.
        if exact_value and float(rendered[0]) != value:
            continue
        if exact_other and float(rendered[1]) != other:
            continue
        # Equal inputs return the narrow rendering unwidened: there is nothing
        # to distinguish. A *marked* operand overrides that, because
        # misreporting the configuration is a false claim whether or not the
        # measurement happens to equal it.
        if value == other or rendered[0] != rendered[1]:
            return rendered
    # `repr` round-trips a double by definition, so it separates two distinct
    # values and reproduces a configured one exactly. The one exit that does not
    # guarantee a shared precision, which was already true before #152.
    return (repr(value), repr(other))
