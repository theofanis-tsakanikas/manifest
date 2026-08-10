# The tier-0 reader, as an image.
#
# **The reader is the artefact, not the code around it.** Every threshold in this repository is
# derived from a committed recording produced by this binary at this version with this language
# data. A deployed tier 0 that ran a different build would produce different confidences, and
# every claim-1 figure would become a statement about a machine nobody can reproduce. So the
# version is pinned, asserted at build time, and the image is what both the laptop recording
# ceremony and the estate use.
#
# It carries `read_tier0` and `provenance_gate`, because both need the raster and the reader.
# `publish` runs on plain Python and does not belong here; it ships as a zip, and keeping it out
# means a change to the extraction logic does not require rebuilding a 900 MB image.

FROM public.ecr.aws/lambda/python:3.12

# The reader and its language data. Greek and Dutch are the two the scenario turns on and the
# two no managed service reads — `docs/AWS-CONSTRAINTS.md` — so their absence is not a
# degradation here, it is the loss of the only reader that can see those pages at all.
#
# `tesseract-langpack-*` rather than a single blob: an image that silently lacked one language
# would read those pages as English and return confident text in the wrong alphabet.
RUN dnf install -y \
      tesseract \
      tesseract-langpack-ell \
      tesseract-langpack-nld \
      tesseract-langpack-deu \
      tesseract-langpack-fra \
      poppler-utils \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# The pin, asserted rather than hoped for.
#
# A base image is rebuilt by its publisher, and a rebuild can move a package. If that moved the
# reader, every derived threshold would drift on the next deploy — silently, because nothing
# downstream would notice a confidence distribution shifting by two points. `make ocr-record`
# refuses to overwrite a recording without an accepted movement report for exactly this reason,
# and this line is the same rule enforced at the other end.
ARG EXPECTED_READER_VERSION=5.5.0
RUN installed="$(tesseract --version 2>&1 | head -1 | awk '{print $2}')" \
    && if [ "$installed" != "$EXPECTED_READER_VERSION" ]; then \
         echo "reader is $installed, this image expects $EXPECTED_READER_VERSION." >&2; \
         echo "Every threshold in recordings/ was derived from the expected build. Accept the" >&2; \
         echo "movement with 'make ocr-record' and update EXPECTED_READER_VERSION together." >&2; \
         exit 1; \
       fi \
    && for language in eng ell nld deu fra; do \
         tesseract --list-langs 2>&1 | grep -qx "$language" || { \
           echo "language data for '$language' is missing." >&2; \
           echo "A reader without it does not fail on those pages — it reads them in the" >&2; \
           echo "wrong alphabet and returns confident nonsense." >&2; \
           exit 1; }; \
       done

COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY src/ ${LAMBDA_TASK_ROOT}/src/
# The contracts travel with the image. They are the source of truth for which fields exist and
# what each one's comparison rules are, and a deployment whose code and contracts came from
# different commits would apply one version's rules to another version's fields.
COPY contracts/ ${LAMBDA_TASK_ROOT}/contracts/

RUN python -m pip install --no-cache-dir "${LAMBDA_TASK_ROOT}" \
    && python -m pip install --no-cache-dir pillow numpy pypdfium2

ENV PYTHONPATH="${LAMBDA_TASK_ROOT}/src" \
    CONTRACTS_DIR="${LAMBDA_TASK_ROOT}/contracts"

# Overridden per function by the estate: `read_tier0.handler` and `provenance_gate.handler`
# share this image and differ only in this value.
CMD ["manifest.handlers.read_tier0.handler"]
