# Voomie

Voomie is a natural-language front door to **shoptalk** that turns messy customer emails into validated shoptalk job declarations and writes complete job records to MongoDB. Built for the MongoDB AI Agents Hackathon to demonstrate a load-bearing MongoDB MCP integration — the agent reasons about and acts on a single MongoDB source of truth via Model Context Protocol tools invoked over stdio.

**Status:** v1 in active development. See [SPEC.md](./SPEC.md) for the build target.

## Documentation

- [SPEC.md](./SPEC.md) — full v1 specification
- [MEMORY.md](./MEMORY.md) — project context
- [PROTOTYPE-AUDIT.md](./PROTOTYPE-AUDIT.md) — analysis of the predecessor prototype
- [prototype-v0/](./prototype-v0/) — the May 7 predecessor (PressFlow AI), preserved as reference. See its [README](./prototype-v0/README.md).

## Architecture

```
  Customer message + attachments
              │
              ▼
       ┌────────────┐
       │   Voomie   │  (Gemini via Vertex AI)
       │   agent    │
       └─────┬──────┘
             │ MCP over stdio
             ▼
   ┌─────────────────────┐
   │     MCP server      │
   └─┬─────┬─────┬─────┬─┘
     │     │     │     │
     ▼     ▼     ▼     ▼
 MongoDB pikepdf shoptalk verifier
 (jobs,   (PDF   (spec    (preview
 history, inspect) parser) PDF)
 logs)
```

CSR dashboard reads from MongoDB, writes nothing back except CSR-approved sends and overrides.

## Dependencies

Voomie depends on **shoptalk** being available locally. shoptalk's spec parser is invoked via subprocess for declaration validation, and shoptalk's Python verifier renders preview PDFs from action plans. Path resolution is handled at runtime via the `SHOPTALK_REPO_PATH` environment variable — see [SPEC.md](./SPEC.md) §MCP tool surface for the integration boundary.

## License

MIT — see [LICENSE](./LICENSE).
