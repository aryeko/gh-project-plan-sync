# Plugin Reference

Quick lookup for the planpilot Claude Code and Codex plugins: commands, skill locations, marketplace sources, and runtime pins.

## Plugin commands

| Command | Skill | Description |
|---------|-------|-------------|
| `/planpilot:prd` | `create-prd` | Generate a structured PRD from a feature idea |
| `/planpilot:spec` | `create-tech-spec` | Generate a codebase-aware tech spec from a PRD |
| `/planpilot:sync` | `plan-sync` | Generate `.plans` JSON and sync to GitHub Issues + Projects v2 |

## Install methods

### Claude Code Plugin

```bash
claude plugin marketplace add aryeko/planpilot
claude plugin install planpilot@planpilot
```

The plugin is installed from GitHub source and exposes the shared skills plus Claude slash commands.

### Codex Plugin

```bash
codex plugin marketplace add aryeko/planpilot
codex plugin add planpilot@planpilot
```

The Codex plugin is installed from the same GitHub source and exposes the shared skills. Codex does not consume Claude-only command or hook metadata.

### CLI runtime

The plugin source comes from GitHub. The `plan-sync` runtime uses the exact released PyPI artifact:

```bash
uvx --from planpilot==2.7.0 planpilot --version
```

Local `planpilot` or `python3 -m planpilot` fallbacks are valid only when they print `planpilot 2.7.0`.

### Agent self-install

```text
Fetch and follow instructions from https://raw.githubusercontent.com/aryeko/planpilot/main/skills/INSTALL.agent.md
```

### Manual (filesystem skills)

```bash
for skill in create-prd create-tech-spec plan-sync; do
  mkdir -p ~/.agents/skills/$skill
  curl -fsSL "https://raw.githubusercontent.com/aryeko/planpilot/main/skills/$skill/SKILL.md" \
    -o ~/.agents/skills/$skill/SKILL.md
done
```

## Skill files

| File | Purpose |
|------|---------|
| `skills/create-prd/SKILL.md` | PRD generation skill definition |
| `skills/create-tech-spec/SKILL.md` | Tech spec generation skill definition |
| `skills/plan-sync/SKILL.md` | Plan sync skill definition |
| `skills/INSTALL.md` | Manual install instructions |
| `skills/INSTALL.agent.md` | Agent self-install instructions |

## Plugin manifest files

| File | Purpose |
|------|---------|
| `.claude-plugin/plugin.json` | Claude plugin manifest: name, version, skills, commands |
| `.claude-plugin/marketplace.json` | Claude marketplace entry pointing to the repository root payload |
| `.codex-plugin/plugin.json` | Codex plugin manifest: name, version, skills, interface metadata |
| `.agents/plugins/marketplace.json` | Codex-compatible marketplace entry pointing to the repository root payload |
| `skills/` | Shared skill payload used by Claude, Codex, and manual installs |
| `commands/` | Shared Claude slash command payload |

## Release synchronization

`poetry run poe check` includes the release-surface guard. It verifies that `pyproject.toml`, both plugin manifests, marketplace entries, docs examples, and the `plan-sync` runtime pin all reference the same version. The release workflow runs the same guard in `--fix` mode after semantic-release bumps versions and before package build/publish.

## Related

- [Plugin and Skills Guide](../guides/plugin-skills-guide.md) — walkthrough and troubleshooting
- [CLI Reference](./cli-reference.md) — `planpilot sync` / `clean` / `map sync` flags
