# These fixtures are **authored**, not captured

Every JSON file in this directory was written by hand from the *documented* response schema.
**No call has been made to any AWS service from this repository, and no response has ever been captured.**
Each fixture names its source document and the date that document was read, in its
own `_note`, because "the documentation" is a moving target and a fixture that does not say
which version it was authored from cannot be checked against a later one.

| Fixture | Documented shape | Read |
|---|---|---|
| `authored_ocr_response.json` | per-page OCR `Blocks` | 2026-08-09 |
| `authored_document_automation_response.json` | document standard output | 2026-08-10 |
| `authored_model_reply.json` | `Converse` response, tool-use form | 2026-08-12 |

**When the estate is applied, a captured response may replace an authored one — and the `_note`
changes in the same commit.** Decision 14's revision permits that in one direction and forbids
the thing this file exists to prevent in both: a fixture whose provenance line does not match
how it was obtained.

That distinction is the whole reason this file exists. A fixture presented as a recorded
response is evidence about a service; a fixture authored from documentation is evidence about
the *adapter* and about the documentation being read correctly. The second is a smaller claim
and it is the true one, and `docs/DECISIONS.md` 16 requires it to be said in the fixture, in the
test name and in the README.

What these therefore prove: that the mapping handles the documented shape, including the parts
this system does not use, and that it refuses a response that does not match rather than
quietly producing a short reading.

What they cannot prove: that the service returns this shape. Only a call could show that, and
no call is made.
