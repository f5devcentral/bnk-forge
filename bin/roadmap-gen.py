#!/usr/bin/env python3
"""Generate docs/ROADMAP.md and docs/roadmap.html from docs/roadmap.yaml.

Single source of truth = docs/roadmap.yaml. This script is idempotent:
running it twice produces identical output. It never reads the generated
files; it only reads the yaml.

Usage:
    backend/.venv/bin/python bin/roadmap-gen.py   # uses the venv with PyYAML
    python3 bin/roadmap-gen.py                    # if system python has yaml

See docs/roadmap.yaml for the schema and docs/ROADMAP_PROCESS.md for the
contribution flow.
"""
import os
import re
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "ERROR: PyYAML not found. Run with the repo venv:\n"
        "  backend/.venv/bin/python bin/roadmap-gen.py\n"
    )
    sys.exit(1)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YAML_PATH = os.path.join(REPO, "docs", "roadmap.yaml")
MD_PATH = os.path.join(REPO, "docs", "ROADMAP.md")
HTML_PATH = os.path.join(REPO, "docs", "roadmap.html")

GH = os.environ.get("BNKFORGE_REPO_URL", "https://github.com/f5devcentral/bnk-forge")

# ---------------------------------------------------------------------------
# HTML chrome lifted VERBATIM from the original docs/roadmap.html.
# Only the <main> body and the .stats tiles are generated from yaml.
# ---------------------------------------------------------------------------
HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BNK-Forge Roadmap</title>
<style>
  :root{
    --f5red:#E4002B; --f5red-d:#b80023; --ink:#1b1f24; --ink2:#3a424b; --mut:#69727c;
    --bg:#f4f5f7; --card:#ffffff; --bd:#e2e5e9; --link:#0a6ed1;
    --ship:#1a8a4b; --ship-bg:#e8f5ed; --prog:#b5730a; --prog-bg:#fcf2e2;
    --block:#c5221f; --block-bg:#fde9e8; --plan:#1a56c4; --plan-bg:#eaf1fc; --defer:#697079; --defer-bg:#eef0f2;
  }
  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.55 "Helvetica Neue",Helvetica,Arial,"Segoe UI",system-ui,sans-serif;}
  a{color:var(--link);text-decoration:none;} a:hover{text-decoration:underline;}
  header{background:var(--ink);border-bottom:3px solid var(--f5red);color:#fff;padding:22px 40px 20px;}
  .brand{display:flex;align-items:center;gap:14px;}
  .f5mark{background:var(--f5red);color:#fff;font-weight:800;font-size:18px;letter-spacing:.5px;
          padding:6px 10px;border-radius:3px;line-height:1;}
  h1{margin:0;font-size:21px;font-weight:600;letter-spacing:.2px;}
  .sub{color:#aeb6bf;font-size:12.5px;margin-top:3px;}
  .nav{margin-top:18px;display:flex;gap:8px;flex-wrap:wrap;}
  .btn{background:#2a2f36;border:1px solid #3a414a;color:#dfe4ea;padding:7px 14px;border-radius:4px;font-size:12.5px;}
  .btn:hover{border-color:var(--f5red);color:#fff;text-decoration:none;}
  .btn.primary{background:var(--f5red);border-color:var(--f5red);color:#fff;}
  .btn.primary:hover{background:var(--f5red-d);}
  .stats{display:flex;gap:16px;flex-wrap:wrap;max-width:1180px;margin:24px auto 0;padding:0 40px;}
  .stat{flex:1 1 160px;background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:16px 18px;box-shadow:0 1px 2px rgba(20,30,40,.04);}
  .stat .n{font-size:30px;font-weight:700;line-height:1;color:var(--ink);}
  .stat .l{color:var(--mut);font-size:11.5px;margin-top:7px;text-transform:uppercase;letter-spacing:.6px;font-weight:600;}
  .stat .n.red{color:var(--f5red);}
  main{max-width:1180px;margin:0 auto;padding:10px 40px 70px;}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:1.1px;color:var(--ink2);font-weight:700;
     margin:34px 0 14px;padding-left:11px;border-left:4px solid var(--f5red);}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px;}
  .card{background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:16px 18px;box-shadow:0 1px 2px rgba(20,30,40,.04);}
  .card h3{margin:0 0 11px;font-size:14px;font-weight:700;color:var(--ink);}
  ul{margin:0;padding:0;list-style:none;}
  li{padding:7px 0;border-top:1px solid #eef0f2;font-size:13.5px;color:var(--ink2);display:flex;align-items:baseline;gap:8px;}
  li:first-child{border-top:none;}
  .dot{flex:0 0 auto;width:8px;height:8px;border-radius:50%;margin-top:5px;}
  .d-ship{background:var(--ship);} .d-prog{background:var(--prog);} .d-block{background:var(--block);}
  .d-plan{background:var(--plan);} .d-defer{background:var(--defer);}
  .it{flex:1 1 auto;} .ref{font-size:11.5px;color:var(--mut);white-space:nowrap;} .ref a{font-weight:600;}
  code{background:#eef0f2;border:1px solid var(--bd);border-radius:3px;padding:1px 5px;font-size:12.5px;color:var(--ink2);}
  .legend{max-width:1180px;margin:14px auto 0;padding:0 40px;color:var(--mut);font-size:12px;display:flex;gap:18px;flex-wrap:wrap;}
  .legend span{display:inline-flex;align-items:center;gap:6px;}
  footer{max-width:1180px;margin:0 auto;color:var(--mut);font-size:12px;padding:18px 40px 40px;border-top:1px solid var(--bd);}
</style>
</head>
<body>
<header>
  <div class="brand"><span class="f5mark">F5</span>
    <div><h1>BNK-Forge Roadmap</h1>
      <div class="sub">{HUMAN_VIEW_NOTE}</div>
    </div>
  </div>
  <div class="nav">
    <a class="btn primary" href="{GH}/issues">Issues</a>
    <a class="btn" href="{GH}/pulls?q=is%3Apr+is%3Amerged">Merged PRs</a>
    <a class="btn" href="{GH}/issues?q=is%3Aopen+label%3Adynamic-by-default">dynamic-by-default</a>
    <a class="btn" href="./ROADMAP.md">ROADMAP.md</a>
    <a class="btn" href="./roadmap-process.html">How tracking works</a>
  </div>
</header>
"""

HTML_LEGEND = """<div class="legend">
  <span><i class="dot d-prog"></i>In progress</span>
  <span><i class="dot d-plan"></i>Planned</span>
  <span><i class="dot d-block"></i>Blocked</span>
  <span><i class="dot d-defer"></i>Deferred</span>
  <span><i class="dot d-ship"></i>Shipped</span>
</div>
"""

HTML_FOOTER = """<p class="sub" style="margin-top:18px">Strategic/legacy roadmap docs (STRATEGIC_ROADMAP, UX_ROADMAP, PRODUCT_VISION, ENGINEERING_IMPROVEMENTS) predate this file and are mostly shipped &mdash; <code>docs/ROADMAP.md</code> is the single source of truth; see ROADMAP.md &sect;10&ndash;&sect;11.</p>

</main>
<footer>Source of truth: <code>docs/roadmap.yaml</code> &rarr; <code>docs/ROADMAP.md</code> + this page (both generated by <code>bin/roadmap-gen.py</code>) &middot; process: <code>docs/roadmap-process.html</code> &middot; status verified against merged <code>staging</code> + open PRs 2026-06-03. GitHub issues are authoritative for completion.</footer>
</body>
</html>
"""

REF_RE = re.compile(r"^(?:PR\s+)?#(\d+)$")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def ref_to_md(ref):
    """Turn '#159' / 'PR #188' into a markdown link; pass others through."""
    m = REF_RE.match(ref.strip())
    if m:
        return "[%s](%s/issues/%s)" % (ref, GH, m.group(1))
    return ref


def ref_to_html(ref):
    """Turn '#159' / 'PR #188' into an <a>; pass others through (escaped)."""
    m = REF_RE.match(ref.strip())
    if m:
        return '<a href="%s/issues/%s">%s</a>' % (GH, m.group(1), html_escape(ref))
    return html_escape(ref)


def html_escape(text):
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def refs_md(refs):
    return " · ".join(ref_to_md(r) for r in (refs or []))


def refs_html(refs):
    return " &middot; ".join(ref_to_html(r) for r in (refs or []))


def status_emoji(legend, key):
    if not key:
        return ""
    e = legend.get(key, {})
    return ("%s %s" % (e.get("emoji", ""), e.get("label", key))).strip()


def status_dot(legend, key):
    if not key:
        return "d-plan"
    return legend.get(key, {}).get("dot", "d-plan")


def md_table_cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def count_status(sections, key):
    n = 0
    for s in sections:
        for it in s.get("items") or []:
            if it.get("status") == key:
                n += 1
    return n


# ---------------------------------------------------------------------------
# markdown generation
# ---------------------------------------------------------------------------
def gen_md(data):
    meta = data["meta"]
    legend = data["status_legend"]
    out = []
    out.append("# %s\n" % meta["title"])
    out.append(meta["intro_md"].rstrip() + "\n")
    out.append("---\n")

    for sec in data["sections"]:
        out.append("## %s. %s\n" % (sec["number"], sec["heading"]))
        if sec.get("intro"):
            out.append(sec["intro"].rstrip() + "\n")

        render = sec.get("render", "table")
        items = sec.get("items") or []

        if render == "raw":
            out.append(sec.get("raw_md", "").rstrip() + "\n")

        elif render == "table":
            cols = sec.get("columns", ["Item", "Status", "Issues / PRs", "What remains"])
            out.append("| " + " | ".join(cols) + " |")
            out.append("|" + "|".join(["------"] * len(cols)) + "|")
            for it in items:
                row = [
                    md_table_cell("**%s**" % it["title"]),
                    md_table_cell(status_emoji(legend, it.get("status"))),
                    md_table_cell(refs_md(it.get("refs"))),
                    md_table_cell(it.get("note", "")),
                ]
                out.append("| " + " | ".join(row) + " |")
            out.append("")

        elif render == "cards":
            # group by `group`, emit ### subheading per group
            groups = []
            seen = {}
            for it in items:
                g = it.get("group", "")
                if g not in seen:
                    seen[g] = []
                    groups.append(g)
                seen[g].append(it)
            for g in groups:
                if g:
                    out.append("### %s\n" % g)
                for it in seen[g]:
                    line = "- "
                    em = status_emoji(legend, it.get("status"))
                    if em:
                        line += em + " — "
                    line += "**%s**" % it["title"]
                    if it.get("note"):
                        line += " — %s" % it["note"]
                    r = refs_md(it.get("refs"))
                    if r:
                        line += " (%s)" % r
                    out.append(line)
                out.append("")

        elif render == "bullets":
            for it in items:
                line = "- "
                em = status_emoji(legend, it.get("status"))
                if em:
                    line += em + " "
                line += "**%s**" % it["title"]
                if it.get("note"):
                    line += " — %s" % it["note"]
                r = refs_md(it.get("refs"))
                if r:
                    line += " (%s)" % r
                out.append(line)
            if items:
                out.append("")

        if sec.get("outro"):
            out.append(sec["outro"].rstrip() + "\n")

    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# html generation
# ---------------------------------------------------------------------------
def html_li(legend, it):
    dot = status_dot(legend, it.get("status"))
    title = html_escape(it["title"])
    parts = ['<li><i class="dot %s"></i><span class="it">%s</span>' % (dot, title)]
    r = refs_html(it.get("refs"))
    if r:
        parts.append('<span class="ref">%s</span>' % r)
    parts.append("</li>")
    return "".join(parts)


def html_cards_for_section(legend, sec):
    """Return list of card HTML strings for a section (one per group, or one)."""
    items = sec.get("items") or []
    cards = []

    if not items and sec.get("intro"):
        # prose-only section -> single card with the intro as text
        cards.append(
            '<div class="card"><h3>%s</h3><p class="sub" style="color:var(--ink2)">%s</p></div>'
            % (html_escape(sec["heading"]), html_escape(strip_md(sec["intro"])))
        )
        return cards

    # group items
    groups = []
    seen = {}
    for it in items:
        g = it.get("group") or sec["heading"]
        if g not in seen:
            seen[g] = []
            groups.append(g)
        seen[g].append(it)

    for g in groups:
        lis = "\n        ".join(html_li(legend, it) for it in seen[g])
        cards.append(
            '<div class="card">\n      <h3>%s</h3>\n      <ul>\n        %s\n      </ul>\n    </div>'
            % (html_escape(g), lis)
        )
    return cards


def strip_md(text):
    """Light markdown -> plain text for prose-only HTML cards."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = text.replace("\n", " ").strip()
    return text


def gen_html(data):
    meta = data["meta"]
    legend = data["status_legend"]

    # stats tiles
    stat_html = []
    for st in meta.get("stats", []):
        n = st["n"]
        if n == "AUTO:in_progress":
            n = str(count_status(data["sections"], "in_progress"))
        elif n == "AUTO:planned":
            n = str(count_status(data["sections"], "planned"))
        cls = " red" if st.get("red") else ""
        stat_html.append(
            '  <div class="stat"><div class="n%s">%s</div><div class="l">%s</div></div>'
            % (cls, html_escape(n), html_escape(st["label"]))
        )

    # bucket sections by html_group, preserving first-seen order
    buckets = []
    bseen = {}
    for sec in data["sections"]:
        g = sec.get("html_group")
        if not g:
            continue
        if g not in bseen:
            bseen[g] = []
            buckets.append(g)
        bseen[g].append(sec)

    main_parts = ["<main>\n"]
    for g in buckets:
        main_parts.append("  <h2>%s</h2>" % html_escape(g).replace("&mdash;", "—"))
        main_parts.append('  <div class="grid">')
        for sec in bseen[g]:
            for card in html_cards_for_section(legend, sec):
                main_parts.append("    " + card)
        main_parts.append("  </div>\n")

    head = HTML_HEAD.replace("{HUMAN_VIEW_NOTE}", meta.get("human_view_note", "")).replace("{GH}", GH)
    stats = '<div class="stats">\n' + "\n".join(stat_html) + "\n</div>\n"

    return (
        head
        + "\n"
        + stats
        + HTML_LEGEND
        + "\n"
        + "\n".join(main_parts)
        + "\n"
        + HTML_FOOTER
    )


def main():
    with open(YAML_PATH, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    md = gen_md(data)
    html = gen_html(data)

    with open(MD_PATH, "w", encoding="utf-8") as fh:
        fh.write(md)
    with open(HTML_PATH, "w", encoding="utf-8") as fh:
        fh.write(html)

    print("Wrote %s" % MD_PATH)
    print("Wrote %s" % HTML_PATH)
    print(
        "Stats: in_progress=%d planned=%d shipped=%d merged=%d blocked=%d deferred=%d"
        % (
            count_status(data["sections"], "in_progress"),
            count_status(data["sections"], "planned"),
            count_status(data["sections"], "shipped"),
            count_status(data["sections"], "merged"),
            count_status(data["sections"], "blocked"),
            count_status(data["sections"], "deferred"),
        )
    )


if __name__ == "__main__":
    main()
