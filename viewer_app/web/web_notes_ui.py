"""
This module generates the complete HTML and JavaScript for a sidebar
notes panel that can be embedded in a web view.

It works by serializing an initial Python state dict into a JSON string
that is safely embedded into an inline JavaScript snippet, then
returning a formatted HTML document string that uses this state to drive
a small notes UI.

It contains the helper function _json_for_script for safe JSON-in-JS
embedding and the main function build_notes_ui_html that assembles the
HTML, CSS, and client-side JavaScript.

Within the broader system, this module provides the front-end for
viewing and editing notes tied to a document or project, communicating
with backend endpoints like /toc and /notes and exposing hooks
(setNotesContext, loadNotes) for integration with a parent viewer.
"""

from __future__ import annotations

import json

from viewer_app.runtime.state import StateDict


def _json_for_script(payload: StateDict) -> str:
    """
    Convert a Python payload dict into a JSON string safe for inline
    scripts.

    This helper serializes the payload to JSON and escapes characters
    that would break embedding inside a single-quoted JavaScript string
    literal.

    Args:
        payload (StateDict):
            Original data to serialize into a JSON string suitable for
            use in an inline script context.

    Returns:
        str:
            JSON-encoded string with backslashes, quotes, and newlines
            escaped for safe insertion into a JavaScript snippet.
    """
    try:
        raw: str = json.dumps(payload, ensure_ascii=False)
        return (
            raw.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "")
        )
    except Exception:
        return "{}"


def build_notes_ui_html(initial_state: StateDict) -> str:
    """
    Build an HTML document string for the sidebar notes UI panel.

    This function returns a complete HTML page that initializes the
    notes interface and injects the provided initial state into a
    JavaScript variable.

    Args:
        initial_state (StateDict):
            Application state snapshot used to preconfigure the notes
            UI, including active project, current document, and related
            metadata.

    Returns:
        str:
            Full HTML document markup for rendering the notes interface
            in a web view or embedded browser.
    """
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notes</title>
<style>
  :root {{ --bg:#f8fafc; --surface:#ffffff; --text:#0f172a; --muted:#6b7280; --border:#e5e7eb; --accent:#0ea5e9; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; background: transparent; color: var(--text); }}
  .notes-root {{ padding: 8px 10px 10px; height: 100vh; box-sizing:border-box; }}
  .notes-card {{ height:100%; border:1px solid var(--border); border-radius:12px; background:#ffffff; padding:10px; display:flex; flex-direction:column; gap:8px; }}
  .sidebar-title {{ font-size:10px; color:#6b7280; letter-spacing:0.08em; text-transform:uppercase; font-weight:700; }}
  .notes-context {{ font-size:11px; color:var(--muted); margin:0; word-break:break-word; }}
  .notes-scope {{ margin-top:6px; }}
  .notes-scope select {{ width:100%; padding:7px 9px; border:1px solid var(--border); border-radius:8px; background:#f9fafb; font-size:12px; }}
  .notes-scope select:focus {{ outline:none; border-color:var(--accent); box-shadow:0 0 0 2px rgba(14,165,233,0.25); background:#ffffff; }}
  .notes-editor {{ flex:1; min-height:0; margin-top:4px; display:flex; flex-direction:column; }}
  .notes-editor textarea {{ flex:1; width:100%; resize:none; padding:9px 10px; border-radius:10px; border:1px solid var(--border); background:#ffffff; font-size:13px; line-height:1.5; }}
  .notes-editor textarea:focus {{ outline:none; border-color:var(--accent); box-shadow:0 0 0 2px rgba(14,165,233,0.25); }}
  .notes-footer {{ margin-top:6px; display:flex; align-items:center; justify-content:flex-end; }}
  .btn {{
    padding: 8px 12px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: #f9fafb;
    cursor: pointer;
    font-size: 12px;
    font-weight: 650;
    transition: transform 0.08s, background 0.15s, border-color 0.15s, box-shadow 0.15s;
  }}
  .btn:hover {{ background: #e0f2fe; border-color: rgba(56, 189, 248, 0.8); box-shadow: 0 4px 12px rgba(15,23,42,0.10); }}
  .btn:active {{ transform: translateY(1px); }}
  .btn.primary {{ background: linear-gradient(135deg, #38bdf8, #0ea5e9); border: none; color: #fff; box-shadow: 0 4px 14px rgba(37,99,235,0.25); }}
  .btn.primary:hover {{ box-shadow: 0 6px 18px rgba(37,99,235,0.32); }}
</style>
<script>var INITIAL_STATE = null; try {{ INITIAL_STATE = JSON.parse('{_json_for_script(initial_state)}'); }} catch(e) {{ INITIAL_STATE = null; }}</script>
</head>
<body>
  <div class="notes-root">
    <div class="notes-card">
      <div class="sidebar-title">Notes</div>
      <div class="notes-context" id="ctxChip">—</div>
      <div class="notes-scope">
        <select id="scopeSel">
          <option value="">Whole document</option>
        </select>
      </div>
      <div class="notes-editor">
        <textarea id="ta" placeholder="Write lecture notes or notes for this document."></textarea>
        <div class="notes-footer">
          <button class="btn primary" id="saveBtn">Save</button>
        </div>
      </div>
    </div>
  </div>
  <script>
    var ta = document.getElementById('ta');
    var chip = document.getElementById('ctxChip');
    var scopeSel = document.getElementById('scopeSel');

    // Context overrides sent by the main viewer.
    var notesRootOverride = '';
    var notesPathOverride = '';
    var headings = [];

    function curRoot() {{
      if (notesRootOverride) return notesRootOverride;
      try {{
        if (INITIAL_STATE && INITIAL_STATE.activeProjectRoot) return String(INITIAL_STATE.activeProjectRoot || '');
        if (INITIAL_STATE && INITIAL_STATE.currentDoc && INITIAL_STATE.currentDoc.root) return String(INITIAL_STATE.currentDoc.root || '');
      }} catch(e) {{}}
      return '';
    }}
    function curDocPath() {{
      if (notesPathOverride) return notesPathOverride;
      try {{ return (INITIAL_STATE && INITIAL_STATE.currentDoc && String(INITIAL_STATE.currentDoc.path || '')) || ''; }} catch(e) {{ return ''; }}
    }}

    function updateChip() {{
      var r = curRoot();
      var p = curDocPath();
      chip.textContent = p ? ('Document: ' + p) : (r ? ('Project: ' + r) : 'No document selected');
    }}

    async function loadHeadings() {{
      try {{
        var root = curRoot();
        var doc = curDocPath();
        headings = [];
        if (!root || !doc) return;
        var url = '/toc?path=' + encodeURIComponent(doc) + '&root=' + encodeURIComponent(root);
        var html = await fetch(url).then(function(r) {{ return r.text(); }});
        var box = document.createElement('div');
        box.innerHTML = html || '';
        var items = [];
        box.querySelectorAll('a[href^="#"]').forEach(function(a) {{
          var href = a.getAttribute('href') || '';
          var id = href.replace(/^#/, '').trim();
          var title = (a.textContent || '').trim();
          if (!id) return;
          items.push({{ id: id, title: title || id }});
        }});
        var seen = {{}};
        headings = items.filter(function(it) {{
          if (seen[it.id]) return false;
          seen[it.id] = true;
          return true;
        }});
      }} catch (e) {{
        headings = [];
      }} finally {{
        rebuildScopeOptions();
      }}
    }}

    function rebuildScopeOptions() {{
      if (!scopeSel) return;
      var cur = scopeSel.value || '';
      var opts = '<option value="">Whole document</option>';
      headings.forEach(function(h) {{
        opts += '<option value="' + String(h.id).replace(/"/g,'&quot;') + '">' + String(h.title).replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</option>';
      }});
      scopeSel.innerHTML = opts;
      try {{ scopeSel.value = cur; }} catch (e) {{}}
    }}

    async function loadNotes() {{
      updateChip();
      var root = curRoot();
      var doc = curDocPath();
      var anchor = (scopeSel && scopeSel.value) ? String(scopeSel.value || '') : '';
      try {{
        var url = '/notes?root=' + encodeURIComponent(root) + '&path=' + encodeURIComponent(doc);
        if (anchor) url += '&anchor=' + encodeURIComponent(anchor);
        var r = await fetch(url);
        var j = await r.json();
        ta.value = String((j && j.text) || '');
      }} catch(e) {{
      }}
    }}

    async function saveNotes() {{
      var payload = {{
        root: curRoot(),
        path: curDocPath(),
        anchor: (scopeSel && scopeSel.value) ? String(scopeSel.value || '') : '',
        text: String(ta.value || '')
      }};
      try {{
        await fetch('/notes', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload)
        }});
      }} catch(e) {{
      }}
    }}

    document.getElementById('saveBtn').onclick = saveNotes;
    if (scopeSel) {{
      scopeSel.onchange = function() {{ loadNotes(); }};
      scopeSel.ondblclick = function() {{
        var a = String(scopeSel.value || '').trim();
        if (!a) return;
        try {{
          if (window.parent && window.parent.chatBridge && window.parent.chatBridge.notesGoToAnchor) window.parent.chatBridge.notesGoToAnchor(a);
        }} catch (e) {{}}
      }};
    }}

    // Initial load when the panel opens.
    updateChip();
    loadHeadings().then(function() {{ loadNotes(); }});

    // Expose functions to the parent window (study_md_desk) so
    // document changes can update context and reload notes.
    window.setNotesContext = function(root, path) {{
      notesRootOverride = String(root || '');
      notesPathOverride = String(path || '');
      updateChip();
      loadHeadings().then(function() {{ loadNotes(); }});
    }};
    window.loadNotes = loadNotes;
  </script>
</body></html>"""
