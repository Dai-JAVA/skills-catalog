#!/usr/bin/env python3
"""
Skill Catalog Regenerator
==========================
Scans ~/.claude/skills/*/SKILL.md, categorizes every skill, and regenerates
catalog.json, catalog.md, and catalog.html.

Usage:
    python regenerate.py                  # Full regeneration
    python regenerate.py --with-usage     # Include transcript call statistics
    python regenerate.py --dry-run        # Preview without writing files
    python regenerate.py --verbose        # Show detailed matching info

Category assignment logic (in order):
    1. Manual mapping (category-mapping.txt) -- highest priority
    2. Keyword similarity against existing categories
    3. Auto-create new category if no match (threshold: < 25% similarity)
"""
import json, os, re, sys, time
from pathlib import Path
from collections import defaultdict

# Force UTF-8 output on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# --- Configuration ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SKILLS_DIR = SCRIPT_DIR.parent
CATALOG_DIR = SCRIPT_DIR
MAPPING_FILE = CATALOG_DIR / "category-mapping.txt"
PENDING_DELETES = CATALOG_DIR / "pending-deletes.txt"
CATALOG_JSON = CATALOG_DIR / "catalog.json"
CATALOG_MD = CATALOG_DIR / "catalog.md"
CATALOG_HTML = CATALOG_DIR / "catalog.html"
HTML_TEMPLATE = CATALOG_DIR / "catalog.template.html"

# Claude Code data directories
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

BUILTIN_SKILLS = {
    "deep-research": {
        "description": "Deep research harness -- fan-out web searches, fetch sources, adversarially verify claims, synthesize a cited report. If the question is underspecified, asks 2-3 clarifying questions to narrow scope before researching.",
        "trigger_keywords": ["deep research", "research report", "multi-source", "fact-checked", "cited report", "comprehensive research"]
    },
    "update-config": {
        "description": "Configure the Claude Code harness via settings.json. Handles hooks, permissions, env vars, and changes to settings.json / settings.local.json.",
        "trigger_keywords": ["settings", "config", "permissions", "env vars", "hooks", "allow", "settings.json"]
    },
    "keybindings-help": {
        "description": "Customize keyboard shortcuts, rebind keys, add chord bindings, or modify ~/.claude/keybindings.json.",
        "trigger_keywords": ["keybindings", "keyboard shortcuts", "rebind", "chord", "customize keys", "hotkeys"]
    },
    "verify": {
        "description": "Verify that a code change actually does what it's supposed to by running the app and observing behavior.",
        "trigger_keywords": ["verify PR", "confirm fix", "test change", "check feature", "validate local changes"]
    },
    "code-review": {
        "description": "Review the current diff for correctness bugs and reuse/simplification/efficiency cleanups. Pass --comment to post findings as inline PR comments, or --fix to apply findings to the working tree.",
        "trigger_keywords": ["review diff", "correctness bugs", "cleanups", "--comment", "--fix", "code review"]
    },
    "simplify": {
        "description": "Review the changed code for reuse, simplification, efficiency, and altitude cleanups, then apply the fixes. Quality only -- does not hunt for bugs; use /code-review for that.",
        "trigger_keywords": ["simplify", "reuse", "efficiency", "cleanups", "refactor"]
    },
    "security-review": {
        "description": "Complete a security review of the pending changes on the current branch.",
        "trigger_keywords": ["security review", "vulnerabilities", "insecure patterns", "security audit"]
    },
    "review": {
        "description": "Review a pull request. Examines the full PR diff for correctness, style, performance, and test coverage.",
        "trigger_keywords": ["review PR", "pull request review", "PR feedback"]
    },
    "fewer-permission-prompts": {
        "description": "Scan your transcripts for common read-only Bash and MCP tool calls, then add a prioritized allowlist to reduce permission prompts.",
        "trigger_keywords": ["permission prompts", "allowlist", "reduce prompts", "scan transcripts", "auto-allow"]
    },
    "loop": {
        "description": "Run a prompt or slash command on a recurring interval (e.g. /loop 5m /foo, defaults to 10m). Do NOT invoke for one-off tasks.",
        "trigger_keywords": ["recurring", "interval", "poll", "repeat", "every 5 minutes", "keep running", "schedule"]
    },
    "claude-api": {
        "description": "Build, debug, and optimize Claude API / Anthropic SDK apps. Handles prompt caching, model migration, tool use, batch processing, files, citations, and memory.",
        "trigger_keywords": ["Claude API", "Anthropic SDK", "prompt caching", "model migration", "tool use", "batch processing"]
    },
    "run": {
        "description": "Launch and drive this project's app to see a change working. Use when asked to run, start, or screenshot the app.",
        "trigger_keywords": ["run app", "start app", "screenshot app", "launch"]
    },
    "init": {
        "description": "Initialize a new CLAUDE.md file with codebase documentation.",
        "trigger_keywords": ["initialize", "CLAUDE.md", "codebase documentation", "project setup", "new project"]
    },
}

SIMILARITY_THRESHOLD = 0.25


# --- YAML Frontmatter Parser -------------------------------------------------

def parse_skill_md(filepath):
    """Extract name, description, and body content from a SKILL.md."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"  [WARN] Cannot read {filepath}: {e}", file=sys.stderr)
        return None

    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not m:
        print(f"  [WARN] No frontmatter in {filepath}", file=sys.stderr)
        return None

    fm = m.group(1)
    body = content[m.end():].strip()
    # Truncate body for detail panel (first 3000 chars, ~150 lines)
    body_preview = body[:3000]
    if len(body) > 3000:
        body_preview += "\n\n... (truncated)"

    name = None
    description = None
    nm = re.search(r'^name:\s*["\']?(.+?)["\']?\s*$', fm, re.MULTILINE)
    if nm:
        name = nm.group(1).strip()

    desc_match = re.search(r'^description:\s*["\']?(.+?)["\']?\s*$', fm, re.MULTILINE)
    if desc_match:
        description = desc_match.group(1).strip()
    else:
        desc_block = re.search(r'^description:\s*\|\s*\n(.*?)(?=^[a-z-]+:\s)', fm, re.MULTILINE | re.DOTALL)
        if desc_block:
            lines = desc_block.group(1).strip().split('\n')
            description = ' '.join(line.strip() for line in lines)

    if not name:
        print(f"  [WARN] No 'name' field in {filepath}", file=sys.stderr)
        return None

    return {
        "name": name,
        "description": description or "",
        "source": "local",
        "file_path": str(Path(filepath).relative_to(SKILLS_DIR.parent).as_posix()),
        "body": body_preview,              # For detail panel
        "body_word_count": len(body.split()),
        "allowed_tools": extract_allowed_tools(fm),
    }


def extract_allowed_tools(fm):
    """Extract allowed-tools list from frontmatter."""
    tools = []
    for line in fm.split('\n'):
        m = re.match(r'\s*-\s*(.+)', line)
        if m:
            tools.append(m.group(1).strip())
    return tools[:10] if tools else []


# --- Keyword Extraction ------------------------------------------------------

def extract_keywords(text):
    if not text:
        return []
    quoted = re.findall(r'["""]([^"""]+?)["\""]', text)
    trigger_phrases = []
    for indicator in ['Triggers include', 'use when', 'triggers on', 'Use when']:
        idx = text.find(indicator)
        if idx >= 0:
            after = text[idx + len(indicator):]
            triggers = re.findall(r'["""]([^"""]+?)["\""]', after)
            trigger_phrases.extend(triggers)
            parts = after.split(',')
            trigger_phrases.extend(p.strip(' ."') for p in parts[:5])
    keywords = quoted + trigger_phrases
    stop = {'the', 'and', 'for', 'use', 'when', 'this', 'that', 'with', 'from',
            'any', 'all', 'not', 'are', 'has', 'had', 'was', 'can', 'may', 'like',
            'more', 'also', 'into', 'over', 'than', 'then', 'just', 'how', 'what'}
    return list(dict.fromkeys(
        k.lower().strip()
        for k in keywords
        if len(k) > 3 and k.lower() not in stop
    ))[:8]


# --- Transcript Scanner ------------------------------------------------------

# Known name mappings: transcript tool-call name → canonical SKILL.md name
# Add your own mappings here if directory names differ from SKILL.md names.
NAME_ALIASES = {
    # Example: "My-Skill-Dir": "my-skill-name",
}

def normalize_skill_name(name):
    """Normalize a skill name from transcript to canonical form."""
    return NAME_ALIASES.get(name, name)


def scan_transcripts(capture_triggers=False):
    """Scan all .jsonl transcript files for Skill tool calls.
    If capture_triggers=True, also captures the user message immediately before each call.
    Returns {skill_name: {count, last_used, sessions: [{time, prompt, session_id, trigger_phrase}]}}
    """
    usage = defaultdict(lambda: {"count": 0, "last_used": None, "sessions": []})

    if not CLAUDE_PROJECTS_DIR.exists():
        return dict(usage)

    jsonl_files = list(CLAUDE_PROJECTS_DIR.rglob("*.jsonl"))
    if not jsonl_files:
        return dict(usage)

    for jsonl_path in jsonl_files:
        session_id = jsonl_path.stem
        try:
            with open(jsonl_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception:
            continue

        session_prompt = ""
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "user":
                msg = obj.get("message", {})
                content = msg.get("content", [])
                if content and isinstance(content, list):
                    session_prompt = str(content[0].get("text", ""))[:120]
                elif isinstance(content, str):
                    session_prompt = str(content)[:120]
                if session_prompt:
                    break

        # Track the most recent user message for per-call trigger capture
        last_user_msg = session_prompt

        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Update last_user_msg whenever we see a user message
            if obj.get("type") == "user":
                msg = obj.get("message", {})
                content = msg.get("content", [])
                user_text = ""
                if content and isinstance(content, list):
                    user_text = str(content[0].get("text", ""))[:200]
                elif isinstance(content, str):
                    user_text = str(content)[:200]
                if user_text:
                    last_user_msg = user_text

            timestamp = obj.get("timestamp", "")

            # Pattern 1: Assistant message with tool_use in content array
            if obj.get("type") == "assistant":
                for c in obj.get("message", {}).get("content", []):
                    if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Skill":
                        skill_name = c.get("input", {}).get("skill", "unknown")
                        if skill_name and skill_name != "unknown":
                            normalized = normalize_skill_name(skill_name)
                            trigger = last_user_msg if capture_triggers else ""
                            _aggregate_call(usage, [(normalized, timestamp, trigger)], session_id, session_prompt)

            # Pattern 2: Top-level tool_use (older format)
            if obj.get("type") == "tool_use" and obj.get("tool") == "Skill":
                skill_name = obj.get("input", {}).get("skill", "unknown")
                if skill_name and skill_name != "unknown":
                    normalized = normalize_skill_name(skill_name)
                    trigger = last_user_msg if capture_triggers else ""
                    _aggregate_call(usage, [(normalized, timestamp, trigger)], session_id, session_prompt)

    for skill_name in usage:
        usage[skill_name]["sessions"].sort(
            key=lambda s: s.get("time", ""), reverse=True
        )
        usage[skill_name]["sessions"] = usage[skill_name]["sessions"][:10]

    return dict(usage)


def _aggregate_call(usage, calls, session_id, session_prompt):
    """Helper: aggregate skill calls into usage dict."""
    for skill_name, timestamp, trigger_phrase in calls:
        u = usage[skill_name]
        u["count"] += 1
        if not u["last_used"] or timestamp > u["last_used"]:
            u["last_used"] = timestamp
        existing = [s for s in u["sessions"] if s["session_id"] == session_id]
        if not existing:
            entry = {
                "session_id": session_id[:12],
                "time": timestamp[:16] if timestamp else "unknown",
                "prompt": session_prompt
            }
            if trigger_phrase:
                entry["trigger_phrase"] = trigger_phrase
            u["sessions"].append(entry)


# --- Route Suggestion Engine --------------------------------------------------

_STOP_PHRASES = {
    "can you", "could you", "would you", "please help", "i need", "i want",
    "help me", "tell me", "what is", "how do", "how to", "how can",
    "i have", "there is", "there are", "this is", "that is",
    "the", "and", "for", "with", "from", "that",
    "tell", "show", "give", "make", "take", "use",
    "帮我", "请问", "能不能", "你可以", "我需要", "我想",
    "你看", "这个", "那个", "怎么样", "有没有", "是什么",
    "帮我看看", "帮我分析", "帮我看一下", "麻烦你",
    "不用", "不要", "不是", "可以", "需要", "应该",
}


def _extract_trigger_phrases(text):
    """Extract potential trigger phrases from user message text.
    Focuses on short, actionable phrases (2-5 words) and filters noise.
    """
    phrases = []
    # Normalize: remove URLs, file paths, excessive punctuation
    cleaned = re.sub(r'https?://\S+', '', text)
    cleaned = re.sub(r'[a-zA-Z]:[\\/][^\s]{20,}', '', cleaned)  # Windows paths
    cleaned = re.sub(r'~?/[\w.-]+(?:/[\w.-]+){2,}', '', cleaned)  # Unix paths
    words = cleaned.split()
    for n in [2, 3, 4, 5]:
        for i in range(len(words) - n + 1):
            chunk = " ".join(words[i:i+n])
            # Must be 6-60 chars, not too generic
            if 6 <= len(chunk) <= 60 and chunk.lower() not in _STOP_PHRASES:
                phrases.append(chunk)
    # Also grab short first sentence (often the main intent)
    first = cleaned.split("\n")[0].split("。")[0].split(". ")[0]
    if 6 <= len(first) <= 60:
        phrases.append(first)
    return phrases


def suggest_routes(usage_data, existing_memory_path=None):
    """Analyze transcript usage to suggest explicit routing rules.
    Returns {suggestions: [...], memory_entries: [...]}
    """
    existing_routes = {}
    if existing_memory_path and Path(existing_memory_path).exists():
        try:
            with open(existing_memory_path, 'r', encoding='utf-8') as f:
                content = f.read()
            for m in re.finditer(r'(?:skills?|/)([a-z][a-z0-9-]+)', content, re.IGNORECASE):
                existing_routes[m.group(1).lower()] = True
        except Exception:
            pass

    all_triggers = defaultdict(set)
    skill_triggers = defaultdict(list)

    for skill_name, data in usage_data.items():
        if data["count"] < 2:
            continue
        for session in data.get("sessions", []):
            raw = session.get("trigger_phrase", "")
            if not raw:
                continue
            phrases = _extract_trigger_phrases(raw)
            for phrase in phrases:
                normalized = phrase.lower().strip()
                if len(normalized) < 4 or normalized in _STOP_PHRASES:
                    continue
                all_triggers[normalized].add(skill_name)
                skill_triggers[skill_name].append((normalized, raw[:80]))

    suggestions = []
    for skill_name, data in sorted(usage_data.items(), key=lambda x: -x[1]["count"]):
        if skill_name in existing_routes:
            continue
        triggers = skill_triggers.get(skill_name, [])
        if not triggers:
            continue

        phrase_counts = defaultdict(int)
        for phrase, _ in triggers:
            phrase_counts[phrase] += 1

        scored = []
        for phrase, count in phrase_counts.items():
            skill_count = len(all_triggers[phrase])
            conflict_with = all_triggers[phrase] - {skill_name}
            uniqueness = 1.0 / max(skill_count, 1)
            frequency = min(count / max(data["count"], 1), 1.0)
            score = (frequency * 0.4) + (uniqueness * 0.6)
            scored.append({
                "phrase": phrase,
                "score": round(score, 2),
                "count": count,
                "conflict_with": sorted(conflict_with) if conflict_with else [],
                "unique": len(conflict_with) == 0
            })

        scored.sort(key=lambda x: (-x["score"], -x["count"]))
        top = [s for s in scored if s["score"] > 0.4][:4]
        if top:
            suggestions.append({
                "skill": skill_name,
                "count": data["count"],
                "suggested_triggers": top
            })

    memory_entries = []
    for s in suggestions:
        if not s["suggested_triggers"]:
            continue
        best = s["suggested_triggers"][0]
        if best["unique"]:
            example = best["phrase"]
        else:
            uniques = [t for t in s["suggested_triggers"] if t["unique"]]
            example = uniques[0]["phrase"] if uniques else best["phrase"]
        memory_entries.append(f"{s['skill']} | {example} | auto")

    return {"suggestions": suggestions, "memory_entries": memory_entries}


# --- Pending Deletes ---------------------------------------------------------

def load_pending_deletes():
    """Load list of skills pending deletion."""
    if not PENDING_DELETES.exists():
        return []
    with open(PENDING_DELETES, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]


# --- Route Management --------------------------------------------------------

ROUTES_JSON = CATALOG_DIR / "routes.json"
def _find_memory_dir():
    """Find the memory directory under .claude/projects/ for this machine."""
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    # Look for a project directory that has a memory/ subdirectory
    for d in sorted(projects_dir.iterdir()):
        if d.is_dir() and (d / "memory").is_dir():
            return d / "memory"
    return None

MEMORY_ROUTE_PATH = None  # Lazily resolved via _resolve_memory_path()

def _resolve_memory_path():
    global MEMORY_ROUTE_PATH
    if MEMORY_ROUTE_PATH is None:
        mem_dir = _find_memory_dir()
        MEMORY_ROUTE_PATH = (mem_dir / "skill_auto_trigger.md") if mem_dir else None
    return MEMORY_ROUTE_PATH


def load_existing_routes():
    """Load routes from routes.json (preferred) or parse from memory file.
    Returns [{skill, trigger, priority, source}] or [].
    """
    # Primary: routes.json
    if ROUTES_JSON.exists():
        try:
            with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
                data = json.load(f)
            routes = data.get("routes", [])
            for r in routes:
                r["source"] = "routes.json"
            return routes
        except Exception:
            pass

    # Fallback: parse memory file
    mem_path = _resolve_memory_path()
    if mem_path and mem_path.exists():
        try:
            with open(mem_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            return []
        routes = []
        in_table = False
        for line in content.split('\n'):
            stripped = line.strip()
            if '|' in stripped and ('Skill' in stripped or 'skill' in stripped.lower()) and ('触发' in stripped or 'trigger' in stripped.lower()):
                in_table = True
                continue
            if in_table and stripped.startswith('|') and not stripped.startswith('|---'):
                parts = [p.strip() for p in stripped.split('|') if p.strip()]
                if len(parts) >= 2:
                    skill_name = parts[0].replace('`', '').strip()
                    trigger = parts[1].strip()
                    skill_name = normalize_skill_name(skill_name)
                    routes.append({
                        "skill": skill_name,
                        "trigger": trigger,
                        "priority": 3,
                        "source": "memory"
                    })
            elif in_table and not stripped.startswith('|'):
                in_table = False
        return routes

    return []


def sync_routes_to_memory():
    """Sync routes.json → skill_auto_trigger.md memory file.
    Called by --sync-routes flag or automatically when routes.json is newer.
    """
    if not ROUTES_JSON.exists():
        print("  No routes.json found, skipping sync.")
        return False

    with open(ROUTES_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)

    routes = data.get("routes", [])
    if not routes:
        print("  routes.json is empty, skipping sync.")
        return False

    # Build memory file content
    lines = [
        "---",
        "name: skill-auto-trigger",
        "description: 常用的 skills 应自动触发，无需用户每次手动点名",
        "metadata:",
        "  type: feedback",
        "---",
        "",
        "以下 skills 在满足触发条件时必须自动调用：",
        "",
        "| Skill | 触发条件 |",
        "|-------|----------|",
    ]
    for r in routes:
        lines.append(f"| {r['skill']} | {r['trigger']} |")

    lines += [
        "",
        "其他 skills 保持按需手动调用。",
        "",
        "**Why:** 用户不希望每次都要手动说\"用那个 skill\"。这些 skill 的触发条件是确定性的，应该自动执行。",
        "",
        "**How to apply:** 在每次对话中，判断当前任务是否命中上述触发条件。命中即自动调用相关 skill，无需询问。多个 skill 同时命中时，按优先级顺序执行。",
        "",
    ]

    content = '\n'.join(lines) + '\n'

    mem_path = _resolve_memory_path()
    if not mem_path:
        print("  Could not find memory directory. Skipping sync.")
        return False

    mem_path.parent.mkdir(parents=True, exist_ok=True)
    with open(mem_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"  Synced {len(routes)} routes to {mem_path}")
    return True


# --- Category Mapping --------------------------------------------------------

def load_category_mapping():
    mapping = {}
    if not MAPPING_FILE.exists():
        return mapping
    with open(MAPPING_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '|' in line:
                skill, category = line.split('|', 1)
                mapping[skill.strip()] = category.strip()
    return mapping


# --- Similarity Engine -------------------------------------------------------

def category_signature(cat_name, skills_in_cat):
    sig_words = set()
    sig_words.update(w.lower() for w in re.findall(r'[A-Za-z]+', cat_name))
    for s in skills_in_cat:
        sig_words.update(w.lower() for w in re.findall(r'[a-z]+', s['name'].replace('-', ' ')))
        if s.get('description'):
            sig_words.update(w.lower() for w in re.findall(r'[a-z]{5,}', s['description']))
    stop = {'about', 'when', 'this', 'that', 'with', 'from', 'your', 'have',
            'their', 'what', 'they', 'them', 'then', 'than', 'just', 'also',
            'more', 'some', 'into', 'over', 'after', 'before', 'which', 'other'}
    sig_words -= stop
    return sig_words


def compute_similarity(skill, cat_name, cat_signature):
    skill_text = (skill['name'] + ' ' + skill.get('description', '')).lower()
    skill_words = set(re.findall(r'[a-z]{4,}', skill_text))
    if not cat_signature or not skill_words:
        return 0.0
    overlap = skill_words & cat_signature
    return len(overlap) / max(len(skill_words), 1)


def best_category_match(skill, existing_categories):
    best_cat = None
    best_score = 0.0
    for cat_name, cat_sig in existing_categories.items():
        score = compute_similarity(skill, cat_name, cat_sig)
        if score > best_score:
            best_score = score
            best_cat = cat_name
    if best_score >= SIMILARITY_THRESHOLD:
        return best_cat, best_score
    return None, best_score


def generate_category_name(skill):
    desc = skill.get('description', '')
    name = skill['name']
    domain_patterns = [
        (r'(?:automation|browser|testing|scraping|crawling|extraction)', 'Automation & Tools'),
        (r'(?:security|vulnerability|auth|permission)', 'Security & Permissions'),
        (r'(?:database|sql|storage|cache|data)', 'Data & Storage'),
        (r'(?:api|sdk|integration|service)', 'API & Integration'),
        (r'(?:deploy|ci/cd|pipeline|build|release)', 'Deploy & DevOps'),
        (r'(?:docs|documentation|writing|content)', 'Documentation & Content'),
        (r'(?:monitor|logging|alert|observe)', 'Monitoring & Observability'),
        (r'(?:ai|ml|model|training|inference)', 'AI & Machine Learning'),
        (r'(?:mobile|ios|android|react native)', 'Mobile Development'),
        (r'(?:game|unity|unreal|graphics)', 'Game Development'),
        (r'(?:design|ui|ux|css|style|theme)', 'Design & UI'),
        (r'(?:linux|windows|mac|os|system)', 'System & Platform'),
    ]
    combined = (desc + ' ' + name).lower()
    for pattern, cat_name in domain_patterns:
        if re.search(pattern, combined):
            return cat_name
    words = name.replace('-', ' ').replace('_', ' ').split()
    significant = [w for w in words if w not in ('the', 'a', 'an', 'for', 'with', 'and', 'or', 'to')]
    if significant:
        return ' '.join(w.capitalize() for w in significant[:3])
    return "Other Tools"


# --- Core Regeneration -------------------------------------------------------

def regenerate(dry_run=False, verbose=False, with_usage=False, suggest_routes_flag=False):
    stats = {"local_scanned": 0, "builtin_added": 0, "new_categories": [],
             "total": 0, "deleted": 0}

    # Step 0: Load pending deletes
    pending_deletes = load_pending_deletes()
    if pending_deletes:
        print(f"[0] Pending deletes: {len(pending_deletes)} skills marked for removal")
        for name in pending_deletes:
            skill_dir = SKILLS_DIR / name
            if skill_dir.exists():
                print(f"    DELETING: {skill_dir}")
                import shutil
                try:
                    shutil.rmtree(skill_dir)
                    stats["deleted"] += 1
                    print(f"    -> Deleted {skill_dir}")
                except Exception as e:
                    print(f"    [ERROR] Failed to delete {skill_dir}: {e}")
            else:
                print(f"    SKIP: {skill_dir} not found")
        # Clear pending deletes file
        if not dry_run:
            PENDING_DELETES.write_text("# Pending skill deletions (processed)\n", encoding='utf-8')

    # Step 1: Scan local SKILL.md files
    print("[1/5] Scanning local skills...")
    local_skills = []
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        if skill_dir.name == "skill-catalog":
            continue

        parsed = parse_skill_md(skill_md)
        if parsed:
            parsed['trigger_keywords'] = extract_keywords(parsed['description'])
            local_skills.append(parsed)
            stats["local_scanned"] += 1
            if verbose:
                print(f"  + {parsed['name']}")

    print(f"  -> Found {stats['local_scanned']} local skills")

    # Step 2: Load manual category mapping
    print("[2/5] Loading category mapping...")
    manual_mapping = load_category_mapping()
    print(f"  -> {len(manual_mapping)} manual assignments loaded")

    # Step 3: Load existing categories as reference
    existing_categories = {}
    if CATALOG_JSON.exists():
        try:
            with open(CATALOG_JSON, 'r', encoding='utf-8') as f:
                old_data = json.load(f)
            for cat in old_data.get('categories', []):
                existing_categories[cat['name']] = {
                    'id': cat.get('id', re.sub(r'[^a-z0-9]+', '-', cat['name'].lower())),
                    'description': cat.get('description', ''),
                    'skills': cat.get('skills', [])
                }
        except Exception:
            pass

    # Step 4: Categorize
    print("[3/5] Categorizing skills...")
    categorized = defaultdict(list)
    new_categories = set()

    all_defined_cats = set(manual_mapping.values())
    cat_signatures = {}
    for cat_name in all_defined_cats:
        cat_signatures[cat_name] = category_signature(cat_name, [])
    for cat_name, cat_data in existing_categories.items():
        cat_signatures[cat_name] = category_signature(cat_name, cat_data.get('skills', []))

    for skill in local_skills:
        name = skill['name']
        if name in manual_mapping:
            cat = manual_mapping[name]
            categorized[cat].append(skill)
            if verbose:
                print(f"  |-> {name} -> [{cat}] (manual)")
            continue
        matched_cat, score = best_category_match(skill, cat_signatures)
        if matched_cat:
            categorized[matched_cat].append(skill)
            if verbose:
                print(f"  |-> {name} -> [{matched_cat}] ({score:.0%})")
            continue
        new_cat = generate_category_name(skill)
        categorized[new_cat].append(skill)
        new_categories.add(new_cat)
        if verbose:
            print(f"  |-> {name} -> [{new_cat}] (NEW, {score:.0%})")

    # Step 5: Add built-in skills
    print("[4/5] Adding built-in skills...")
    for name, info in BUILTIN_SKILLS.items():
        skill = {
            "name": name,
            "description": info["description"],
            "source": "builtin",
            "file_path": None,
            "trigger_keywords": info["trigger_keywords"],
            "body": None,
            "body_word_count": 0,
            "allowed_tools": [],
        }
        if name in manual_mapping:
            cat = manual_mapping[name]
        else:
            matched_cat, score = best_category_match(skill, cat_signatures)
            if matched_cat:
                cat = matched_cat
            else:
                cat = generate_category_name(skill)
                new_categories.add(cat)
        categorized[cat].append(skill)
        stats["builtin_added"] += 1

    # Step 5.5: Scan transcripts for usage data
    usage_data = {}
    route_suggestions = None
    if with_usage:
        print("[5/5] Scanning transcripts for usage data...")
        usage_data = scan_transcripts(capture_triggers=suggest_routes_flag)
        total_calls = sum(u["count"] for u in usage_data.values())
        skills_with_usage = len(usage_data)
        print(f"  -> Found {total_calls} total calls across {skills_with_usage} skills")
        if verbose:
            for name, u in sorted(usage_data.items(), key=lambda x: -x[1]["count"])[:10]:
                print(f"     {name}: {u['count']} calls, last: {u['last_used'][:16] if u['last_used'] else 'N/A'}")

        if suggest_routes_flag:
            memory_path = _resolve_memory_path()
            route_suggestions = suggest_routes(usage_data, str(memory_path) if memory_path else None)
            print(f"\n  Route suggestions ({len(route_suggestions['suggestions'])} skills):")
            for s in route_suggestions["suggestions"]:
                best = s["suggested_triggers"][0]
                tag = "UNIQUE" if best["unique"] else f"conflicts: {', '.join(best['conflict_with'][:2])}"
                print(f"    {s['skill']} ({s['count']} calls): \"{best['phrase']}\" [{tag}]")
            if route_suggestions["memory_entries"]:
                print(f"\n  Suggested memory entries (append to skill_auto_trigger.md):")
                for entry in route_suggestions["memory_entries"]:
                    print(f"    {entry}")
    else:
        print("[5/5] Skipping transcript scan (use --with-usage to enable)")

    # Step 6: Stats
    stats["new_categories"] = list(new_categories)
    stats["total"] = sum(len(skills) for skills in categorized.values())
    if new_categories:
        print(f"\n  |-> New categories: {', '.join(sorted(new_categories))}")
    print(f"\n  |-> {stats['total']} skills, {len(categorized)} categories "
          f"({stats['local_scanned']} local + {stats['builtin_added']} builtin)")
    if stats["deleted"]:
        print(f"     Deleted: {stats['deleted']} skills")

    if dry_run:
        print("\n  [DRY RUN] No files written.")
        return categorized

    # Step 7: Generate catalog.json
    print("\n|-> Generating catalog.json...")
    categories_out = []
    cat_id_counter = {}

    for cat_name, skills in sorted(categorized.items()):
        cat_id = re.sub(r'[^a-z0-9]+', '-', cat_name.lower()).strip('-')
        base_id = cat_id
        counter = 1
        while cat_id in cat_id_counter:
            cat_id = f"{base_id}-{counter}"
            counter += 1
        cat_id_counter[cat_id] = True

        if cat_name in existing_categories:
            cat_desc = existing_categories[cat_name].get('description',
                f"Skills for {cat_name.lower()}.")
        else:
            cat_desc = f"Skills for {cat_name.lower()}."

        # Attach usage data and routing info to skills
        skills_out = []
        for s in sorted(skills, key=lambda x: x['name']):
            skill_entry = dict(s)
            if with_usage and s['name'] in usage_data:
                skill_entry['usage'] = usage_data[s['name']]
            else:
                skill_entry['usage'] = None
            # Attach routing suggestion if available
            if route_suggestions:
                for sug in route_suggestions.get("suggestions", []):
                    if sug["skill"] == s['name']:
                        skill_entry['routing'] = {
                            "suggested": True,
                            "triggers": sug["suggested_triggers"]
                        }
                        break
                if 'routing' not in skill_entry:
                    skill_entry['routing'] = None
            else:
                skill_entry['routing'] = None
            skills_out.append(skill_entry)

        categories_out.append({
            "id": cat_id,
            "name": cat_name,
            "description": cat_desc,
            "skill_count": len(skills_out),
            "skills": skills_out
        })

    existing_routes = load_existing_routes()

    catalog = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "total_skills": stats["total"],
            "local_skills": stats["local_scanned"],
            "builtin_skills": stats["builtin_added"],
            "category_count": len(categories_out),
            "has_usage_data": with_usage,
            "has_routes": len(existing_routes) > 0,
        },
        "memory_routes": existing_routes,
        "categories": categories_out
    }

    with open(CATALOG_JSON, 'w', encoding='utf-8') as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
    print(f"  -> Wrote {CATALOG_JSON}")

    # Step 8: Generate catalog.md
    print("|-> Generating catalog.md...")
    md = generate_markdown(catalog)
    with open(CATALOG_MD, 'w', encoding='utf-8') as f:
        f.write(md)
    print(f"  -> Wrote {CATALOG_MD}")

    # Step 9: Generate catalog.html
    print("|-> Generating catalog.html...")
    html = generate_html(catalog)
    with open(CATALOG_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  -> Wrote {CATALOG_HTML}")

    print("\n[OK] Regeneration complete!")
    return categorized


# --- Markdown Generator ------------------------------------------------------

def generate_markdown(catalog):
    lines = [
        "# Claude Code Skill Catalog",
        "",
        f"**{catalog['metadata']['total_skills']} skills** across "
        f"**{catalog['metadata']['category_count']} categories** - "
        f"Generated {catalog['metadata']['generated_at']} - "
        f"[Open Interactive Viewer](catalog.html)",
        "",
        "> **How to use:** Browse by category, find the right skill, then invoke with `/skill-name`.",
        "",
        "## Table of Contents",
        "",
        "| # | Category | Count |",
        "|---|----------|-------|",
    ]
    for idx, cat in enumerate(catalog['categories'], 1):
        anchor = cat['name'].lower().replace(' & ', '-').replace(' ', '-').replace('(', '').replace(')', '')
        lines.append(f"| {idx} | [{cat['name']}](#{idx}-{anchor}) | {cat['skill_count']} |")
    lines.append("")

    for idx, cat in enumerate(catalog['categories'], 1):
        lines.extend(["---", "", f"## {idx}. {cat['name']}", "", cat['description'], ""])
        for skill in cat['skills']:
            badge = "local" if skill['source'] == 'local' else 'builtin'
            badge_color = "green" if badge == "local" else "yellow"
            lines.append(f"### `{skill['name']}` ![{badge}](https://img.shields.io/badge/{badge}-{badge_color})")
            lines.append("")
            lines.append(f"> {skill['description']}")
            lines.append("")
            lines.append(f"- **Invoke:** `/{skill['name']}`")
            if skill.get('trigger_keywords'):
                lines.append(f"- **Triggers:** {', '.join(skill['trigger_keywords'][:5])}")
            if skill.get('file_path'):
                lines.append(f"- **File:** `{skill['file_path']}`")
            else:
                lines.append("- **Source:** built-in (no local file)")
            if skill.get('usage') and skill['usage'].get('count'):
                lines.append(f"- **Called:** {skill['usage']['count']} times, last: {skill['usage'].get('last_used', 'N/A')[:16]}")
            lines.append("")

    lines.extend([
        "---", "", "## Legend", "",
        "| Badge | Meaning |",
        "|-------|---------|",
        "| ![local](https://img.shields.io/badge/local-green) | Installed locally in `~/.claude/skills/` |",
        "| ![builtin](https://img.shields.io/badge/builtin-yellow) | Built-in / plugin skill (no local file) |",
        "",
    ])
    return '\n'.join(lines)


# --- HTML Generator ----------------------------------------------------------

def generate_html(catalog):
    json_str = json.dumps(catalog, ensure_ascii=False)

    # Always generate from the template (with __CATALOG_JSON__ placeholder)
    if HTML_TEMPLATE.exists():
        with open(HTML_TEMPLATE, 'r', encoding='utf-8') as f:
            html = f.read()
        return html.replace('__CATALOG_JSON__', json_str)

    # Fallback: use catalog.html as template (first run after write_html.py)
    if CATALOG_HTML.exists():
        with open(CATALOG_HTML, 'r', encoding='utf-8') as f:
            html = f.read()
        if '__CATALOG_JSON__' in html:
            return html.replace('__CATALOG_JSON__', json_str)

    # Absolute fallback
    return f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Skill Catalog</title></head>
<body><pre id="data"></pre>
<script>document.getElementById('data').textContent = JSON.stringify({json_str}, null, 2);</script>
</body></html>'''


# --- CLI Entry Point ---------------------------------------------------------

if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    with_usage = '--with-usage' in sys.argv
    suggest_routes_flag = '--suggest-routes' in sys.argv
    sync_routes_flag = '--sync-routes' in sys.argv

    # Handle --sync-routes standalone
    if sync_routes_flag:
        print("=" * 60)
        print("  Route Sync: routes.json → memory file")
        print("=" * 60)
        sync_routes_to_memory()
        sys.exit(0)

    print("=" * 60)
    print("  Skill Catalog Regenerator")
    print("=" * 60)
    print(f"  Skills dir : {SKILLS_DIR}")
    print(f"  Output dir : {CATALOG_DIR}")
    if with_usage:
        print(f"  Usage scan : ENABLED (scanning {CLAUDE_PROJECTS_DIR})")
    if suggest_routes_flag:
        print(f"  Route suggest : ENABLED (analyzing trigger phrases)")
    if dry_run:
        print("  Mode       : DRY RUN")
    print()

    regenerate(dry_run=dry_run, verbose=verbose, with_usage=with_usage, suggest_routes_flag=suggest_routes_flag)
