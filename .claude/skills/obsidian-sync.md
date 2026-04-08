---
name: obsidian-sync
description: Sync code changes to Obsidian vault documentation
user_invocable: true
---

# Obsidian Sync Skill

When invoked, analyze recent code changes and update the corresponding Obsidian documentation in `docs/obsidian-vault/`.

## Workflow

1. **Detect Changes**: Check git diff or recently modified files
2. **Map to Docs**: Identify which Obsidian docs need updating
3. **Update Docs**: Edit the markdown files with new information
4. **Verify Architecture**: Ensure changes align with Architecture-Overview.canvas

## File Mapping

| Source Path | Obsidian Doc |
|-------------|--------------|
| `layer0-collector/alloy/*` | `Infrastructure/Grafana Alloy.md` |
| `layer0-collector/loki/*` | `Infrastructure/Loki.md` |
| `layer0-storage/timescaledb/*` | `Infrastructure/TimescaleDB.md`, `Database/Database Schema.md` |
| `layer0-storage/sync/*` | `Infrastructure/Log Archiver.md` |
| `layer1-filter/*` | `Services/Layer 1 Filter.md` |
| `logbert/*` | `Services/LogBERT.md` |
| `layer2-analyzer/src/anomaly_consumer*` | `Services/Anomaly Consumer.md` |
| `layer2-analyzer/src/root_cause_analyzer/*` | `Services/*.md` |
| `layer2-analyzer/src/llm/*` | `External/OpenAI API.md`, `External/Ollama.md` |
| `layer3-remediation/*` | `Services/Auto Remediation.md` |
| `dashboard/*` | `Visualization/Dashboard.md` |
| `grafana/*` | `Visualization/Grafana Dashboard.md` |
| `prometheus/*` | `Infrastructure/Prometheus.md` |
| `docker-compose.yaml` | `Docker Compose Services.md` |

## Instructions

1. Run `git diff --name-only HEAD~5` to see recent changes
2. For each changed file, identify the corresponding Obsidian doc
3. Read both the source file and the Obsidian doc
4. Update the doc to reflect changes (new functions, config changes, API changes)
5. Maintain existing doc structure (frontmatter, sections, wikilinks)

## Architecture Check

After updating docs, verify:
- Layer count matches (code directories vs Layer overview pages)
- No new services missing documentation
- Configuration Files.md is up to date
- API Endpoints.md reflects current endpoints
