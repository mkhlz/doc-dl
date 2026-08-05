# doc-dl deterministic fixtures

These files define browser and discovery behavior independently of the implementation language.

The eventual integration-test server will mount `site/` as HTML fixtures and implement the routes described by `cases.json`.

Required generated test documents:

- A valid one-page PDF.
- A valid DOCX ZIP container.
- A corrupt PDF prefix followed by invalid data.
- Six numbered SVG viewer pages.

The fixture server must bind only to a loopback address and use a random free port.

