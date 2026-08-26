# outputs/

Scan reports land here by default (`.json` + `.md`).

```bash
tads scan inputs/RFC5905.txt --doc-id RFC5905 --yes
# → outputs/RFC5905.json and outputs/RFC5905.md

# Or choose a prefix (useful for provider bake-offs; append a tag to avoid overwrites):
tads scan inputs/RFC5905.txt --doc-id RFC5905 \
  -o outputs/RFC5905__gemini__gemini-3.6-flash__pf0.5.0 --yes
```

JSON is canonical (all candidates, including `out_of_scope`). Markdown shows core/supporting candidates for review, incidental in a lower section, and omits out_of_scope by default.

Edit dispositions in the JSON (`accepted` = human-confirmed / validated finding), then refresh Markdown:

```bash
tads render outputs/RFC5905.json -o outputs/RFC5905.md
```

Contents of this folder are gitignored (except this README).
