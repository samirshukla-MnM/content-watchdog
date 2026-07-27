"""
Produces both a machine summary and human-readable highlighted HTML of changes.
Word-level highlighting inside modified lines so "exact changes" are visible.
"""
import difflib
import html
import re


def _word_diff(old: str, new: str):
    """Inline word-level highlight for a modified block."""
    o = re.findall(r"\S+|\s+", old)
    n = re.findall(r"\S+|\s+", new)
    sm = difflib.SequenceMatcher(None, o, n)

    old_out, new_out = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        o_chunk = html.escape("".join(o[i1:i2]))
        n_chunk = html.escape("".join(n[j1:j2]))
        if tag == "equal":
            old_out.append(o_chunk)
            new_out.append(n_chunk)
        elif tag == "delete":
            old_out.append(f'<span class="del">{o_chunk}</span>')
        elif tag == "insert":
            new_out.append(f'<span class="ins">{n_chunk}</span>')
        else:
            old_out.append(f'<span class="del">{o_chunk}</span>')
            new_out.append(f'<span class="ins">{n_chunk}</span>')
    return "".join(old_out), "".join(new_out)


def diff(old_text: str, new_text: str, context_lines: int = 2) -> dict:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    sm = difflib.SequenceMatcher(None, old_lines, new_lines)
    similarity = sm.ratio()

    added, removed, modified = [], [], []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            added.extend(new_lines[j1:j2])
        elif tag == "delete":
            removed.extend(old_lines[i1:i2])
        elif tag == "replace":
            o_block = old_lines[i1:i2]
            n_block = new_lines[j1:j2]
            # Pair them up so we can do word-level highlighting
            for k in range(max(len(o_block), len(n_block))):
                o = o_block[k] if k < len(o_block) else ""
                n = n_block[k] if k < len(n_block) else ""
                if o and n:
                    modified.append((o, n))
                elif o:
                    removed.append(o)
                else:
                    added.append(n)

    total = len(added) + len(removed) + len(modified)

    return {
        "changed": total > 0,
        "similarity": round(similarity, 4),
        "change_pct": round((1 - similarity) * 100, 2),
        "added": added,
        "removed": removed,
        "modified": modified,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
            "total": total,
        },
        "unified": "\n".join(
            difflib.unified_diff(
                old_lines, new_lines,
                fromfile="previous", tofile="current",
                lineterm="", n=context_lines,
            )
        ),
    }


def render_diff_html(d: dict, max_items: int = 25) -> str:
    """Email-safe HTML block showing previous vs updated."""
    if not d["changed"]:
        return "<p>No changes.</p>"

    parts = []

    if d["modified"]:
        parts.append('<div class="sec-title">Modified</div>')
        for old, new in d["modified"][:max_items]:
            o_h, n_h = _word_diff(old, new)
            parts.append(
                '<table class="cmp" role="presentation"><tr>'
                f'<td class="lbl">Previous</td><td class="old">{o_h}</td></tr><tr>'
                f'<td class="lbl">Updated</td><td class="new">{n_h}</td>'
                "</tr></table>"
            )
        if len(d["modified"]) > max_items:
            parts.append(
                f'<p class="more">+ {len(d["modified"]) - max_items} more modified blocks</p>'
            )

    if d["added"]:
        parts.append('<div class="sec-title">Added</div>')
        for line in d["added"][:max_items]:
            parts.append(f'<div class="row new">+ {html.escape(line)}</div>')
        if len(d["added"]) > max_items:
            parts.append(f'<p class="more">+ {len(d["added"]) - max_items} more added</p>')

    if d["removed"]:
        parts.append('<div class="sec-title">Removed</div>')
        for line in d["removed"][:max_items]:
            parts.append(f'<div class="row old">− {html.escape(line)}</div>')
        if len(d["removed"]) > max_items:
            parts.append(f'<p class="more">+ {len(d["removed"]) - max_items} more removed</p>')

    return "".join(parts)
