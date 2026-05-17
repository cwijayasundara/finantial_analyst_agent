# PII model installation

The PII NER stack needs spaCy's `en_core_web_lg` model. Install once per machine:

    uv run python -m spacy download en_core_web_lg

The model is ~560MB and not bundled with the spaCy package. CI runs `python -m spacy download en_core_web_lg` after `uv sync`. The model lives under spaCy's package data and is not committed to the repo.

To verify:

    uv run python -c "import spacy; nlp = spacy.load('en_core_web_lg'); print(nlp('John Smith lives in Manchester.').ents)"

Expected: `(John Smith, Manchester)` with `PERSON` and `GPE` labels.
