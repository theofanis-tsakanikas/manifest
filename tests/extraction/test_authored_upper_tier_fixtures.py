"""The two upper-tier adapters map their documented shapes — from AUTHORED fixtures.

Read `tests/extraction/fixtures/AUTHORED.md` first. These fixtures were written from published
schemas, not captured from a service, so what passes here is evidence about the *adapters* and
about the documentation having been read correctly. It is not evidence about the services. The
test names say so, because a reader skimming a green suite should not come away with the larger
claim.

The through-line of both files: **neither of these tiers reports a confidence, and neither is
allowed to appear to.** The document-automation service documents no confidence field anywhere;
a model can be asked for one and the number would be meaningless. Every test below is a way one
of those absences could have quietly become a number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manifest.core.document import Page, ReadDocument, ReaderIdentity, Word, build_line
from manifest.core.geometry import Box, PageSize
from manifest.core.review import Reason, reason_for
from manifest.extraction.aws import bda, llm

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _bda_response() -> dict:
    return _load("authored_document_automation_response.json")


def _bda_document() -> ReadDocument:
    return bda.to_document(
        source_id="SHP00001/bill_of_lading",
        source_digest="abc123",
        response=_bda_response(),
        language="en",
        service_version="2026-08-10",
    )


class TestTheDocumentAutomationAdapterAgainstTheAuthoredShape:
    def test_the_authored_response_maps_to_the_normalised_representation(self) -> None:
        reading = _bda_document()
        assert len(reading.pages) == 1
        assert reading.pages[0].number == 1, "page_index is zero-based; page numbers are not"
        assert [line.text for line in reading.pages[0].lines] == ["GROSS WEIGHT", "27000 KGS"]

    def test_the_page_size_comes_from_the_response_and_not_from_the_caller(self) -> None:
        """Unlike the per-page OCR adapter, this service measures its own raster.

        The caller cannot tell it a size, so the caller cannot tell it a *wrong* size — which
        is one fewer way for a made-up page dimension to end up inside a provenance record.
        """
        assert _bda_document().pages[0].size == PageSize(width=2480, height=3508)

    def test_every_word_carries_no_confidence_rather_than_a_confident_looking_number(
        self,
    ) -> None:
        """The single most important assertion about this adapter.

        The documented schema has no confidence field on a word, a line, an element or a page.
        A `1.0` here would clear every derived threshold in this repository, on every page this
        reader ever touches, and nothing downstream could tell it from a real score.
        """
        words = list(_bda_document().pages[0].words)
        assert words, "the fixture has words"
        assert all(word.confidence is None for word in words)

    def test_a_line_of_unscored_words_is_itself_unscored(self) -> None:
        assert all(line.confidence is None for line in _bda_document().pages[0].lines)

    def test_nothing_this_reader_produces_can_publish_on_its_score(self) -> None:
        """The consequence, asserted rather than left to be inferred from the type."""
        for line in _bda_document().pages[0].lines:
            assert reason_for(line.confidence, threshold=0.5) is Reason.UNSCORED

    def test_a_response_without_word_granularity_is_refused_not_reconstructed(self) -> None:
        """Word granularity is off by default, and line-splitting would invent geometry.

        Splitting `"27000 KGS"` into two words needs a box for each, and there is no box for
        each — so any box produced would be a guess. A guessed box is a provenance record
        pointing somewhere nobody measured, which is claim 2 defeated by a helpful default.
        """
        response = _bda_response()
        del response["text_words"]
        with pytest.raises(bda.ResponseError, match="word granularity"):
            bda.to_document(
                source_id="d",
                source_digest="x",
                response=response,
                language="en",
                service_version="2026-08-10",
            )

    def test_a_page_with_no_rectified_size_is_refused(self) -> None:
        response = _bda_response()
        del response["pages"][0]["asset_metadata"]
        with pytest.raises(bda.ResponseError, match="raster size is unknown"):
            bda.to_document(
                source_id="d",
                source_digest="x",
                response=response,
                language="en",
                service_version="2026-08-10",
            )

    def test_a_word_on_a_page_the_response_never_declared_is_refused(self) -> None:
        """Rather than dropped, which would produce a reading short by an unknown amount."""
        response = _bda_response()
        response["text_words"][0]["locations"]["page_index"] = 7
        with pytest.raises(bda.ResponseError, match="not among the pages"):
            bda.to_document(
                source_id="d",
                source_digest="x",
                response=response,
                language="en",
                service_version="2026-08-10",
            )

    def test_the_documented_list_form_of_locations_is_accepted_too(self) -> None:
        """The published schema shows an object here and a list on elements.

        Accepting both is not laxity: the shape is the vendor's to choose, and an adapter that
        refused one of two documented forms would fail on a response the service may send.
        """
        response = _bda_response()
        for word in response["text_words"]:
            word["locations"] = [word["locations"]]
        assert (
            len(
                list(
                    bda.to_document(
                        source_id="d",
                        source_digest="x",
                        response=response,
                        language="en",
                        service_version="2026-08-10",
                    )
                    .pages[0]
                    .words
                )
            )
            == 4
        )


def _grounding() -> ReadDocument:
    """A tier-0 reading of the same page, with measured geometry and real confidences."""

    def word(text: str, left: float, top: float, confidence: float) -> Word:
        return Word(
            text=text,
            confidence=confidence,
            box=Box(left=left, top=top, width=0.06, height=0.014),
        )

    return ReadDocument(
        source_id="SHP00001/bill_of_lading",
        source_digest="abc123",
        reader=ReaderIdentity(name="local-reference-ocr", version="5.5.0"),
        pages=(
            Page(
                number=1,
                size=PageSize(width=2480, height=3508),
                lines=(
                    build_line([word("27000", 0.08, 0.22, 0.71), word("KGS", 0.15, 0.22, 0.96)]),
                    build_line([word("ΠΕΙΡΑΙΑΣ", 0.08, 0.30, 0.64)]),
                ),
            ),
        ),
    )


class TestTheModelAdapterAgainstTheAuthoredShape:
    def test_a_proposal_found_in_the_tier_zero_reading_gets_that_readings_geometry(self) -> None:
        found = {
            proposal.field: proposal
            for proposal in llm.proposals(
                response=_load("authored_model_reply.json"), grounding=_grounding()
            )
        }
        assert found["gross_weight"].has_provenance
        assert found["gross_weight"].page == 1

    def test_a_greek_value_locates_across_the_accent_that_upper_case_drops(self) -> None:
        """The page prints `ΠΕΙΡΑΙΑΣ`; the model returns `Πειραιάς`. Same port.

        Case folding alone does not reconcile them — Greek loses its accents in upper case as
        orthography, so `ΠΕΙΡΑΙΑΣ`.casefold() is `πειραιασ` while `Πειραιάς`.casefold() keeps
        its `ά`. Without the diacritics rule this correct reading would arrive with no
        provenance and be queued: a right answer converted into review volume.
        """
        found = {
            proposal.field: proposal
            for proposal in llm.proposals(
                response=_load("authored_model_reply.json"), grounding=_grounding()
            )
        }
        assert found["port_of_loading"].has_provenance

    def test_a_value_that_is_not_on_the_page_gets_no_provenance_and_cannot_publish(self) -> None:
        """The fixture's `consignee` appears nowhere in the tier-0 reading.

        This is the invented-value case, and the answer is doctrine rule 7: no provenance, no
        publication, and no approval either — an approver has nothing to approve.
        """
        found = llm.proposals(response=_load("authored_model_reply.json"), grounding=_grounding())
        assert [proposal.field for proposal in llm.unlocated(found)] == ["consignee"]

    def test_the_box_is_never_taken_from_the_model_even_when_offered(self) -> None:
        """A model asked for coordinates returns plausible ones. Plausible is the problem."""
        reply = _load("authored_model_reply.json")
        reply["output"]["message"]["content"][0]["text"] = json.dumps(
            {"gross_weight": {"value": "27000 KGS", "page": 9, "box": [0.9, 0.9, 0.05, 0.01]}}
        )
        found = llm.proposals(response=reply, grounding=_grounding())
        assert found[0].page == 1, "the page comes from the reading that measured it"
        assert found[0].box == Box.hull([word.box for word in _grounding().pages[0].lines[0].words])

    @pytest.mark.parametrize(
        "key", ["confidence", "score", "certainty", "probability", "logprob", "CONFIDENCE"]
    )
    def test_a_self_reported_score_is_refused_loudly_rather_than_ignored(self, key: str) -> None:
        """Ignoring it would let a prompt change introduce one and nobody notice.

        The number is not a measured frequency over a labelled distribution; it is a token the
        prompt made likely. Entering claim 1's derivation it would be indistinguishable from a
        score that means something.
        """
        reply = _load("authored_model_reply.json")
        reply["output"]["message"]["content"][0]["text"] = json.dumps(
            {"gross_weight": {"value": "27000 KGS", key: 0.97}}
        )
        with pytest.raises(llm.ResponseError, match="self-reported score"):
            llm.proposals(response=reply, grounding=_grounding())

    def test_a_reply_cut_off_at_the_token_limit_is_refused_on_the_flag(self) -> None:
        """A prefix of valid JSON is still valid JSON, with fields missing and nothing saying so."""
        reply = _load("authored_model_reply.json")
        reply["stopReason"] = "max_tokens"
        with pytest.raises(llm.ResponseError, match="token limit"):
            llm.proposals(response=reply, grounding=_grounding())

    def test_a_reply_that_is_not_json_is_refused_rather_than_repaired(self) -> None:
        reply = _load("authored_model_reply.json")
        reply["output"]["message"]["content"][0]["text"] = "Here is the weight: 27000 KGS"
        with pytest.raises(llm.ResponseError, match="not JSON"):
            llm.proposals(response=reply, grounding=_grounding())

    def test_a_proposal_carries_no_confidence_field_at_all(self) -> None:
        """Not `None` — absent. There is nowhere for one to be added without this test failing."""
        found = llm.proposals(response=_load("authored_model_reply.json"), grounding=_grounding())
        assert not hasattr(found[0], "confidence")
