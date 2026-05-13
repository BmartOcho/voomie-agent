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

## Shop-private content (loaded at runtime)

Two pieces of shop-specific content are loaded at runtime from gitignored files. Both have `.example` stubs committed to document the expected shape:

- **System prompt** — `prompts/voomie_system.md` (gitignored). Contains your shop's DSL primer, coating compatibility rules, escalation policy, and the 10-step agent mission. Loaded via the `VOOMIE_SYSTEM_PROMPT_PATH` environment variable. See [`prompts/voomie_system.md.example`](./prompts/voomie_system.md.example) for the shape.
- **Seed data** — `seed_data.json` (gitignored). Customer profiles, job history, conversation logs, and flag records used to populate the demo database. Loaded by `scripts/seed_db.py` (override path with `SEED_DATA_PATH` env var). See [`seed_data.json.example`](./seed_data.json.example) for one stub entry showing the JSON shape.

Both are gitignored because they contain shop-specific business logic and (in the seed case) realistic customer/job records that aren't appropriate to ship in a public repo. Copy the `.example` files, fill in your shop's content, and set the env vars before running.

## License

MIT — see [LICENSE](./LICENSE).
