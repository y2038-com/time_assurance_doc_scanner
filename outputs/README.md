# outputs/

Scan reports land here by default (`.json` + `.md`).

```bash
tads scan inputs/RFC5905.txt --doc-id RFC5905 --yes
# → outputs/RFC5905.json and outputs/RFC5905.md

# Or choose a prefix:
tads scan inputs/RFC5905.txt --doc-id RFC5905 -o outputs/RFC5905 --yes
```

Edit dispositions in the JSON (`accepted` = human-confirmed / validated finding), then refresh Markdown:

```bash
tads render outputs/RFC5905.json -o outputs/RFC5905.md
```

Contents of this folder are gitignored (except this README).
