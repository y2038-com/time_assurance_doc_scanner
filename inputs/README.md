# inputs/

Place source documents here for scanning (fetched RFCs, converted `.txt`, local specs).

```bash
tads fetch RFC5905          # → inputs/RFC5905.txt
tads convert ./spec.docx -o inputs/spec.txt
tads plan inputs/RFC5905.txt --doc-id RFC5905
```

Contents of this folder are gitignored (except this README).
