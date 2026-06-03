# Skill Catalog for Claude Code

A browsable, searchable skill directory for Claude Code. When you have 30+ skills and can't remember which one does what — this solves that.

![skill-catalog](https://img.shields.io/badge/skills-catalog-blue)

## What it does

- **Auto-discovers** all your installed skills from `~/.claude/skills/`
- **Categorizes** them intelligently (manual mapping + keyword similarity + auto-create new categories)
- **Generates** an interactive HTML viewer with search, category filtering, dark/light theme
- **Tracks usage** — scans Claude Code transcripts to show how often each skill is called, with session history
- **One command** to update everything: `python regenerate.py --with-usage`

## Screenshot

![Screenshot](screenshot.png)

The HTML viewer provides:
- Real-time search by skill name, description, or trigger keywords
- Category sidebar + pill navigation
- Click any card → detail panel with full SKILL.md content preview
- Usage statistics: call counts, last used dates, session history
- Delete skills from the UI (copies terminal command to clipboard)
- Keyboard shortcuts: `/` to search, `1-9` jump categories, `Ctrl+T` toggle theme
- Dark/light theme auto-detection + manual toggle
- Mobile responsive

## Installation

```bash
# 1. Clone into your Claude Code skills directory
cd ~/.claude/skills
git clone https://github.com/YOUR_USERNAME/skill-catalog.git

# 2. Run the regenerator
cd skill-catalog
python regenerate.py --with-usage

# 3. Open the catalog
# In browser: open catalog.html
# Or via Claude Code: /skill-catalog
```

**Requirements:** Python 3.7+ (no pip packages needed) + a browser.

## File Structure

```
skill-catalog/
├── SKILL.md                 # Skill definition → enables /skill-catalog
├── regenerate.py            # Auto-update engine (the core)
├── catalog.template.html    # HTML viewer template
├── category-mapping.txt     # Your skill→category assignments (edit this!)
│
├── catalog.json             # GENERATED — structured data
├── catalog.md               # GENERATED — markdown catalog
└── catalog.html             # GENERATED — interactive viewer
```

All `catalog.*` files are auto-generated. Add them to `.gitignore`.

## How Categories Work

### Manual mapping (recommended)

Edit `category-mapping.txt`:
```
brainstorming | Planning & Design
my-custom-skill | Content Creation
```

### Auto-categorization

Skills NOT in the mapping are categorized by keyword similarity against existing categories. If no match (similarity < 25%), a **new category is auto-created** from the skill's description.

### Built-in skills

Claude Code has ~13 built-in/plugin skills (like `code-review`, `deep-research`, `verify`). They're automatically included even without a local SKILL.md.

## Usage Tracking

```bash
python regenerate.py --with-usage
```

Scans all `~/.claude/projects/**/*.jsonl` transcript files to find `Skill` tool calls. Each skill card then shows:

```
Called 42 times   Last: 2025-12-15   history →
```

Click "history" to see full session list with timestamps and prompts.

**Privacy note:** Usage data stays local — the HTML is a static file with embedded JSON, nothing is sent anywhere.

## Commands

| Command | What it does |
|---------|-------------|
| `python regenerate.py` | Basic regeneration (no usage data) |
| `python regenerate.py --with-usage` | Full regeneration with call statistics |
| `python regenerate.py --dry-run` | Preview without writing files |
| `python regenerate.py --verbose` | Show per-skill categorization decisions |

## How the HTML Works

- **Single file, zero dependencies** — no CDN, no npm, no framework
- **All data embedded** — `catalog.json` is inlined into a `<script>` tag
- **Works offline** — open `catalog.html` directly in any browser
- **Theme persists** — via `localStorage`

## Customization

### Adding a new built-in skill

Edit `regenerate.py` → `BUILTIN_SKILLS` dict. Add:
```python
"my-builtin-skill": {
    "description": "What this skill does.",
    "trigger_keywords": ["keyword1", "keyword2"]
}
```

### Changing the similarity threshold

Edit `regenerate.py` → `SIMILARITY_THRESHOLD` (default: 0.25). Lower = more aggressive grouping into existing categories.

### Modifying the HTML template

Edit `catalog.template.html`, then run `python regenerate.py`.

## License

MIT
