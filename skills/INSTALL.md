# Install: planpilot Skills

This installs the planpilot skills into the open discovery path used by agent platforms that support filesystem skills:

- `~/.agents/skills/<skill-name>/SKILL.md`

## Planpilot Skills Overview

The planpilot suite includes three complementary skills that form a workflow chain:

1. **`create-prd`** — Start with a feature idea and generate a structured Product Requirements Document through interactive discovery.
2. **`create-tech-spec`** — Take your PRD and create a codebase-aware technical specification with architecture diagrams.
3. **`plan-sync`** — Convert your PRD/spec into schema-aligned `.plans` JSON files and sync them to GitHub Issues and Projects v2.

Install all three to unlock the full end-to-end planning workflow.

## Agent Self-Install

Tell your agent:

> Fetch and follow instructions from https://raw.githubusercontent.com/aryeko/planpilot/main/skills/INSTALL.agent.md

The agent will install `planpilot` and all three skills automatically.

## Manual Install

### 1) Prerequisites

Check Python 3.11+ is available:

```bash
python3 --version
```

If Python is not installed or below 3.11, install it from https://python.org.

### 2) Install `planpilot`

The skill runtime uses an exact release. You can run it one-off through `uvx` or install the same release on PATH.

**Option A — via `uv` (recommended):**

[`uv`](https://docs.astral.sh/uv/) installs tools in isolated environments with no PEP 668 issues.

If `uv` is not installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> **Note:** Piping to `sh` without checksum verification is the officially recommended method but carries inherent supply-chain risk. For a verified install, download the binary directly from [uv GitHub releases](https://github.com/astral-sh/uv/releases) and verify the SHA-256 hash before running.

For one-off skill runs, prefer the pinned runtime command used by the plugin:

```bash
uvx --from planpilot==2.7.0 planpilot --version
```

For a persistent local install, install the same release:

```bash
uv tool install "planpilot==2.7.0"
planpilot --version
```

**Option B — via `pipx`:**

[`pipx`](https://pipx.pypa.io/) installs CLI tools in isolated environments — avoids PEP 668 errors on macOS Homebrew and system Python.

If `pipx` is not found:

```bash
# macOS
brew install pipx
pipx ensurepath

# Debian / Ubuntu
sudo apt install -y pipx
pipx ensurepath
```

You may need to restart your shell after `ensurepath`.

```bash
pipx install "planpilot==2.7.0"
planpilot --version
```

**Option C — via `pip3` (last resort):**

```bash
pip3 install "planpilot==2.7.0"
planpilot --version
```

### 3) Install the skills into open skill path

Create destination directories:

```bash
mkdir -p ~/.agents/skills/create-prd
mkdir -p ~/.agents/skills/create-tech-spec
mkdir -p ~/.agents/skills/plan-sync
```

#### Local: Copy from local repo checkout

```bash
cp skills/create-prd/SKILL.md \
  ~/.agents/skills/create-prd/SKILL.md

cp skills/create-tech-spec/SKILL.md \
  ~/.agents/skills/create-tech-spec/SKILL.md

cp skills/plan-sync/SKILL.md \
  ~/.agents/skills/plan-sync/SKILL.md
```

#### Remote: Fetch from GitHub raw URL

```bash
curl -fsSL "https://raw.githubusercontent.com/aryeko/planpilot/main/skills/create-prd/SKILL.md" \
  -o ~/.agents/skills/create-prd/SKILL.md

curl -fsSL "https://raw.githubusercontent.com/aryeko/planpilot/main/skills/create-tech-spec/SKILL.md" \
  -o ~/.agents/skills/create-tech-spec/SKILL.md

curl -fsSL "https://raw.githubusercontent.com/aryeko/planpilot/main/skills/plan-sync/SKILL.md" \
  -o ~/.agents/skills/plan-sync/SKILL.md
```

For reproducible skill installs, pin the raw GitHub URLs to the release tag that matches the runtime pin instead of `main`.

### 4) Verify skill install

```bash
ls -la ~/.agents/skills/create-prd
head -5 ~/.agents/skills/create-prd/SKILL.md

ls -la ~/.agents/skills/create-tech-spec
head -5 ~/.agents/skills/create-tech-spec/SKILL.md

ls -la ~/.agents/skills/plan-sync
head -5 ~/.agents/skills/plan-sync/SKILL.md
```

Expected frontmatter should include:

- `name: create-prd`
- `name: create-tech-spec`
- `name: plan-sync`

### 5) Restart your agent

**Restart your agent** (start a new session) so it discovers the newly installed skill. The skill won't be available until the agent re-scans `~/.agents/skills/`.

### 6) Update / uninstall

**Update `planpilot`:**

```bash
# If installed via uv
uv tool install --force "planpilot==2.7.0"

# If installed via pipx
pipx install --force "planpilot==2.7.0"

# If installed via pip3
pip3 install --upgrade "planpilot==2.7.0"
```

**Update skills** (re-copy or re-fetch `SKILL.md` for each skill):

```bash
curl -fsSL "https://raw.githubusercontent.com/aryeko/planpilot/main/skills/create-prd/SKILL.md" \
  -o ~/.agents/skills/create-prd/SKILL.md

curl -fsSL "https://raw.githubusercontent.com/aryeko/planpilot/main/skills/create-tech-spec/SKILL.md" \
  -o ~/.agents/skills/create-tech-spec/SKILL.md

curl -fsSL "https://raw.githubusercontent.com/aryeko/planpilot/main/skills/plan-sync/SKILL.md" \
  -o ~/.agents/skills/plan-sync/SKILL.md
```

Uninstall all skills:

```bash
rm -rf ~/.agents/skills/create-prd
rm -rf ~/.agents/skills/create-tech-spec
rm -rf ~/.agents/skills/plan-sync
```

Or uninstall individual skills as needed.
