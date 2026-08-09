"""CLAIM 6 — entity resolution is reversible.

*北方桥货运* / *Northbridge Forwarding B.V.* / *NORTHBRIDGE FWD BV* / *N. Bridge Forwarding
B.V.* are one party. Transliteration, abbreviation, legal-form suffixes and reader damage, all
at once — and **a merge is sometimes wrong, so it must be reversible with lineage intact.**

The claim is about reversibility, not about accuracy, and that distinction is load-bearing.
There is no labelled set of same-party judgements large enough to derive a merge threshold
honestly, so `contracts/entities/parties.yaml` declares a **chosen** one and the schema refuses
any contract that claims otherwise. What this module can demonstrate is the property that makes
a chosen threshold survivable: every merge can be undone, and every record that pointed at the
merged entity points back where it came from.

Three decisions:

**A merge records what it merged, not just the result.** Storing only the surviving entity
makes an un-merge a guess. The lineage carries every member with the score and the rule that
brought it in, so undoing is arithmetic rather than reconstruction.

**A merge is explained, in a sentence a human can disagree with.** An unexplained merge is one
nobody can audit, and doctrine rule 5 says nothing approves itself.

**Un-merging is total.** Half an un-merge — the entity split, the downstream records left
pointing at a canonical id that no longer exists — is worse than no un-merge, because the
dangling pointer is invisible until somebody follows it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher

from manifest.core.text import Rule, normalise


@dataclass(frozen=True, slots=True)
class MatchRule:
    """One way two names may be recognised as the same party."""

    rule_id: str
    explanation: str
    rules: tuple[Rule, ...]
    weight: Decimal


@dataclass(frozen=True, slots=True)
class Mention:
    """One party name as it appeared on one document."""

    mention_id: str
    name: str
    document: str
    shipment: str


@dataclass(frozen=True, slots=True)
class Match:
    """Why two mentions were judged the same party."""

    left: str
    right: str
    score: Decimal
    rule_id: str
    explanation: str
    #: A candidate is a suggestion for a human. It never merges anything, and it is a separate
    #: field rather than a separate type so that a merge and a suggestion cannot be stored in
    #: one list and then read as the same thing.
    is_candidate: bool = False


@dataclass(frozen=True, slots=True)
class Entity:
    """A resolved party: a canonical id and every mention that resolved into it.

    `entity_id` is derived from the sorted member ids, so the same set of mentions always
    produces the same entity — no counter, no clock, and a re-resolution that reaches the same
    conclusion is byte-identical to the one before it (claim 3's rule, applied here).
    """

    entity_id: str
    canonical_name: str
    members: tuple[str, ...]
    matches: tuple[Match, ...] = field(default_factory=tuple)

    @property
    def merged(self) -> bool:
        return len(self.members) > 1


#: Below this similarity, two normalised names are not even worth a human's attention. Above it
#: and short of identity, they are a **candidate** and never a merge — see `score`.
CANDIDATE_SIMILARITY = 0.88


def score(left: str, right: str, rules: tuple[MatchRule, ...]) -> Match | None:
    """The best-scoring rule under which these two names are the same party, or None.

    **Only exact equality after a rule's normalisations produces a merge.** Similarity does not.

    That was not the first design, and the test that changed it is worth stating: `Hellenic
    Marble SA` and `Hellenic Marine SA` are 89% similar, and a scored near-match merged them.
    Reader damage on a party name — `N0RTHBRIDGE` for `NORTHBRIDGE` — sits in the same
    similarity band as two genuinely different companies, so **no threshold separates them**.
    A resolver that merges on similarity is not being cautious about the threshold; it is
    claiming a distinction that the signal does not contain.

    So a near-match becomes a `candidate`: recorded, explained, offered to a human, and never
    merged by the system. Doctrine rule 5 — nothing approves itself — applied to identity.

    Best rather than first: the rules are ordered by how much they discard, and a pair matching
    under the safest one should be attributed to it. The attribution is what a human reads when
    they disagree with a merge.
    """
    best: Match | None = None
    for rule in rules:
        normalised_left = normalise(left, rule.rules)
        normalised_right = normalise(right, rule.rules)
        if not normalised_left or not normalised_right:
            continue
        if normalised_left != normalised_right:
            continue
        candidate = Match(
            left=left,
            right=right,
            score=rule.weight,
            rule_id=rule.rule_id,
            is_candidate=False,
            explanation=(
                f"{left!r} and {right!r} are identical after {rule.rule_id}: {rule.explanation}"
            ),
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def candidate(left: str, right: str, rules: tuple[MatchRule, ...]) -> Match | None:
    """A near-match: similar enough to be worth a person's attention, never enough to merge.

    Returned separately from `score` so that no caller can accidentally treat one as the other.
    A merge and a suggestion are different acts, and a function that returned both behind one
    type would eventually have one of them used as the other.
    """
    best: Match | None = None
    for rule in rules:
        normalised_left = normalise(left, rule.rules)
        normalised_right = normalise(right, rule.rules)
        if not normalised_left or not normalised_right or normalised_left == normalised_right:
            continue
        ratio = SequenceMatcher(None, normalised_left, normalised_right).ratio()
        if ratio < CANDIDATE_SIMILARITY:
            continue
        suggestion = Match(
            left=left,
            right=right,
            score=Decimal(str(round(ratio, 4))),
            rule_id=rule.rule_id,
            is_candidate=True,
            explanation=(
                f"{left!r} and {right!r} are {ratio:.0%} similar after {rule.rule_id}. That is "
                f"a candidate for a human, not a merge: reader damage and two different "
                f"companies sit in the same similarity band, and no threshold separates them"
            ),
        )
        if best is None or suggestion.score > best.score:
            best = suggestion
    return best


def resolve(
    mentions: tuple[Mention, ...] | list[Mention],
    rules: tuple[MatchRule, ...],
    threshold: Decimal,
) -> tuple[Entity, ...]:
    """Group mentions into entities, by transitive closure over pairs above the threshold.

    Transitive on purpose, and it is the decision with the most risk in it: A matching B and B
    matching C merges A with C even where they score below the threshold against each other.
    That is what catches a transliteration chain — the Chinese form matches the transliteration,
    which matches the abbreviation — and it is also how one bad pair merges two real companies.

    Which is precisely why claim 6 is about **reversibility**. A transitive merge that could not
    be undone would be a design that had to be right; one that can be undone is a design that
    can be corrected, and the second is available while the first is not.
    """
    order = list(mentions)
    parent = {mention.mention_id: mention.mention_id for mention in order}
    evidence: dict[str, list[Match]] = {}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for index, left in enumerate(order):
        for right in order[index + 1 :]:
            match = score(left.name, right.name, rules)
            if match is None or match.score < threshold:
                continue
            root_left, root_right = find(left.mention_id), find(right.mention_id)
            if root_left != root_right:
                parent[root_right] = root_left
            evidence.setdefault(find(left.mention_id), []).append(match)

    grouped: dict[str, list[Mention]] = {}
    for mention in order:
        grouped.setdefault(find(mention.mention_id), []).append(mention)

    entities = []
    for root, members in grouped.items():
        member_ids = tuple(sorted(mention.mention_id for mention in members))
        entities.append(
            Entity(
                entity_id=_entity_id(member_ids),
                # The longest surface form, which is the one most likely to carry the legal
                # form and the full name. Not the first seen: that would make the canonical
                # name depend on document order, and two runs over the same shipments in a
                # different order would disagree about what the company is called.
                canonical_name=max((m.name for m in members), key=lambda name: (len(name), name)),
                members=member_ids,
                matches=tuple(evidence.get(root, ())),
            )
        )
    return tuple(sorted(entities, key=lambda entity: entity.entity_id))


def _entity_id(members: tuple[str, ...]) -> str:
    return hashlib.sha256("\x1f".join(sorted(members)).encode()).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class UnMerge:
    """The result of undoing a merge: the entities that replace it, and what must be re-pointed.

    `repointed` is the half nobody builds. Splitting the entity is easy; finding every
    downstream record that referenced the merged id and sending it to the right successor is
    the work, and a partial answer leaves a dangling pointer that is invisible until somebody
    follows it.
    """

    removed: str
    replacements: tuple[Entity, ...]
    repointed: dict[str, str]


def unmerge(
    entity: Entity,
    references: dict[str, str],
    rules: tuple[MatchRule, ...],
    threshold: Decimal,
    keep_together: tuple[tuple[str, ...], ...] = (),
) -> UnMerge:
    """Undo a merge, re-pointing every downstream reference.

    `references` maps a downstream record id to the mention id it was resolved from — kept
    rather than discarded at merge time, which is what makes this arithmetic rather than
    guesswork. A design that stored only the entity id would have to *re-run* resolution to
    un-merge, and re-running the thing that made the mistake is not a correction.

    `keep_together` lets a human say which members were genuinely one party; everything else
    becomes a singleton. Empty means split completely, which is the honest default: after a
    merge has been disputed, the system is not entitled to a second opinion about which parts
    of it were right.
    """
    grouped = list(keep_together)
    assigned = {member for group in grouped for member in group}
    grouped.extend((member,) for member in entity.members if member not in assigned)

    unknown = assigned - set(entity.members)
    if unknown:
        raise ValueError(
            f"cannot keep {sorted(unknown)} together: they are not members of {entity.entity_id}"
        )

    replacements = tuple(
        Entity(
            entity_id=_entity_id(tuple(sorted(group))),
            canonical_name=max(group, key=lambda name: (len(name), name)),
            members=tuple(sorted(group)),
            matches=tuple(
                match for match in entity.matches if match.left in group and match.right in group
            ),
        )
        for group in grouped
    )

    by_member = {
        member: replacement.entity_id
        for replacement in replacements
        for member in replacement.members
    }
    repointed = {
        record: by_member[mention] for record, mention in references.items() if mention in by_member
    }

    dangling = {record for record, mention in references.items() if mention not in by_member}
    if dangling:
        raise ValueError(
            f"{len(dangling)} downstream record(s) reference a mention this entity does not "
            f"contain: {sorted(dangling)[:5]}. Un-merging with them unresolved would leave a "
            f"pointer to an entity that no longer exists, and a dangling pointer is invisible "
            f"until somebody follows it"
        )
    return UnMerge(removed=entity.entity_id, replacements=replacements, repointed=repointed)
