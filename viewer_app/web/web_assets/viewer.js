(function loadViewerBootstrap() {
  try {
    var bootstrapNode = document.getElementById("mdViewerViewBootstrap");
    var payload = bootstrapNode
      ? JSON.parse(bootstrapNode.textContent || "{}")
      : {};
    window.PROMPT_TEMPLATES = payload.promptTemplates || null;
    window.EXPLAIN_PROMPT_KEY = payload.explainPromptKey || "explain_ru";
  } catch (e) {
    window.PROMPT_TEMPLATES = null;
    window.EXPLAIN_PROMPT_KEY = "explain_ru";
  }
})();
if (typeof window.EXPLAIN_PROMPT_KEY === "undefined")
  window.EXPLAIN_PROMPT_KEY = "explain_ru";

function mdViewerExplainPromptTemplateRaw() {
  var pt = window.PROMPT_TEMPLATES;
  var key = window.EXPLAIN_PROMPT_KEY || "explain_ru";
  if (pt && pt[key]) return pt[key];
  if (pt && pt.explain_ru) return pt.explain_ru;
  return "{CONTENT}";
}

(function initDocScrollRoot() {
  window.mdViewerDocScrollRoot = function () {
    var el = document.querySelector(".md-doc-scroll");
    return el || document.scrollingElement || document.documentElement;
  };
})();

(function initMermaidBlocks() {
  var themeApplied = null;
  function getMermaidTheme() {
    try {
      if (document.documentElement.classList.contains("theme-dark"))
        return "dark";
      if (/[?&]rt=dark(?:&|$)/i.test(String(window.location.search || "")))
        return "dark";
      return "default";
    } catch (e) {
      return "default";
    }
  }
  function clearMermaidOutputs() {
    try {
      document.querySelectorAll(".mermaid").forEach(function (el) {
        el.innerHTML = "";
      });
    } catch (e) {}
  }
  function ensureInit() {
    try {
      if (!window.mermaid) return false;
      var th = getMermaidTheme();
      if (themeApplied === th) return true;
      if (themeApplied !== null) clearMermaidOutputs();
      var init = {
        startOnLoad: false,
        theme: th === "dark" ? "dark" : "default",
        securityLevel: "loose",
        flowchart: { htmlLabels: true, useMaxWidth: true },
      };
      if (th === "dark") {
        init.themeVariables = {
          darkMode: true,
          background: "#0b1220",
          mainBkg: "#1e293b",
          secondBkg: "#0f172a",
          primaryColor: "#1e293b",
          primaryTextColor: "#e2e8f0",
          primaryBorderColor: "#475569",
          secondaryColor: "#0f172a",
          tertiaryColor: "#334155",
          lineColor: "#94a3b8",
          secondaryTextColor: "#cbd5e1",
          tertiaryTextColor: "#94a3b8",
          edgeLabelBackground: "#1e293b",
          titleColor: "#38bdf8",
          nodeTextColor: "#e2e8f0",
          clusterBkg: "rgba(30, 41, 59, 0.45)",
          clusterBorder: "#475569",
          defaultLinkColor: "#94a3b8",
          actorBkg: "#1e293b",
          actorBorder: "#475569",
          actorTextColor: "#e2e8f0",
          signalColor: "#64748b",
          labelBoxBkgColor: "#1e293b",
          labelTextColor: "#e2e8f0",
        };
      }
      mermaid.initialize(init);
      themeApplied = th;
      return true;
    } catch (e) {
      return false;
    }
  }
  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function decodeMermaidSourceFromB64(b64) {
    try {
      if (typeof TextDecoder !== "undefined") {
        var bin = atob(b64);
        var bytes = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return new TextDecoder("utf-8").decode(bytes);
      }
    } catch (e0) {}
    try {
      return decodeURIComponent(
        Array.prototype.map
          .call(atob(b64), function (c) {
            return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
          })
          .join(""),
      );
    } catch (e1) {}
    return "";
  }

  function getMermaidSource(el) {
    try {
      var b64 = el.getAttribute("data-mermaid-src-b64");
      if (b64) {
        var decoded = decodeMermaidSourceFromB64(b64);
        if (decoded) return decoded;
      }
    } catch (e) {}
    return el.getAttribute("data-mermaid-src") || (el.textContent || "").trim();
  }
  async function renderOneFallback(el, idx) {
    if (!el) return;
    var src = getMermaidSource(el);
    if (!src) return;
    try {
      var id =
        "mmd_" +
        String(idx) +
        "_" +
        String(Date.now()) +
        "_" +
        Math.random().toString(36).slice(2, 10);
      var res = await mermaid.render(id, src);
      el.innerHTML = res.svg || "";
      try {
        if (res.bindFunctions) res.bindFunctions(el);
      } catch (e0) {}
    } catch (e1) {
      el.innerHTML =
        '<pre class=\"mermaid-fallback\" style=\"white-space:pre-wrap;padding:10px;border:1px solid rgba(148,163,184,0.45);border-radius:8px;background:rgba(226,232,240,0.35);\"><code>' +
        escapeHtml(src) +
        "</code></pre>";
    }
  }
  async function renderAll() {
    if (!ensureInit()) return;
    try {
      if (typeof mermaid.run === "function") {
        await mermaid.run({ querySelector: ".mermaid", suppressErrors: true });
      }
    } catch (e2) {
      /* continue to per-block fallback */
    }
    var blocks =
      Array.prototype.slice.call(document.querySelectorAll(".mermaid")) || [];
    for (var j = 0; j < blocks.length; j++) {
      var node = blocks[j];
      if (!node.querySelector("svg")) {
        await renderOneFallback(node, j);
      }
    }
  }
  window.mdViewerRenderMermaid = function () {
    try {
      return renderAll();
    } catch (e3) {
      return Promise.resolve();
    }
  };
  function scheduleRender() {
    setTimeout(function () {
      try {
        renderAll();
      } catch (e4) {}
    }, 0);
  }
  if (document.readyState === "complete") scheduleRender();
  else window.addEventListener("load", scheduleRender);
  var _mPoll = 0;
  var _mId = setInterval(function () {
    if (window.mermaid) {
      clearInterval(_mId);
      scheduleRender();
    } else if (++_mPoll > 100) {
      clearInterval(_mId);
    }
  }, 50);
})();

(function () {
  var q = window.location.search;
  if (!q) return;
  document.querySelectorAll('a[href$=".md"]').forEach(function (a) {
    var h = a.getAttribute("href");
    if (h && h.startsWith("/")) return;
    var url = new URL(h, window.location.href);
    url.search = q;
    a.href = url.pathname + url.search;
  });
})();

(function initReaderEnhancements() {
  function isInside(el, selector) {
    try {
      return !!(el && el.closest && el.closest(selector));
    } catch (e) {
      return false;
    }
  }

  function normalizeEscapedLiterals() {
    function shouldSkipParent(el) {
      try {
        if (!el) return true;
        return !!el.closest(
          "pre, code, script, style, .mermaid, mjx-container",
        );
      } catch (e) {
        return true;
      }
    }
    try {
      var walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        {
          acceptNode: function (node) {
            try {
              if (!node || !node.nodeValue) return NodeFilter.FILTER_REJECT;
              if (!/\\[\[\]\(\)\*\{\}_`]/.test(node.nodeValue))
                return NodeFilter.FILTER_REJECT;
              var p = node.parentElement;
              if (!p || shouldSkipParent(p)) return NodeFilter.FILTER_REJECT;
              return NodeFilter.FILTER_ACCEPT;
            } catch (e) {
              return NodeFilter.FILTER_REJECT;
            }
          },
        },
      );
      var n = null;
      var touched = 0;
      while ((n = walker.nextNode())) {
        if (touched++ > 1200) break;
        try {
          n.nodeValue = (n.nodeValue || "").replace(
            /\\([\[\]\(\)\*\{\}_`])/g,
            "$1",
          );
        } catch (e) {}
      }
    } catch (e) {}
  }

  function enhanceDenseEnumerations() {
    function appendWithCodeSpans(parent, text) {
      var re = /(\[[^\]\n]{2,180}\])/g;
      var last = 0;
      var m = null;
      while ((m = re.exec(text))) {
        if (m.index > last)
          parent.appendChild(
            document.createTextNode(text.slice(last, m.index)),
          );
        var code = document.createElement("code");
        code.textContent = m[1];
        parent.appendChild(code);
        last = re.lastIndex;
      }
      if (last < text.length)
        parent.appendChild(document.createTextNode(text.slice(last)));
    }

    var blocks =
      Array.prototype.slice.call(
        document.querySelectorAll(
          ".md-doc p, .details-body p, .md-doc li > p, .details-body li > p",
        ),
      ) || [];
    blocks.forEach(function (node) {
      try {
        if (!node || node.getAttribute("data-enum-enhanced") === "1") return;
        if (
          isInside(
            node,
            "pre, code, table, .stepwise-block, .callout, .doc-nav, mjx-container",
          )
        )
          return;
        var text = (node.textContent || "").replace(/\s+/g, " ").trim();
        if (!text || text.length < 180) return;

        var markerRe = /\((\d{1,2})\)\s+/g;
        var matches = [];
        var mm = null;
        while ((mm = markerRe.exec(text)))
          matches.push({ idx: mm.index, n: mm[1], end: markerRe.lastIndex });
        if (matches.length < 2) return;

        var prefix = text.slice(0, matches[0].idx).trim();
        if (!prefix) return;

        var card = document.createElement("section");
        card.className = "dense-enum-card";

        var title = document.createElement("div");
        title.className = "dense-enum-title";
        title.textContent = prefix;
        card.appendChild(title);

        var ol = document.createElement("ol");
        ol.className = "dense-enum-list";

        for (var i = 0; i < matches.length; i++) {
          var cur = matches[i];
          var next = matches[i + 1];
          var itemText = text
            .slice(cur.end, next ? next.idx : text.length)
            .trim();
          if (!itemText) continue;
          var li = document.createElement("li");
          appendWithCodeSpans(li, itemText);
          ol.appendChild(li);
        }
        if (!ol.children.length) return;
        card.appendChild(ol);

        node.replaceWith(card);
      } catch (e) {}
    });
  }

  function enhanceWrappedMarkers() {
    return;
  }

  function enhanceLongLists() {
    var lists =
      Array.prototype.slice.call(
        document.querySelectorAll(
          "main ul, main ol, body > ul, body > ol, article ul, article ol, .content ul, .content ol, ul, ol",
        ),
      ) || [];
    lists.forEach(function (list) {
      try {
        if (!list || list.getAttribute("data-list-enhanced") === "1") return;
        if (
          isInside(
            list,
            "pre, code, table, nav, .doc-nav, .stepwise-block, details summary",
          )
        )
          return;
        var items = Array.prototype.slice
          .call(list.children || [])
          .filter(function (ch) {
            return ch && ch.tagName && ch.tagName.toLowerCase() === "li";
          });
        if (items.length < 8) return;

        var visible = 6;
        items.forEach(function (li, idx) {
          if (idx >= visible) li.classList.add("list-collapsed-item");
        });

        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "list-expand-toggle";
        btn.textContent = "Show more " + String(items.length - visible);
        btn.setAttribute("data-expanded", "0");
        btn.addEventListener("click", function () {
          var expanded = btn.getAttribute("data-expanded") === "1";
          items.forEach(function (li, idx) {
            if (idx >= visible)
              li.classList.toggle("list-collapsed-item", expanded);
          });
          btn.setAttribute("data-expanded", expanded ? "0" : "1");
          btn.textContent = expanded
            ? "Show more " + String(items.length - visible)
            : "Collapse list";
        });
        list.insertAdjacentElement("afterend", btn);
        list.setAttribute("data-list-enhanced", "1");
      } catch (e) {}
    });
  }

  function enhanceCheckSections() {
    var heads =
      Array.prototype.slice.call(document.querySelectorAll("h2, h3, h4, h5")) ||
      [];
    heads.forEach(function (h) {
      try {
        if (!h || h.getAttribute("data-check-enhanced") === "1") return;
        var t = (h.textContent || "").trim();
        if (!/(self-check|questions|checklist)/i.test(t)) return;
        h.classList.add("study-check-heading");

        var node = h.nextElementSibling;
        while (node && !/^H[1-5]$/.test(node.tagName || "")) {
          if (node.tagName === "OL" || node.tagName === "UL") {
            node.classList.add("study-check-list");
            break;
          }
          node = node.nextElementSibling;
        }
        h.setAttribute("data-check-enhanced", "1");
      } catch (e) {}
    });
  }

  function mountDetailsToolbar() {
    try {
      var details =
        Array.prototype.slice.call(document.querySelectorAll("details")) || [];
      if (details.length < 3) return;
      if (document.getElementById("detailsToolbar")) return;

      var bar = document.createElement("div");
      bar.id = "detailsToolbar";
      bar.className = "details-toolbar";
      bar.innerHTML =
        '<button type="button" data-act="open">Open all answers</button>' +
        '<button type="button" data-act="close">Hide answers</button>';
      bar.addEventListener("click", function (ev) {
        var btn = ev.target;
        if (!btn || !btn.getAttribute) return;
        var act = btn.getAttribute("data-act");
        if (act !== "open" && act !== "close") return;
        details.forEach(function (d) {
          try {
            d.open = act === "open";
          } catch (e) {}
        });
      });
      document.body.appendChild(bar);
    } catch (e) {}
  }

  function enhanceDefinitionLists() {
    var items =
      Array.prototype.slice.call(document.querySelectorAll("li")) || [];
    items.forEach(function (li) {
      try {
        if (!li || li.getAttribute("data-def-enhanced") === "1") return;
        if (isInside(li, "pre, code, table, .stepwise-block, .callout")) return;

        var strong = li.querySelector(":scope > strong");
        if (!strong) return;
        var st = (strong.textContent || "").trim();
        if (!st || st.length > 52) return;

        var full = (li.textContent || "").trim();
        if (!/:/.test(full)) return;

        li.classList.add("def-item");
        strong.classList.add("def-label");

        var html = li.innerHTML;
        var strongEnd = html.indexOf("</strong>");
        if (strongEnd < 0) return;
        var rest = html
          .slice(strongEnd + 9)
          .replace(/^\s*[:\-]\s*/, "")
          .trim();
        if (!rest) return;
        var prefix = html.slice(0, strongEnd + 9);
        li.innerHTML = prefix + '<span class="def-body">' + rest + "</span>";
        li.setAttribute("data-def-enhanced", "1");
      } catch (e) {}
    });
  }

  function mountBackToTop() {
    try {
      var id = "backToTopBtn";
      var btn = document.getElementById(id);
      if (!btn) {
        btn = document.createElement("button");
        btn.id = id;
        btn.type = "button";
        btn.className = "back-to-top";
        btn.textContent = "Top";
        btn.addEventListener("click", function () {
          try {
            var sr = window.mdViewerDocScrollRoot
              ? window.mdViewerDocScrollRoot()
              : document.documentElement;
            if (sr && typeof sr.scrollTo === "function")
              sr.scrollTo({ top: 0, behavior: "smooth" });
            else window.scrollTo({ top: 0, behavior: "smooth" });
          } catch (e) {
            try {
              var sr2 = window.mdViewerDocScrollRoot
                ? window.mdViewerDocScrollRoot()
                : document.documentElement;
              if (sr2) sr2.scrollTop = 0;
              else window.scrollTo(0, 0);
            } catch (e2) {
              window.scrollTo(0, 0);
            }
          }
        });
        document.body.appendChild(btn);
      }
      var onScroll = function () {
        try {
          var el = window.mdViewerDocScrollRoot
            ? window.mdViewerDocScrollRoot()
            : document.scrollingElement || document.documentElement;
          var show = el && el.scrollTop > 800;
          btn.classList.toggle("visible", !!show);
        } catch (e) {}
      };
      var scrollTarget = window.mdViewerDocScrollRoot
        ? window.mdViewerDocScrollRoot()
        : window;
      try {
        scrollTarget.addEventListener("scroll", onScroll, { passive: true });
      } catch (e) {
        window.addEventListener("scroll", onScroll, { passive: true });
      }
      onScroll();
    } catch (e) {}
  }

  function mountSectionJump() {
    try {
      if (document.getElementById("sectionJumpWrap")) return;
      var headings =
        Array.prototype.slice.call(
          document.querySelectorAll("h2[id], h3[id]"),
        ) || [];
      if (headings.length < 6) return;

      var wrap = document.createElement("div");
      wrap.id = "sectionJumpWrap";
      wrap.className = "section-jump-wrap";
      var select = document.createElement("select");
      select.className = "section-jump-select";

      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Jump to section...";
      select.appendChild(placeholder);

      headings.forEach(function (h) {
        var text = (h.textContent || "").trim();
        if (!text) return;
        var op = document.createElement("option");
        op.value = h.id || "";
        op.textContent = (h.tagName === "H3" ? "  - " : "") + text;
        select.appendChild(op);
      });

      select.addEventListener("change", function () {
        var id = select.value;
        if (!id) return;
        var target = document.getElementById(id);
        if (target && target.scrollIntoView) {
          try {
            target.scrollIntoView({ block: "start", behavior: "smooth" });
          } catch (e) {
            target.scrollIntoView();
          }
        }
      });

      wrap.appendChild(select);
      document.body.appendChild(wrap);
    } catch (e) {}
  }

  function run() {
    normalizeEscapedLiterals();
    enhanceDenseEnumerations();
    enhanceLongLists();
    enhanceCheckSections();
    enhanceDefinitionLists();
    mountDetailsToolbar();
    mountBackToTop();
  }

  if (
    document.readyState === "complete" ||
    document.readyState === "interactive"
  )
    run();
  else document.addEventListener("DOMContentLoaded", run);
  window.addEventListener("load", run);
})();

(function () {
  function send(payload) {
    try {
      window.parent.postMessage(payload, "*");
    } catch (e) {}
  }
  function applySettings(data) {
    var root = document.documentElement;
    root.style.setProperty("--reader-scale", String(data.fontScale || 1));
    root.style.setProperty("--reader-max-width", data.maxWidth || "1240px");
    var theme = (data.theme || "").toLowerCase();
    root.classList.toggle("theme-dark", theme === "dark");
    root.classList.toggle("theme-sepia", !!data.sepia);
    var mermaidPromise = Promise.resolve();
    try {
      if (window.mdViewerRenderMermaid)
        mermaidPromise = Promise.resolve(window.mdViewerRenderMermaid());
    } catch (e) {
      mermaidPromise = Promise.resolve();
    }
    mermaidPromise
      .catch(function () {})
      .then(function () {
        return new Promise(function (resolve) {
          requestAnimationFrame(function () {
            requestAnimationFrame(resolve);
          });
        });
      })
      .then(function () {
        send({ type: "doc-render-ready" });
      });
  }
  function estimateReadingTime() {
    var words = ((document.body.innerText || "").trim().match(/\\S+/g) || [])
      .length;
    var mins = Math.max(1, Math.round(words / 180));
    return mins + " min";
  }
  function getHeadings() {
    return Array.prototype.slice
      .call(document.querySelectorAll("h1[id], h2[id], h3[id], h4[id]"))
      .map(function (el) {
        return { id: el.id, title: (el.textContent || "").trim(), top: 0 };
      })
      .filter(function (item) {
        return item.id && item.title;
      });
  }
  function getCurrentHeading() {
    var headings = Array.prototype.slice.call(
      document.querySelectorAll("h1[id], h2[id], h3[id], h4[id]"),
    );
    var current = null;
    var threshold = 140;
    headings.forEach(function (el) {
      var top = el.getBoundingClientRect().top;
      if (top <= threshold) current = el;
    });
    if (!current && headings.length) current = headings[0];
    return current
      ? { id: current.id, title: (current.textContent || "").trim() }
      : null;
  }
  function reportMeta() {
    var titleEl = document.querySelector("h1, h2, h3");
    var current = getCurrentHeading();
    send({
      type: "doc-meta",
      title: titleEl
        ? (titleEl.textContent || "").trim()
        : document.title || "Document",
      section: current ? current.title : "",
      readingTime: estimateReadingTime(),
    });
  }
  function report() {
    var el = window.mdViewerDocScrollRoot
      ? window.mdViewerDocScrollRoot()
      : document.scrollingElement || document.documentElement;
    var max = el.scrollHeight - el.clientHeight;
    var pct = max > 0 ? el.scrollTop / max : 1;
    var current = getCurrentHeading();
    send({ type: "scroll-progress", progress: pct });
    if (current)
      send({ type: "active-section", id: current.id, title: current.title });
  }
  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "viewer-settings") applySettings(e.data);
  });

  (function () {
    var lastSpan = null;
    var lastScopeSpan = null;
    var ttsResumeGlobal = null;

    function shouldSkipNode(el) {
      try {
        if (!el) return false;
        var tag = (el.tagName || "").toLowerCase();
        if (tag === "script" || tag === "style") return true;
        if (tag === "pre") return true;
        if (tag === "code") {
          var x = el;
          while (x) {
            if (x.tagName && String(x.tagName).toLowerCase() === "pre")
              return true;
            x = x.parentElement;
          }
        }
        if (
          el.classList &&
          (el.classList.contains("note-mark") ||
            el.classList.contains("tts-highlight"))
        )
          return true;
      } catch (e) {}
      return false;
    }
    function unwrapSpan(s) {
      try {
        if (!s || !s.parentNode) return;
        var p = s.parentNode;
        while (s.firstChild) p.insertBefore(s.firstChild, s);
        p.removeChild(s);
      } catch (e) {}
    }
    function makeTtsTextWalker() {
      return document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode: function (node) {
          try {
            if (!node || !node.nodeValue) return NodeFilter.FILTER_REJECT;
            var p = node.parentElement;
            while (p) {
              if (shouldSkipNode(p)) return NodeFilter.FILTER_REJECT;
              p = p.parentElement;
            }
            return NodeFilter.FILTER_ACCEPT;
          } catch (e) {
            return NodeFilter.FILTER_REJECT;
          }
        },
      });
    }
    function ttsRangeHasBlock(r) {
      try {
        var frag = r.cloneContents();
        var w = document.createTreeWalker(frag, NodeFilter.SHOW_ELEMENT, null);
        var el = null;
        var bad = {
          P: 1,
          DIV: 1,
          BLOCKQUOTE: 1,
          PRE: 1,
          LI: 1,
          UL: 1,
          OL: 1,
          H1: 1,
          H2: 1,
          H3: 1,
          H4: 1,
          H5: 1,
          H6: 1,
          TABLE: 1,
          THEAD: 1,
          TBODY: 1,
          TR: 1,
          TD: 1,
          TH: 1,
          FIGURE: 1,
          HR: 1,
        };
        while ((el = w.nextNode())) {
          if (bad[el.tagName]) return true;
        }
        return false;
      } catch (e) {
        return true;
      }
    }
    function buildTtsFlat() {
      var walker = makeTtsTextWalker();
      var segs = [];
      var parts = [];
      var acc = 0;
      var n = null;
      while ((n = walker.nextNode())) {
        var s = n.nodeValue || "";
        var L = s.length;
        if (!L) continue;
        segs.push({ node: n, g0: acc, g1: acc + L });
        parts.push(s);
        acc += L;
      }
      return { full: parts.join(""), segs: segs };
    }
    function collectFlatNeedlePositions(full, needle) {
      var matches = [];
      if (!needle || !full) return matches;
      var pos = 0;
      while (pos <= full.length) {
        var idx = full.indexOf(needle, pos);
        if (idx < 0) break;
        matches.push({
          gStart: idx,
          gEnd: idx + needle.length,
          len: needle.length,
        });
        pos = idx + 1;
      }
      return matches;
    }
    function pickTtsMatch(matches, minG) {
      if (!matches.length) return null;
      if (minG == null) return matches[0];
      for (var i = 0; i < matches.length; i++) {
        if (matches[i].gStart >= minG) return matches[i];
      }
      return matches[0];
    }
    function domRangeFromFlat(gStart, gEndExclusive, segs) {
      if (!segs.length || gStart >= gEndExclusive) return null;
      var i = 0;
      var sStart = null;
      var sEnd = null;
      for (i = 0; i < segs.length; i++) {
        var sg = segs[i];
        if (gStart >= sg.g0 && gStart < sg.g1) {
          sStart = { node: sg.node, offset: gStart - sg.g0 };
          break;
        }
      }
      for (i = 0; i < segs.length; i++) {
        var sg2 = segs[i];
        if (gEndExclusive > sg2.g0 && gEndExclusive <= sg2.g1) {
          sEnd = { node: sg2.node, offset: gEndExclusive - sg2.g0 };
          break;
        }
      }
      if (!sStart || !sEnd) return null;
      try {
        var r = document.createRange();
        r.setStart(sStart.node, sStart.offset);
        r.setEnd(sEnd.node, sEnd.offset);
        return r;
      } catch (e) {
        return null;
      }
    }
    function syncSnippetVariants(raw) {
      var vs = [raw];
      var cur = raw.replace(/\s+/g, " ").trim();
      if (cur && cur !== raw) vs.push(cur);
      cur = cur || raw;
      while (cur.length > 14) {
        var sp = cur.lastIndexOf(" ");
        if (sp < 6) break;
        cur = cur.slice(0, sp).trim();
        if (cur.length >= 6) vs.push(cur);
      }
      return vs;
    }
    function findMatchForHighlight(t, minG) {
      var flat = buildTtsFlat();
      var full = flat.full;
      var segs = flat.segs;
      var variants = syncSnippetVariants(t);
      for (var v = 0; v < variants.length; v++) {
        var needle = variants[v];
        if (!needle) continue;
        var all = collectFlatNeedlePositions(full, needle);
        var m = pickTtsMatch(all, minG);
        if (m) {
          var r = domRangeFromFlat(m.gStart, m.gEnd, segs);
          if (r)
            return {
              range: r,
              gStart: m.gStart,
              gEnd: m.gEnd,
            };
        }
      }
      return null;
    }
    function tryApplyTtsHighlightRange(r, className, dataAttr) {
      var span = null;
      var cls = className || "tts-highlight";
      var dKey = dataAttr || "data-tts";
      try {
        span = document.createElement("span");
        span.className = cls;
        try {
          span.setAttribute(dKey, "1");
        } catch (e) {}
        r.surroundContents(span);
        return span;
      } catch (e) {
        try {
          if (ttsRangeHasBlock(r)) return null;
          span = document.createElement("span");
          span.className = cls;
          try {
            span.setAttribute(dKey, "1");
          } catch (e3) {}
          var frag = r.extractContents();
          span.appendChild(frag);
          r.insertNode(span);
          return span;
        } catch (e2) {}
      }
      return null;
    }
    function expandFlatSentenceBounds(full, g0, g1) {
      var s0 = 0;
      var i = 0;
      for (i = g0 - 1; i >= 0; i--) {
        var ch = full[i];
        var nxt = i + 1 < full.length ? full[i + 1] : "";
        if (
          (ch === "." || ch === "!" || ch === "?" || ch === "\u2026") &&
          (i + 1 >= full.length || /\s/.test(nxt))
        ) {
          s0 = i + 1;
          while (s0 < full.length && /\s/.test(full[s0])) s0++;
          break;
        }
      }
      var s1 = full.length;
      for (i = g1; i < full.length; i++) {
        var c2 = full[i];
        if (
          (c2 === "." || c2 === "!" || c2 === "?" || c2 === "\u2026") &&
          (i + 1 >= full.length || /\s/.test(full[i + 1]))
        ) {
          s1 = i + 1;
          break;
        }
      }
      if (s1 < g1) s1 = g1;
      if (s0 > g0) s0 = g0;
      return { s0: s0, s1: s1 };
    }
    function rangeTouchesHeading(range) {
      try {
        if (!range) return false;
        var n = range.startContainer;
        var el = n.nodeType === 3 ? n.parentElement : n;
        while (el) {
          var tg = el.tagName && String(el.tagName).toLowerCase();
          if (/^h[1-6]$/.test(tg)) return true;
          el = el.parentElement;
        }
      } catch (e) {}
      return false;
    }
    function scrollTtsIfNeeded(span, follow) {
      if (!follow || !span || !span.getBoundingClientRect) return;
      try {
        var root = window.mdViewerDocScrollRoot
          ? window.mdViewerDocScrollRoot()
          : null;
        var er = span.getBoundingClientRect();
        if (!root || !root.getBoundingClientRect) {
          span.scrollIntoView({ block: "nearest", behavior: "auto" });
          return;
        }
        var rr = root.getBoundingClientRect();
        var margin = Math.min(100, Math.max(48, rr.height * 0.12));
        if (er.bottom < rr.top + margin || er.top > rr.bottom - margin) {
          span.scrollIntoView({ block: "nearest", behavior: "auto" });
        }
      } catch (e) {
        try {
          span.scrollIntoView({ block: "nearest", behavior: "auto" });
        } catch (e2) {}
      }
    }
    function highlightTextOnce(text, follow) {
      try {
        var t = String(text || "").trim();
        if (!t) return;
        if (lastSpan) {
          unwrapSpan(lastSpan);
          lastSpan = null;
        }
        if (lastScopeSpan) {
          unwrapSpan(lastScopeSpan);
          lastScopeSpan = null;
        }

        var m = findMatchForHighlight(t, ttsResumeGlobal);
        if (!m) {
          ttsResumeGlobal = null;
          m = findMatchForHighlight(t, null);
        }
        if (!m || !m.range) return;

        var flat = buildTtsFlat();
        var sent = expandFlatSentenceBounds(flat.full, m.gStart, m.gEnd);
        var useScope =
          (sent.s0 < m.gStart || sent.s1 > m.gEnd) &&
          !rangeTouchesHeading(m.range);
        var span = null;
        if (useScope) {
          var rS = domRangeFromFlat(sent.s0, sent.s1, flat.segs);
          if (rS) {
            lastScopeSpan = tryApplyTtsHighlightRange(
              rS,
              "tts-highlight-sentence",
              "data-tts-scope",
            );
          }
          flat = buildTtsFlat();
        }
        var rC = domRangeFromFlat(m.gStart, m.gEnd, flat.segs);
        if (rC)
          span = tryApplyTtsHighlightRange(rC, "tts-highlight", "data-tts");
        if (!span) {
          var flat2 = buildTtsFlat();
          var r2 = domRangeFromFlat(m.gStart, m.gEnd, flat2.segs);
          if (r2)
            span = tryApplyTtsHighlightRange(r2, "tts-highlight", "data-tts");
        }
        if (!span) {
          if (lastScopeSpan) {
            unwrapSpan(lastScopeSpan);
            lastScopeSpan = null;
          }
          return;
        }

        lastSpan = span;
        ttsResumeGlobal = m.gEnd;
        scrollTtsIfNeeded(span, follow);
      } catch (e) {}
    }
    function clearTtsHighlight() {
      try {
        if (lastSpan) {
          unwrapSpan(lastSpan);
          lastSpan = null;
        }
        if (lastScopeSpan) {
          unwrapSpan(lastScopeSpan);
          lastScopeSpan = null;
        }
        ttsResumeGlobal = null;
      } catch (e) {}
    }
    window.addEventListener("message", function (ev) {
      try {
        if (!ev || !ev.data) return;
        if (ev.data.type === "tts-highlight-clear") {
          clearTtsHighlight();
          return;
        }
        if (ev.data.type === "tts-highlight") {
          highlightTextOnce(ev.data.text, !!ev.data.follow);
        }
      } catch (e) {}
    });
  })();
  (function bindDocScroll() {
    var t = window.mdViewerDocScrollRoot
      ? window.mdViewerDocScrollRoot()
      : window;
    try {
      t.addEventListener("scroll", report, { passive: true });
    } catch (e) {
      window.addEventListener("scroll", report, { passive: true });
    }
  })();
  window.addEventListener("load", function () {
    reportMeta();
    report();
  });
  setTimeout(function () {
    reportMeta();
    report();
  }, 100);
  setTimeout(function () {
    reportMeta();
    report();
  }, 500);
  var lastAskPrompt = "";
  var lastSelText = "";
  document.addEventListener("contextmenu", function (e) {
    var sel = (window.getSelection && window.getSelection().toString()) || "";
    sel = sel.trim();
    e.preventDefault();
    var selRange = null;
    try {
      var ws = window.getSelection && window.getSelection();
      if (ws && ws.rangeCount) selRange = ws.getRangeAt(0).cloneRange();
    } catch (e) {
      selRange = null;
    }
    var text = sel;
    lastSelText = text || "";
    function isInExample(node) {
      try {
        var el = node && (node.nodeType === 1 ? node : node.parentElement);
        while (el) {
          var tag = (el.tagName || "").toLowerCase();
          if (tag === "pre" || tag === "code") return true;
          el = el.parentElement;
        }
      } catch (e) {}
      return false;
    }
    var noteableSel = true;
    try {
      if (sel && selRange) {
        if (
          isInExample(selRange.startContainer) ||
          isInExample(selRange.endContainer)
        )
          noteableSel = false;
      } else if (sel) {
        var wsel = window.getSelection && window.getSelection();
        if (wsel && wsel.anchorNode && isInExample(wsel.anchorNode))
          noteableSel = false;
      }
    } catch (e) {
      noteableSel = true;
    }
    var full = (
      (document.body &&
        (document.body.innerText || document.body.textContent)) ||
      ""
    ).trim();
    if (!full && !text) return;
    var h = document.querySelector("h1, h2, h3");
    var title = h ? (h.textContent || "").trim() : document.title || "";
    var rawTpl = mdViewerExplainPromptTemplateRaw();
    var tpl = String(rawTpl || "");
    if (tpl.indexOf("{CONTENT}") === -1) tpl = "{CONTENT}";
    var sectionText = full.slice(0, 20000);
    var highlight = text ? text.slice(0, 4000) : "";
    var bodyText = "Full text of the current section:\\n\\n" + sectionText;
    if (highlight) {
      bodyText +=
        "\\n\\n---\\n\\nSelected fragment (place extra emphasis on it):\\n\\n" +
        highlight;
    }
    var topic = title || "";
    var prompt = tpl
      .split("{TITLE}")
      .join(topic || "Untitled")
      .split("{CONTENT}")
      .join(bodyText);
    lastAskPrompt = prompt;
    function nodePath(node) {
      var path = [];
      var cur = node;
      while (cur && cur !== document.body) {
        var parent = cur.parentNode;
        if (!parent) break;
        var idx = Array.prototype.indexOf.call(parent.childNodes, cur);
        path.unshift(idx);
        cur = parent;
      }
      return path;
    }
    function _skipTag(el) {
      try {
        if (!el) return false;
        var tag = (el.tagName || "").toLowerCase();
        return (
          tag === "pre" || tag === "code" || tag === "script" || tag === "style"
        );
      } catch (e) {
        return false;
      }
    }
    function _acceptTextNode(node) {
      try {
        if (!node || !node.nodeValue) return false;
        var p = node.parentElement;
        while (p) {
          if (_skipTag(p)) return false;
          if (p.classList && p.classList.contains("note-mark")) return false;
          p = p.parentElement;
        }
        return true;
      } catch (e) {
        return false;
      }
    }
    function _globalOffsetFor(container, localOffset) {
      try {
        var target = container;
        var off = Number(localOffset || 0);
        var acc = 0;
        var walker = document.createTreeWalker(
          document.body,
          NodeFilter.SHOW_TEXT,
          {
            acceptNode: function (n) {
              return _acceptTextNode(n)
                ? NodeFilter.FILTER_ACCEPT
                : NodeFilter.FILTER_REJECT;
            },
          },
        );
        var n = null;
        while ((n = walker.nextNode())) {
          if (n === target) return acc + off;
          acc += (n.nodeValue || "").length;
          if (acc > 50_000_000) break;
        }
      } catch (e) {}
      return null;
    }
    function serializeRange(r) {
      try {
        if (!r) return null;
        var sc = r.startContainer,
          ec = r.endContainer;
        if (!sc || !ec) return null;
        var sg = _globalOffsetFor(sc, r.startOffset || 0);
        var eg = _globalOffsetFor(ec, r.endOffset || 0);
        return {
          startPath: nodePath(sc),
          startOffset: r.startOffset || 0,
          endPath: nodePath(ec),
          endOffset: r.endOffset || 0,
          startGlobal: typeof sg === "number" ? sg : null,
          endGlobal: typeof eg === "number" ? eg : null,
        };
      } catch (e) {
        return null;
      }
    }
    window.mdViewerSerializeRange = serializeRange;
    var selRangeSig = serializeRange(selRange);

    var clips = window._mdViewerClips || [];
    var clipMatch = null;
    if (sel && noteableSel) {
      clips.some(function (c) {
        if (!c) return false;
        var cr = c.range || null;
        if (
          selRangeSig &&
          cr &&
          JSON.stringify(cr) === JSON.stringify(selRangeSig)
        ) {
          clipMatch = c;
          return true;
        }
        var q = String(c.quote || "").trim();
        if (!clipMatch && q && q === sel) {
          clipMatch = c;
          return true;
        }
        return false;
      });
    }

    var menu = document.getElementById("askInChatCtxMenu");
    if (!menu) {
      menu = document.createElement("div");
      menu.id = "askInChatCtxMenu";
      menu.className = "md-ctx-menu";
      document.body.appendChild(menu);
    } else {
      menu.className = "md-ctx-menu";
    }
    var itemsHtml = "";
    if (clipMatch && noteableSel) {
      itemsHtml +=
        '<a class="md-ctx-menu-item" href="#" data-act="note-edit">Edit note</a><a class="md-ctx-menu-item" href="#" data-act="note-del">Delete note</a>';
    } else if (sel && noteableSel) {
      itemsHtml +=
        '<a class="md-ctx-menu-item" href="#" data-act="note">Add note</a>';
    }
    if (sel) {
      itemsHtml +=
        '<a class="md-ctx-menu-item" href="#" data-act="copy">Copy</a><a class="md-ctx-menu-item" href="#" data-act="prompt">Generate prompt to clipboard</a>';
      if (noteableSel)
        itemsHtml +=
          '<a class="md-ctx-menu-item" href="#" data-act="tts">Speak</a>';
    }
    menu.innerHTML = itemsHtml;
    menu.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        var act = a.getAttribute("data-act") || "";
        try {
          if (act === "note") {
            var cur = null;
            try {
              cur = getCurrentHeading && getCurrentHeading();
            } catch (e) {
              cur = null;
            }
            window.parent.postMessage(
              {
                type: "add-note",
                text: lastSelText,
                range: selRangeSig,
                headingId: cur && cur.id ? cur.id : "",
                headingTitle: cur && cur.title ? cur.title : "",
              },
              "*",
            );
          } else if (act === "note-edit" && clipMatch) {
            window.parent.postMessage(
              {
                type: "add-note",
                op: "edit",
                text: String(clipMatch.quote || ""),
                note: String(clipMatch.note || ""),
                range: clipMatch.range || selRangeSig,
                headingId: String(clipMatch.headingId || ""),
                headingTitle: String(clipMatch.headingTitle || ""),
              },
              "*",
            );
          } else if (act === "note-del" && clipMatch) {
            window.parent.postMessage(
              {
                type: "delete-note",
                quote: String(clipMatch.quote || ""),
                range: clipMatch.range || selRangeSig,
                headingId: String(clipMatch.headingId || ""),
              },
              "*",
            );
          } else if (act === "copy") {
            try {
              window.parent.postMessage(
                { type: "copy-text", text: String(lastSelText || "") },
                "*",
              );
            } catch (e) {}
          } else if (act === "tts") {
            window.parent.postMessage(
              { type: "tts-speak", text: lastSelText },
              "*",
            );
          } else {
            window.parent.postMessage(
              { type: "ask-in-chat", prompt: lastAskPrompt },
              "*",
            );
          }
        } catch (err) {}
        menu.style.display = "none";
      });
    });
    menu.style.left = e.clientX + "px";
    menu.style.top = e.clientY + "px";
    menu.style.display = "block";
    var hide = function () {
      menu.style.display = "none";
      document.removeEventListener("click", hide);
    };
    setTimeout(function () {
      document.addEventListener("click", hide);
    }, 10);
  });

  (function applyNoteMarks() {
    /**
     * True if wrapping the range in an inline span would pull in block-level
     * nodes (e.g. two &lt;p&gt;). Browsers then hoist elements and leave
     * broken empty blocks — misplaced borders next to blockquotes/callouts.
     */
    function rangeCloneHasBlockInside(r) {
      try {
        if (!r || r.collapsed) return true;
        var frag = r.cloneContents();
        var w = document.createTreeWalker(frag, NodeFilter.SHOW_ELEMENT, null);
        var el = null;
        var blockWrap = {
          P: 1,
          DIV: 1,
          BLOCKQUOTE: 1,
          PRE: 1,
          LI: 1,
          UL: 1,
          OL: 1,
          H1: 1,
          H2: 1,
          H3: 1,
          H4: 1,
          H5: 1,
          H6: 1,
          TABLE: 1,
          THEAD: 1,
          TBODY: 1,
          TR: 1,
          TD: 1,
          TH: 1,
          FIGURE: 1,
          HR: 1,
          SECTION: 1,
          ARTICLE: 1,
          ASIDE: 1,
          NAV: 1,
          HEADER: 1,
          FOOTER: 1,
          MAIN: 1,
          FORM: 1,
          DL: 1,
          DT: 1,
          DD: 1,
          DETAILS: 1,
          SUMMARY: 1,
        };
        while ((el = w.nextNode())) {
          if (blockWrap[el.tagName]) return true;
        }
        return false;
      } catch (e) {
        return true;
      }
    }
    window._mdViewerClips = window._mdViewerClips || [];
    window.mdViewerRemoveClip =
      window.mdViewerRemoveClip ||
      function (rangeObj) {
        try {
          if (!rangeObj) return;
          var sig = "";
          try {
            sig = JSON.stringify(rangeObj);
          } catch (e) {
            sig = "";
          }
          if (!sig) return;
          var spans =
            Array.prototype.slice.call(
              document.querySelectorAll("span.note-mark"),
            ) || [];
          spans.forEach(function (s) {
            try {
              if (!s || !s.getAttribute) return;
              if (s.getAttribute("data-range") !== sig) return;
              var p = s.parentNode;
              if (!p) return;
              while (s.firstChild) p.insertBefore(s.firstChild, s);
              p.removeChild(s);
            } catch (e) {}
          });
        } catch (e) {}
      };
    window.mdViewerApplyClip =
      window.mdViewerApplyClip ||
      function (clip) {
        try {
          if (!clip) return;
          if (clip.isUpdate && clip.range) {
            var sig = JSON.stringify(clip.range);
            document.querySelectorAll("span.note-mark").forEach(function (s) {
              try {
                if (
                  s &&
                  s.getAttribute &&
                  s.getAttribute("data-range") === sig
                ) {
                  s.title = String(clip.note || "").slice(0, 600);
                }
              } catch (e) {}
            });
            return;
          }
          try {
            if (clip.range && typeof clip.range === "object") {
              var crJson = JSON.stringify(clip.range);
              var dupClip = (window._mdViewerClips || []).some(function (x) {
                return x && x.range && JSON.stringify(x.range) === crJson;
              });
              if (dupClip) return;
              var spansDup = document.querySelectorAll("span.note-mark");
              for (var di = 0; di < spansDup.length; di++) {
                if (spansDup[di].getAttribute("data-range") === crJson) return;
              }
            }
          } catch (e) {}
          window._mdViewerClips.push(clip);
          var list = [clip];
          list.forEach(function (c) {
            try {
              var n = c.note || "";
              function rangeFromGlobals(sg, eg) {
                try {
                  sg = Number(sg);
                  eg = Number(eg);
                  if (!isFinite(sg) || !isFinite(eg) || eg < sg) return null;
                  var acc = 0;
                  var startNode = null,
                    endNode = null,
                    so = 0,
                    eo = 0;
                  var walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    {
                      acceptNode: function (node) {
                        try {
                          if (!node || !node.nodeValue)
                            return NodeFilter.FILTER_REJECT;
                          var p = node.parentElement;
                          while (p) {
                            if (shouldSkipNode(p))
                              return NodeFilter.FILTER_REJECT;
                            if (
                              p.classList &&
                              p.classList.contains("note-mark")
                            )
                              return NodeFilter.FILTER_REJECT;
                            p = p.parentElement;
                          }
                          return NodeFilter.FILTER_ACCEPT;
                        } catch (e) {
                          return NodeFilter.FILTER_REJECT;
                        }
                      },
                    },
                  );
                  var tn = null;
                  while ((tn = walker.nextNode())) {
                    var L = (tn.nodeValue || "").length;
                    if (startNode === null && acc + L >= sg) {
                      startNode = tn;
                      so = Math.max(0, sg - acc);
                    }
                    if (startNode !== null && acc + L >= eg) {
                      endNode = tn;
                      eo = Math.max(0, eg - acc);
                      break;
                    }
                    acc += L;
                    if (acc > 50_000_000) break;
                  }
                  if (!startNode || !endNode) return null;
                  var rr = document.createRange();
                  rr.setStart(startNode, so);
                  rr.setEnd(endNode, eo);
                  return rr;
                } catch (e) {
                  return null;
                }
              }
              if (c.range && c.range.startPath) {
                function getNodeByPath(p) {
                  var cur = document.body;
                  for (var i = 0; i < p.length; i++) {
                    if (
                      !cur ||
                      !cur.childNodes ||
                      cur.childNodes.length <= p[i]
                    )
                      return null;
                    cur = cur.childNodes[p[i]];
                  }
                  return cur;
                }
                var r = null;
                if (
                  typeof c.range.startGlobal === "number" &&
                  typeof c.range.endGlobal === "number"
                ) {
                  r = rangeFromGlobals(c.range.startGlobal, c.range.endGlobal);
                }
                if (!r) {
                  try {
                    r = document.createRange();
                    var sc = getNodeByPath(c.range.startPath || []);
                    var ec = getNodeByPath(c.range.endPath || []);
                    if (sc && ec) {
                      r.setStart(sc, Number(c.range.startOffset || 0));
                      r.setEnd(ec, Number(c.range.endOffset || 0));
                    } else {
                      r = null;
                    }
                  } catch (e) {
                    r = null;
                  }
                }
                if (r && !rangeCloneHasBlockInside(r)) {
                  var span = document.createElement("span");
                  applyMarkStyle(span, c, n);
                  try {
                    span.setAttribute("data-range", JSON.stringify(c.range));
                  } catch (e) {}
                  var frag = r.extractContents();
                  span.appendChild(frag);
                  r.insertNode(span);
                  return;
                }
              }
              var qA = c.quote || "";
              try {
                var fullA =
                  (document.body &&
                    (document.body.innerText || document.body.textContent)) ||
                  "";
                if (qA && countOccurrences(fullA, String(qA), 2) === 1) {
                  markOneQuote(qA, n, c);
                }
              } catch (e) {}
            } catch (e) {}
          });
        } catch (e) {}
      };
    function getRoot() {
      try {
        var m = (window.location.search || "").match(/root=([^&]+)/);
        return m && m[1] ? decodeURIComponent(m[1]) : "";
      } catch (e) {
        return "";
      }
    }
    function getPath() {
      try {
        return decodeURIComponent(
          (window.location.pathname || "").replace("/view/", "").split("?")[0],
        );
      } catch (e) {
        return "";
      }
    }
    function shouldSkipNode(el) {
      if (!el) return false;
      var tag = (el.tagName || "").toLowerCase();
      if (!tag) return false;
      return (
        tag === "pre" || tag === "code" || tag === "script" || tag === "style"
      );
    }
    function colorIndexForClip(c) {
      try {
        if (typeof c.color === "number") return Math.abs(c.color) % 6;
        var t = 0;
        if (c && typeof c.createdAt === "number") t = c.createdAt;
        else if (c && c.range) t = JSON.stringify(c.range).length * 97;
        else if (c && c.quote) t = String(c.quote).length * 131;
        return Math.abs(t) % 6;
      } catch (e) {
        return 0;
      }
    }
    function applyMarkStyle(span, clip, noteText) {
      if (!span) return;
      var idx = colorIndexForClip(clip);
      span.className = "note-mark note-c" + String(idx);
      if (noteText) span.title = String(noteText).slice(0, 600);
    }

    function markOneQuote(quote, noteText, clip) {
      quote = String(quote || "").trim();
      if (!quote) return false;
      var walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        {
          acceptNode: function (node) {
            try {
              if (!node || !node.nodeValue) return NodeFilter.FILTER_REJECT;
              var p = node.parentElement;
              while (p) {
                if (shouldSkipNode(p)) return NodeFilter.FILTER_REJECT;
                if (p.classList && p.classList.contains("note-mark"))
                  return NodeFilter.FILTER_REJECT;
                p = p.parentElement;
              }
              return NodeFilter.FILTER_ACCEPT;
            } catch (e) {
              return NodeFilter.FILTER_REJECT;
            }
          },
        },
      );
      var node = null;
      while ((node = walker.nextNode())) {
        var txt = node.nodeValue || "";
        var idx = txt.indexOf(quote);
        if (idx < 0) continue;
        try {
          var before = txt.slice(0, idx);
          var mid = txt.slice(idx, idx + quote.length);
          var after = txt.slice(idx + quote.length);
          var span = document.createElement("span");
          span.textContent = mid;
          applyMarkStyle(span, clip || null, noteText);
          if (clip && clip.range) {
            try {
              span.setAttribute("data-range", JSON.stringify(clip.range));
            } catch (e) {}
          }
          var parent = node.parentNode;
          if (!parent) return false;
          if (before)
            parent.insertBefore(document.createTextNode(before), node);
          parent.insertBefore(span, node);
          if (after) parent.insertBefore(document.createTextNode(after), node);
          parent.removeChild(node);
          return true;
        } catch (e) {
          return false;
        }
      }
      return false;
    }
    function countOccurrences(haystack, needle, limit) {
      try {
        haystack = String(haystack || "");
        needle = String(needle || "");
        if (!needle) return 0;
        var c = 0;
        var i = 0;
        while (true) {
          var p = haystack.indexOf(needle, i);
          if (p < 0) break;
          c++;
          if (limit && c >= limit) return c;
          i = p + needle.length;
        }
        return c;
      } catch (e) {
        return 0;
      }
    }
    function unwrapAllNoteMarks() {
      try {
        var spans = document.querySelectorAll("span.note-mark");
        for (var i = 0; i < spans.length; i++) {
          var s = spans[i];
          var p = s.parentNode;
          if (!p) continue;
          while (s.firstChild) p.insertBefore(s.firstChild, s);
          p.removeChild(s);
        }
      } catch (e) {}
    }
    function dedupeClips(clips) {
      var out = [];
      var seenRange = {};
      var seenQuote = {};
      (clips || []).forEach(function (c) {
        if (!c) return;
        if (c.range && typeof c.range === "object" && c.range.startPath) {
          try {
            var rk = JSON.stringify(c.range);
            if (seenRange[rk]) return;
            seenRange[rk] = true;
          } catch (e) {}
        } else {
          var qh =
            String(c.quote || "").trim() +
            "\0" +
            String(c.headingId || "") +
            "\0" +
            String(c.headingTitle || "");
          if (seenQuote[qh]) return;
          seenQuote[qh] = true;
        }
        out.push(c);
      });
      return out;
    }
    function run() {
      try {
        var root = getRoot();
        var path = getPath();
        if (!root || !path) return;
        fetch(
          "/notes?root=" +
            encodeURIComponent(root) +
            "&path=" +
            encodeURIComponent(path) +
            "&includeClips=1",
          { cache: "no-store" },
        )
          .then(function (r) {
            return r.json();
          })
          .then(function (j) {
            var raw = j && j.clips ? j.clips : [];
            if (!Array.isArray(raw) || !raw.length) return;
            unwrapAllNoteMarks();
            var clips = dedupeClips(raw).slice(0, 80);
            if (!clips.length) return;
            window._mdViewerClips = clips;
            clips.forEach(function (c) {
              if (!c) return;
              var n = c.note || "";
              if (c.range && c.range.startPath) {
                try {
                  function rangeFromGlobals(sg, eg) {
                    try {
                      sg = Number(sg);
                      eg = Number(eg);
                      if (!isFinite(sg) || !isFinite(eg) || eg < sg)
                        return null;
                      var acc = 0;
                      var startNode = null,
                        endNode = null,
                        so = 0,
                        eo = 0;
                      var walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        {
                          acceptNode: function (node) {
                            try {
                              if (!node || !node.nodeValue)
                                return NodeFilter.FILTER_REJECT;
                              var p = node.parentElement;
                              while (p) {
                                if (shouldSkipNode(p))
                                  return NodeFilter.FILTER_REJECT;
                                if (
                                  p.classList &&
                                  p.classList.contains("note-mark")
                                )
                                  return NodeFilter.FILTER_REJECT;
                                p = p.parentElement;
                              }
                              return NodeFilter.FILTER_ACCEPT;
                            } catch (e) {
                              return NodeFilter.FILTER_REJECT;
                            }
                          },
                        },
                      );
                      var tn = null;
                      while ((tn = walker.nextNode())) {
                        var L = (tn.nodeValue || "").length;
                        if (startNode === null && acc + L >= sg) {
                          startNode = tn;
                          so = Math.max(0, sg - acc);
                        }
                        if (startNode !== null && acc + L >= eg) {
                          endNode = tn;
                          eo = Math.max(0, eg - acc);
                          break;
                        }
                        acc += L;
                        if (acc > 50_000_000) break;
                      }
                      if (!startNode || !endNode) return null;
                      var rr = document.createRange();
                      rr.setStart(startNode, so);
                      rr.setEnd(endNode, eo);
                      return rr;
                    } catch (e) {
                      return null;
                    }
                  }
                  function getNodeByPath(p) {
                    var cur = document.body;
                    for (var i = 0; i < p.length; i++) {
                      if (
                        !cur ||
                        !cur.childNodes ||
                        cur.childNodes.length <= p[i]
                      )
                        return null;
                      cur = cur.childNodes[p[i]];
                    }
                    return cur;
                  }
                  var r = null;
                  if (
                    typeof c.range.startGlobal === "number" &&
                    typeof c.range.endGlobal === "number"
                  ) {
                    r = rangeFromGlobals(
                      c.range.startGlobal,
                      c.range.endGlobal,
                    );
                  }
                  if (!r) {
                    r = document.createRange();
                    var sc = getNodeByPath(c.range.startPath || []);
                    var ec = getNodeByPath(c.range.endPath || []);
                    if (sc && ec) {
                      r.setStart(sc, Number(c.range.startOffset || 0));
                      r.setEnd(ec, Number(c.range.endOffset || 0));
                    } else {
                      r = null;
                    }
                  }
                  if (r && !rangeCloneHasBlockInside(r)) {
                    var span = document.createElement("span");
                    applyMarkStyle(span, c, n);
                    try {
                      span.setAttribute("data-range", JSON.stringify(c.range));
                    } catch (e) {}
                    var frag = r.extractContents();
                    span.appendChild(frag);
                    r.insertNode(span);
                    return;
                  }
                } catch (e) {}
              }
              var q = c.quote || "";
              try {
                var full =
                  (document.body &&
                    (document.body.innerText || document.body.textContent)) ||
                  "";
                if (q && countOccurrences(full, String(q), 2) === 1) {
                  markOneQuote(q, n, c);
                }
              } catch (e) {}
            });
          })
          .catch(function () {});
      } catch (e) {}
    }
    window.addEventListener("load", function () {
      setTimeout(run, 500);
    });
  })();
})();
