var INITIAL_STATE = null;
var APP_CONFIG = null;
var PROMPT_TEMPLATES = null;
(function loadShellBootstrap() {
  try {
    var bootstrapNode = document.getElementById("mdViewerShellBootstrap");
    if (!bootstrapNode) return;
    var payload = JSON.parse(bootstrapNode.textContent || "{}");
    INITIAL_STATE = payload.initialState || null;
    APP_CONFIG = payload.appConfig || null;
    PROMPT_TEMPLATES = payload.promptTemplates || null;
    window.EXPLAIN_PROMPT_KEY = payload.explainPromptKey || "explain_ru";
  } catch (e) {
    INITIAL_STATE = null;
    APP_CONFIG = null;
    PROMPT_TEMPLATES = null;
    window.EXPLAIN_PROMPT_KEY = "explain_ru";
  }
})();
if (typeof window.EXPLAIN_PROMPT_KEY === "undefined")
  window.EXPLAIN_PROMPT_KEY = "explain_ru";

var contentFrame = document.getElementById("contentFrame");
var docLoadingFallbackTimer = null;
var docLoadingActive = false;

function notifyQtDocLoading(active) {
  try {
    if (
      window.chatBridge &&
      typeof window.chatBridge.docContentLoading === "function"
    ) {
      window.chatBridge.docContentLoading(!!active);
    }
  } catch (e) {}
}

function showContentLoading() {
  notifyQtDocLoading(true);
  docLoadingActive = true;
  try {
    if (contentFrame)
      contentFrame.classList.add("content-frame-awaiting-ready");
  } catch (e0) {}
  var el = document.getElementById("contentLoading");
  if (el) {
    el.classList.add("is-visible");
    el.setAttribute("aria-hidden", "false");
  }
}

function hideContentLoading() {
  if (docLoadingFallbackTimer) {
    clearTimeout(docLoadingFallbackTimer);
    docLoadingFallbackTimer = null;
  }
  docLoadingActive = false;
  try {
    if (contentFrame)
      contentFrame.classList.remove("content-frame-awaiting-ready");
  } catch (e0) {}
  var el = document.getElementById("contentLoading");
  if (el) {
    el.classList.remove("is-visible");
    el.setAttribute("aria-hidden", "true");
  }
  notifyQtDocLoading(false);
}
window.chatBridge = null;
if (
  typeof QWebChannel !== "undefined" &&
  typeof qt !== "undefined" &&
  qt.webChannelTransport
) {
  new QWebChannel(qt.webChannelTransport, function (channel) {
    window.chatBridge = channel.objects.chatBridge || null;
  });
}
var currentDoc = {
  path: "",
  root: "",
  title: "",
  section: "",
  progress: 0,
  readingTime: "",
};
var readerPrefs = { fontScale: 1, width: "normal", sepia: false };
var metaPrefs = { section: true, progress: true, reading: true };
var panelState = { files: true, toc: true, content: true, interpreter: false };
var focusMode = false;
var widthMap = { narrow: "980px", normal: "1240px", wide: "1480px" };
var widthOrder = ["narrow", "normal", "wide"];
var settingsSyncTimeout = null;
var appTheme = "light";

if (typeof INITIAL_STATE === "object" && INITIAL_STATE) {
  if (INITIAL_STATE.readerPrefs) {
    readerPrefs = Object.assign(readerPrefs, INITIAL_STATE.readerPrefs);
  }
  if (INITIAL_STATE.metaPrefs) {
    metaPrefs = Object.assign(metaPrefs, INITIAL_STATE.metaPrefs);
  }
  if (INITIAL_STATE.panelState) {
    panelState = Object.assign(panelState, INITIAL_STATE.panelState);
  }
  if (INITIAL_STATE.currentDoc) {
    currentDoc = Object.assign(currentDoc, INITIAL_STATE.currentDoc);
  }
}

function safeParse(key, fallback) {
  try {
    var raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (e) {
    return fallback;
  }
}
function saveJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}
function sanitizeText(text) {
  return (text || "").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function prettyName(path) {
  if (!path) return "Document";
  var tail = path.split("/").pop() || path;
  return tail.replace(/_/g, " ").replace(/\\.md$/i, "");
}
function getProgressStore() {
  return safeParse("study_md_desk_progress", {});
}
function getRecentStore() {
  return safeParse("study_md_desk_recent", []);
}
function getFavoritesStore() {
  return safeParse("study_md_desk_favorites", {});
}
function getCompletedStore() {
  return safeParse("study_md_desk_completed", {});
}
function collectSettingsSnapshot() {
  var progress = getProgressStore();
  var recent = getRecentStore();
  var favorites = getFavoritesStore();
  var completed = getCompletedStore();
  var libraryFilter = document.getElementById("libraryFilter")
    ? document.getElementById("libraryFilter").value
    : "";
  var folderState = {};
  document
    .querySelectorAll("#fileTreeWrap .folder[data-toggle]")
    .forEach(function (el) {
      try {
        var li = el.closest(".folder-item");
        var path =
          li && li.querySelector("a[href]")
            ? li.querySelector("a[href]").getAttribute("href")
            : null;
        var key = path || el.textContent || "";
        if (!key) return;
        folderState[key] = el.classList.contains("expanded");
      } catch (e) {}
    });
  var activeTocId = null;
  var activeToc = document.querySelector("#tocContent a.active");
  if (activeToc) {
    var href = activeToc.getAttribute("href") || "";
    var m = href.match(/#(.+)$/);
    if (m) activeTocId = m[1];
  }
  return {
    currentDoc: currentDoc,
    readerPrefs: readerPrefs,
    metaPrefs: metaPrefs,
    panelState: panelState,
    progress: progress,
    recent: recent,
    favorites: favorites,
    completed: completed,
    libraryFilter: libraryFilter,
    folderState: folderState,
    activeTocId: activeTocId,
  };
}
function scheduleSettingsSync() {
  if (settingsSyncTimeout) clearTimeout(settingsSyncTimeout);
  settingsSyncTimeout = setTimeout(function () {
    try {
      var payload = collectSettingsSnapshot();
      fetch("/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (e) {}
  }, 800);
}
function makeDocHref(item) {
  if (!item || !item.path) return "#";
  return (
    "/view/" +
    encodeURI(item.path) +
    (item.root ? "?root=" + encodeURIComponent(item.root) : "")
  );
}
function openDoc(path, root) {
  if (!path) return;
  showContentLoading();
  try {
    if (window.chatBridge && window.chatBridge.ttsStop)
      window.chatBridge.ttsStop();
  } catch (e) {}
  var _cacheBust = "_=" + String(Date.now());
  var q = [];
  if (root) q.push("root=" + encodeURIComponent(root));
  if (typeof appTheme !== "undefined" && appTheme === "dark") q.push("rt=dark");
  q.push(_cacheBust);
  contentFrame.src = "/view/" + encodeURI(path) + "?" + q.join("&");
}
function updateMetaStrip() {
  document.getElementById("metaDocTitle").textContent =
    currentDoc.title || "No document open";
  document.getElementById("metaSection").textContent =
    currentDoc.section || "—";
  document.getElementById("metaProgress").textContent =
    Math.round(currentDoc.progress || 0) + "%";
  document.getElementById("metaReadingTime").textContent =
    currentDoc.readingTime || "—";
  var sectionPill = document.getElementById("metaSectionPill");
  var progressPill = document.getElementById("metaProgressPill");
  var readingPill = document.getElementById("metaReadingPill");
  if (sectionPill) sectionPill.style.display = metaPrefs.section ? "" : "none";
  if (progressPill)
    progressPill.style.display = metaPrefs.progress ? "" : "none";
  if (readingPill) readingPill.style.display = metaPrefs.reading ? "" : "none";
}
function updateReaderButtonStates() {
  document
    .getElementById("favoriteBtn")
    .classList.toggle(
      "active",
      !!(currentDoc.path && getFavoritesStore()[currentDoc.path]),
    );
  document
    .getElementById("completeBtn")
    .classList.toggle(
      "active",
      !!(currentDoc.path && getCompletedStore()[currentDoc.path]),
    );
  document
    .getElementById("toggleFocusBtn")
    .classList.toggle("active", focusMode);
  document.getElementById("readerWidthBtn").textContent =
    "Width: " +
    (readerPrefs.width === "narrow"
      ? "narrow"
      : readerPrefs.width === "wide"
        ? "wide"
        : "normal");
}
function renderList(containerId, items, emptyText, showProgress) {
  var root = document.getElementById(containerId);
  if (!root) return;
  if (!items || !items.length) {
    root.innerHTML = '<div class=\"sidebar-empty\">' + emptyText + "</div>";
    return;
  }
  root.innerHTML = items
    .map(function (item) {
      var title = sanitizeText(item.title || prettyName(item.path));
      var meta = sanitizeText(item.path || "");
      var progress = Math.round(item.progress || 0);
      var progressHtml = showProgress
        ? '<div class=\"progress-mini\"><span style=\"width:' +
          progress +
          '%\"></span></div>'
        : "";
      return (
        '<a class=\"sidebar-link\" href=\"' +
        makeDocHref(item) +
        '\" data-open-path=\"' +
        encodeURIComponent(item.path || "") +
        '\" data-open-root=\"' +
        encodeURIComponent(item.root || "") +
        '\">' +
        '<span class=\"sidebar-link-title\">' +
        title +
        "</span>" +
        '<span class=\"sidebar-link-meta\">' +
        meta +
        (showProgress ? " · " + progress + "%" : "") +
        "</span>" +
        progressHtml +
        "</a>"
      );
    })
    .join("");
  root.querySelectorAll("[data-open-path]").forEach(function (a) {
    a.onclick = function (e) {
      e.preventDefault();
      openDoc(
        decodeURIComponent(a.getAttribute("data-open-path") || ""),
        decodeURIComponent(a.getAttribute("data-open-root") || ""),
      );
    };
  });
}
function renderFavorites() {
  var store = getFavoritesStore();
  var items = Object.keys(store)
    .map(function (path) {
      return store[path];
    })
    .sort(function (a, b) {
      return (b.added || 0) - (a.added || 0);
    })
    .slice(0, 8);
  renderList("favoritesList", items, "Empty for now.", false);
}
window.mdViewerOpenRecent = function () {
  var list = getRecentStore();
  if (!list || !list.length) return;
  var first = list[0];
  openDoc(first.path, first.root);
};
window.mdViewerGetRecentList = function (maxCount) {
  var list = getRecentStore() || [];
  maxCount = maxCount || 10;
  return JSON.stringify(list.slice(0, maxCount));
};
window.mdViewerOpenByPath = function (path, root) {
  if (!path) return;
  openDoc(path, root || "");
};
function expandTreeForCurrent() {
  if (!currentDoc.path) return;
  try {
    var href = "/view/" + encodeURI(currentDoc.path);
    var link = document.querySelector(
      '#fileTreeWrap a[href^=\"' + href + '\"]',
    );
    if (!link) return;
    var li = link.closest(".folder-item");
    while (li) {
      var folder = li.querySelector(":scope > .folder[data-toggle]");
      if (folder) {
        folder.classList.add("expanded");
        folder.classList.remove("collapsed");
      }
      li = li.parentElement ? li.parentElement.closest(".folder-item") : null;
    }
  } catch (e) {}
}
function syncTreeState() {
  var favorites = getFavoritesStore();
  var completed = getCompletedStore();
  document.querySelectorAll("#fileTreeWrap a").forEach(function (a) {
    var href = a.getAttribute("href") || "";
    var path =
      href.indexOf("/view/") === 0
        ? decodeURIComponent(href.replace("/view/", "").split("?")[0])
        : "";
    a.classList.toggle("current", !!path && path === currentDoc.path);
    a.classList.toggle("favorite", !!favorites[path]);
    a.classList.toggle("completed", !!completed[path]);
  });
  expandTreeForCurrent();
}
function persistCurrentDoc() {
  if (!currentDoc.path) return;
  var progress = getProgressStore();
  progress[currentDoc.path] = {
    path: currentDoc.path,
    root: currentDoc.root,
    title: currentDoc.title || prettyName(currentDoc.path),
    progress: Math.round(currentDoc.progress || 0),
    section: currentDoc.section || "",
    readingTime: currentDoc.readingTime || "",
    lastOpened: Date.now(),
  };
  saveJson("study_md_desk_progress", progress);

  var recent = getRecentStore().filter(function (item) {
    return item.path !== currentDoc.path;
  });
  recent.unshift(progress[currentDoc.path]);
  saveJson("study_md_desk_recent", recent.slice(0, 18));
  scheduleSettingsSync();
}
function refreshStudyUI() {
  updateMetaStrip();
  updateReaderButtonStates();
  renderFavorites();
  syncTreeState();
}
function applyReaderPrefs() {
  document
    .getElementById("readerFontDown")
    .classList.toggle("active", readerPrefs.fontScale < 1);
  document
    .getElementById("readerFontUp")
    .classList.toggle("active", readerPrefs.fontScale > 1);
  updateReaderButtonStates();
  try {
    var enableSepia = !!readerPrefs.sepia && appTheme !== "dark";
    document.body.classList.toggle("theme-sepia", enableSepia);
    document.documentElement.classList.toggle("theme-sepia", enableSepia);
    try {
      if (window.chatBridge && window.chatBridge.setChatTheme) {
        window.chatBridge.setChatTheme(enableSepia ? "sepia" : appTheme);
      }
    } catch (e) {}
  } catch (e) {}
  try {
    if (contentFrame.contentWindow) {
      contentFrame.contentWindow.postMessage(
        {
          type: "viewer-settings",
          fontScale: readerPrefs.fontScale,
          maxWidth: widthMap[readerPrefs.width] || widthMap.normal,
          sepia: !!readerPrefs.sepia,
          theme: appTheme,
        },
        "*",
      );
    }
  } catch (e) {}
}
function applyPanels() {
  document.getElementById("sidebar").style.display = panelState.files
    ? "block"
    : "none";
  document.getElementById("tocPanel").style.display = panelState.toc
    ? "block"
    : "none";
  document.getElementById("contentMain").style.display = panelState.content
    ? "flex"
    : "none";
  document.getElementById("interpreter").style.display = panelState.interpreter
    ? "flex"
    : "none";
}
function savePanels() {
  saveJson("study_md_desk_panels", panelState);
}
function setPanelState(name, checked) {
  if (!(name in panelState)) return;
  panelState[name] = !!checked;
  var inputId = {
    files: "toggleFiles",
    toc: "toggleToc",
    content: "toggleContent",
    interpreter: "toggleInterpreter",
  }[name];
  if (inputId && document.getElementById(inputId))
    document.getElementById(inputId).checked = !!checked;
  applyPanels();
  savePanels();
}
function toggleFocusMode() {
  var before = {
    files: panelState.files,
    toc: panelState.toc,
    content: panelState.content,
    interpreter: panelState.interpreter,
  };
  focusMode = !focusMode;
  if (focusMode) {
    saveJson("study_md_desk_panels_before_focus", before);
    panelState.files = false;
    panelState.toc = false;
  } else {
    var stored = safeParse("study_md_desk_panels_before_focus", null);
    if (stored) panelState = stored;
  }
  applyPanels();
  savePanels();
  updateReaderButtonStates();
  scheduleSettingsSync();
}
function toggleFavorite() {
  if (!currentDoc.path) return;
  var store = getFavoritesStore();
  if (store[currentDoc.path]) delete store[currentDoc.path];
  else
    store[currentDoc.path] = {
      path: currentDoc.path,
      root: currentDoc.root,
      title: currentDoc.title,
      added: Date.now(),
    };
  saveJson("study_md_desk_favorites", store);
  refreshStudyUI();
  scheduleSettingsSync();
}
function toggleCompleted() {
  if (!currentDoc.path) return;
  var store = getCompletedStore();
  if (store[currentDoc.path]) delete store[currentDoc.path];
  else store[currentDoc.path] = true;
  saveJson("study_md_desk_completed", store);
  refreshStudyUI();
  scheduleSettingsSync();
}
function cycleWidth() {
  var idx = widthOrder.indexOf(readerPrefs.width);
  if (idx < 0) idx = 1;
  readerPrefs.width = widthOrder[(idx + 1) % widthOrder.length];
  saveJson("study_md_desk_reader_prefs", readerPrefs);
  applyReaderPrefs();
}
function filterTree(term) {
  var normalized = (term || "").trim().toLowerCase();
  document.querySelectorAll("#fileTreeWrap a").forEach(function (a) {
    var text = (a.textContent || "").toLowerCase();
    a.style.display =
      !normalized || text.indexOf(normalized) >= 0 ? "" : "none";
  });
  document
    .querySelectorAll("#fileTreeWrap .folder-item")
    .forEach(function (item) {
      var hasVisibleLink = !!item.querySelector(
        'a:not([style*=\"display: none\"])',
      );
      var folder = item.querySelector(":scope > .folder");
      var folderMatch =
        folder &&
        (folder.textContent || "").toLowerCase().indexOf(normalized) >= 0;
      item.style.display =
        !normalized || hasVisibleLink || folderMatch ? "" : "none";
      if (normalized && (hasVisibleLink || folderMatch) && folder) {
        folder.classList.add("expanded");
        folder.classList.remove("collapsed");
      }
    });
}
function highlightActiveToc(anchor) {
  document.querySelectorAll("#tocContent a").forEach(function (a) {
    var href = a.getAttribute("href") || "";
    var isActive = anchor && href.indexOf("#" + anchor) >= 0;
    a.classList.toggle("active", !!isActive);
  });
}
(function initViewerShell() {
  if (
    !(
      typeof INITIAL_STATE === "object" &&
      INITIAL_STATE &&
      INITIAL_STATE.panelState
    )
  ) {
    panelState = safeParse("study_md_desk_panels", panelState);
  }
  readerPrefs = safeParse("study_md_desk_reader_prefs", readerPrefs);
  if (typeof readerPrefs.sepia === "undefined") readerPrefs.sepia = false;
  ["toggleFiles", "toggleToc", "toggleContent", "toggleInterpreter"].forEach(
    function (id) {
      var key = id.replace("toggle", "");
      key = key.charAt(0).toLowerCase() + key.slice(1);
      if (document.getElementById(id))
        document.getElementById(id).checked = panelState[key] !== false;
    },
  );
  applyPanels();
  applyReaderPrefs();
  refreshStudyUI();
  var libFilter = document.getElementById("libraryFilter");
  if (libFilter) {
    if (
      typeof INITIAL_STATE === "object" &&
      INITIAL_STATE &&
      INITIAL_STATE.libraryFilter
    ) {
      libFilter.value = INITIAL_STATE.libraryFilter;
      filterTree(libFilter.value);
    }
    libFilter.addEventListener("input", function () {
      filterTree(this.value);
      scheduleSettingsSync();
    });
  }
  if (
    typeof INITIAL_STATE === "object" &&
    INITIAL_STATE &&
    INITIAL_STATE.folderState
  ) {
    try {
      var folderState = INITIAL_STATE.folderState || {};
      document
        .querySelectorAll("#fileTreeWrap .folder[data-toggle]")
        .forEach(function (el) {
          var li = el.closest(".folder-item");
          var path =
            li && li.querySelector("a[href]")
              ? li.querySelector("a[href]").getAttribute("href")
              : null;
          var key = path || el.textContent || "";
          if (!key) return;
          var expanded = !!folderState[key];
          el.classList.toggle("expanded", expanded);
          el.classList.toggle("collapsed", !expanded);
        });
    } catch (e) {}
  }
  if (
    typeof INITIAL_STATE === "object" &&
    INITIAL_STATE &&
    INITIAL_STATE.currentDoc &&
    INITIAL_STATE.currentDoc.path
  ) {
    try {
      openDoc(
        INITIAL_STATE.currentDoc.path,
        INITIAL_STATE.currentDoc.root || "",
      );
    } catch (e) {}
  }
  var settingsBar = document.getElementById("readerSettingsBar");
  var settingsToggle = document.getElementById("readerSettingsToggle");
  if (settingsToggle && settingsBar) {
    settingsToggle.onclick = function () {
      var visible = settingsBar.style.display === "flex";
      settingsBar.style.display = visible ? "none" : "flex";
    };
  }
  (function initAppTheme() {
    function applyThemeFromItems(items) {
      try {
        var m = {};
        (items || []).forEach(function (pair) {
          if (!pair || pair.length < 2) return;
          m[String(pair[0] || "").toLowerCase()] = String(pair[1] || "");
        });
        var t = (m.theme || m.apptheme || "").toLowerCase().trim();
        appTheme = t === "dark" || t === "1" || t === "true" ? "dark" : "light";
        document.body.classList.toggle("theme-dark", appTheme === "dark");
        if (appTheme === "dark") document.body.classList.remove("theme-sepia");
        try {
          if (window.chatBridge && window.chatBridge.setChatTheme) {
            window.chatBridge.setChatTheme(appTheme);
          }
        } catch (e) {}
      } catch (e) {}
    }
    fetch("/app-config", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (cfg) {
        var items = cfg && cfg.items ? cfg.items : [];
        applyThemeFromItems(items);
        applyReaderPrefs();
      })
      .catch(function () {
        applyReaderPrefs();
      });
  })();
  var appModal = document.getElementById("appSettingsModal");
  function openAppSettings() {
    if (!appModal) return;
    var form = document.getElementById("appSettingsForm");
    if (!form) {
      appModal.style.display = "flex";
      return;
    }
    form.innerHTML =
      '<div class="form-hint" style="grid-column:1/-1;">Loading...</div>';
    fetch("/app-config", { cache: "no-store" })
      .then(function (r) {
        return r.json();
      })
      .then(function (cfg) {
        cfg = cfg && typeof cfg === "object" ? cfg : {};
        var items = (function (raw) {
          if (!Array.isArray(raw)) return [];
          var out = [];
          for (var i = 0; i < raw.length; i++) {
            var p = raw[i];
            if (!Array.isArray(p) || p.length < 2) continue;
            var k = String(p[0] || "").trim();
            if (!k) continue;
            out.push([k, String(p[1] == null ? "" : p[1])]);
          }
          return out;
        })(cfg.items);
        var hasTheme = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "theme";
        });
        var hasChatOptions = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "chatoptions";
        });
        var hasChatUrl = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "chaturl";
        });
        var hasSandboxOptions = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "sandboxoptions";
        });
        var hasSandboxUrl = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "sandboxurl";
        });
        var hasTranslateOptions = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "translateoptions";
        });
        var hasTranslateUrl = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "translateurl";
        });
        var hasTtsEngine = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "ttsengine";
        });
        var hasPiperModel = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "pipermodelpath";
        });
        var hasPiperConfig = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "piperconfigpath";
        });
        var hasPiperVoiceName = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "pipervoicename";
        });
        var hasTtsVoice = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "ttsvoice";
        });
        var hasTtsRate = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "ttsrate";
        });
        var hasExplainPromptKey = items.some(function (p) {
          return p && (p[0] || "").toLowerCase() === "explainpromptkey";
        });
        if (!hasTheme) {
          items.push(["theme", "white"]);
        }
        if (!hasChatOptions) {
          items.push([
            "chatOptions",
            "https://chat.qwen.ai/;https://www.perplexity.ai/;https://chat.deepseek.com/",
          ]);
        }
        if (!hasChatUrl) {
          items.push(["chatUrl", "https://chat.qwen.ai/"]);
        }
        if (!hasSandboxOptions) {
          items.push([
            "sandboxOptions",
            "https://www.programiz.com/r/online-compiler/;https://www.programiz.com/sql/online-compiler/",
          ]);
        }
        if (!hasSandboxUrl) {
          items.push([
            "sandboxUrl",
            "https://www.programiz.com/r/online-compiler/",
          ]);
        }
        if (!hasTranslateOptions) {
          items.push([
            "translateOptions",
            "https://translate.yandex.ru/;https://translate.google.com",
          ]);
        }
        if (!hasTranslateUrl) {
          items.push(["translateUrl", "https://translate.yandex.ru/"]);
        }
        if (!hasTtsEngine) items.push(["ttsEngine", "piper"]);
        if (!hasPiperModel)
          items.push([
            "piperModelPath",
            "tts_models/ru_RU-ruslan-medium/model.onnx",
          ]);
        if (!hasPiperConfig)
          items.push([
            "piperConfigPath",
            "tts_models/ru_RU-ruslan-medium/model.onnx.json",
          ]);
        if (!hasPiperVoiceName)
          items.push(["piperVoiceName", "ru_RU-ruslan-medium"]);
        if (!hasTtsVoice) items.push(["ttsVoice", "ru"]);
        if (!hasTtsRate) items.push(["ttsRate", "175"]);
        if (!hasExplainPromptKey)
          items.push(["explainPromptKey", "explain_ru"]);
        APP_CONFIG = cfg;
        if (!items.length) {
          form.innerHTML =
            '<div class="form-hint" style="grid-column:1/-1;">Section [app] is empty. Add parameters to `study_md_desk.ini` or fill them here.</div>';
        } else {
          form.innerHTML = "";
        }
        var chatOptionsRaw = "";
        var sandboxOptionsRaw = "";
        var translateOptionsRaw = "";
        items.forEach(function (p) {
          if (p && (p[0] || "").toLowerCase() === "chatoptions") {
            chatOptionsRaw = String(p[1] || "");
          }
          if (p && (p[0] || "").toLowerCase() === "sandboxoptions") {
            sandboxOptionsRaw = String(p[1] || "");
          }
          if (p && (p[0] || "").toLowerCase() === "translateoptions") {
            translateOptionsRaw = String(p[1] || "");
          }
        });
        var chatOptions = chatOptionsRaw
          .split(/[;,\r\n]+/)
          .map(function (s) {
            return s.trim();
          })
          .filter(function (s) {
            return s;
          });
        if (!chatOptions.length) {
          chatOptions = [
            "https://chat.qwen.ai/",
            "https://www.perplexity.ai",
            "https://chat.deepseek.com/",
          ];
        }
        var sandboxOptions = sandboxOptionsRaw
          .split(/[;,\r\n]+/)
          .map(function (s) {
            return s.trim();
          })
          .filter(function (s) {
            return s;
          });
        if (!sandboxOptions.length) {
          sandboxOptions = [
            "https://www.programiz.com/r/online-compiler/",
            "https://www.programiz.com/sql/online-compiler/",
          ];
        }

        var translateOptions = translateOptionsRaw
          .split(/[;,\r\n]+/)
          .map(function (s) {
            return s.trim();
          })
          .filter(function (s) {
            return s;
          });
        if (!translateOptions.length) {
          translateOptions = [
            "https://translate.yandex.ru/",
            "https://translate.google.com",
          ];
        }
        var themeChoices = ["white", "dark"];
        var ttsEngineChoices = ["piper", "espeak"];
        var piperVoices = null;

        items.forEach(function (pair) {
          var k = pair && pair.length ? pair[0] : "";
          var v = pair && pair.length > 1 ? pair[1] : "";
          if (!k) return;
          if (v === null || typeof v === "undefined") v = "";
          v = String(v);
          var lower = (k || "").toLowerCase();
          if (lower === "ttsspeed" || lower === "pipersentencesilence") return;
          if (lower === "pipermodelpath" || lower === "piperconfigpath") return;
          var isSecret =
            lower.includes("key") ||
            lower.includes("token") ||
            lower.includes("secret");
          var id = "cfg_" + lower.replace(/[^a-z0-9_]/g, "_");
          var row = document.createElement("div");
          row.className = "form-row";
          row.innerHTML =
            '<label for=\"' + id + '\">' + sanitizeText(k) + "</label>";
          if (lower === "theme") {
            var tsel = document.createElement("select");
            tsel.id = id;
            tsel.setAttribute("data-app-key", k);
            themeChoices.forEach(function (name) {
              var opt = document.createElement("option");
              opt.value = name;
              opt.textContent = name;
              if ((v || "").toLowerCase() === name.toLowerCase())
                opt.selected = true;
              tsel.appendChild(opt);
            });
            row.appendChild(tsel);
          } else if (lower === "chaturl") {
            var select = document.createElement("select");
            select.id = id;
            select.setAttribute("data-app-key", k);
            chatOptions.forEach(function (url) {
              var opt = document.createElement("option");
              opt.value = url;
              opt.textContent = url;
              if (url === v) opt.selected = true;
              select.appendChild(opt);
            });
            row.appendChild(select);
          } else if (lower === "sandboxurl") {
            var sselect = document.createElement("select");
            sselect.id = id;
            sselect.setAttribute("data-app-key", k);
            sandboxOptions.forEach(function (url) {
              var opt2 = document.createElement("option");
              opt2.value = url;
              opt2.textContent = url;
              if (url === v) opt2.selected = true;
              sselect.appendChild(opt2);
            });
            row.appendChild(sselect);
          } else if (lower === "translateurl") {
            var tselect = document.createElement("select");
            tselect.id = id;
            tselect.setAttribute("data-app-key", k);
            translateOptions.forEach(function (url) {
              var opt3 = document.createElement("option");
              opt3.value = url;
              opt3.textContent = url;
              if (url === v) opt3.selected = true;
              tselect.appendChild(opt3);
            });
            row.appendChild(tselect);
          } else if (lower === "ttsengine") {
            var engSel = document.createElement("select");
            engSel.id = id;
            engSel.setAttribute("data-app-key", k);
            ttsEngineChoices.forEach(function (name) {
              var opt3 = document.createElement("option");
              opt3.value = name;
              opt3.textContent = name;
              if ((v || "").toLowerCase() === name.toLowerCase())
                opt3.selected = true;
              engSel.appendChild(opt3);
            });
            row.appendChild(engSel);
          } else if (lower === "explainpromptkey") {
            var explSel = document.createElement("select");
            explSel.id = id;
            explSel.setAttribute("data-app-key", k);
            [
              {
                value: "explain_ru",
                label: "Explain prompt — Russian (explain_ru)",
              },
              {
                value: "explain_en",
                label: "Explain prompt — English (explain_en)",
              },
            ].forEach(function (desc) {
              var ox = document.createElement("option");
              ox.value = desc.value;
              ox.textContent = desc.label;
              if (
                String(v || "")
                  .trim()
                  .toLowerCase() === desc.value.toLowerCase()
              )
                ox.selected = true;
              explSel.appendChild(ox);
            });
            row.appendChild(explSel);
          } else if (lower === "pipervoicename") {
            var voiceSel = document.createElement("select");
            voiceSel.id = id;
            voiceSel.setAttribute("data-app-key", k);
            var ph = document.createElement("option");
            ph.value = v || "";
            ph.textContent = v || "loading…";
            ph.selected = true;
            voiceSel.appendChild(ph);
            row.appendChild(voiceSel);
            fetch("/piper-voices", { cache: "no-store" })
              .then(function (r) {
                return r.json();
              })
              .then(function (data) {
                var list = data && data.voices ? data.voices : [];
                if (!Array.isArray(list)) list = [];
                voiceSel.innerHTML = "";
                if (!list.length) {
                  var o0 = document.createElement("option");
                  o0.value = v || "";
                  o0.textContent = v || "no voices found";
                  o0.selected = true;
                  voiceSel.appendChild(o0);
                  return;
                }
                list.forEach(function (it) {
                  var idv = String((it && (it.id || it.name)) || "");
                  if (!idv) return;
                  var opt = document.createElement("option");
                  opt.value = idv;
                  opt.textContent = idv;
                  if ((v || "").toLowerCase() === idv.toLowerCase())
                    opt.selected = true;
                  voiceSel.appendChild(opt);
                });
              })
              .catch(function () {});
            voiceSel.addEventListener("change", function () {
              try {
                var vid = String(voiceSel.value || "");
                if (window.chatBridge && window.chatBridge.ttsSetPiperVoice) {
                  window.chatBridge.ttsSetPiperVoice(vid);
                }
              } catch (e) {}
            });
          } else {
            var input = document.createElement("input");
            input.id = id;
            input.type = isSecret ? "password" : "text";
            input.value = v;
            input.setAttribute("data-app-key", k);
            row.appendChild(input);
          }
          form.appendChild(row);
        });
        var hint = document.createElement("div");
        hint.className = "form-hint";
        hint.textContent =
          "Any new keys in [app] will appear here automatically after reload or reopening the form.";
        form.appendChild(hint);
      })
      .catch(function () {
        form.innerHTML =
          '<div class="form-hint" style="grid-column:1/-1;">Failed to load settings.</div>';
      });
    appModal.style.display = "flex";
  }
  function closeAppSettings() {
    if (appModal) appModal.style.display = "none";
  }
  window.mdViewerOpenAppSettings = openAppSettings;
  var appClose = document.getElementById("appSettingsClose");
  var appCancel = document.getElementById("appSettingsCancel");
  if (appClose) appClose.onclick = closeAppSettings;
  if (appCancel) appCancel.onclick = closeAppSettings;
  if (appModal) {
    appModal.addEventListener("click", function (e) {
      if (e.target === appModal) closeAppSettings();
    });
  }
  var appSave = document.getElementById("appSettingsSave");
  if (appSave) {
    appSave.onclick = function () {
      var payload = {};
      document
        .querySelectorAll("#appSettingsForm [data-app-key]")
        .forEach(function (el) {
          var k = el.getAttribute("data-app-key");
          if (!k) return;
          payload[k] = el.value || "";
        });
      fetch("/app-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function () {
          closeAppSettings();
          location.reload();
        })
        .catch(function () {
          closeAppSettings();
        });
    };
  }

  var notesModal = document.getElementById("notesModal");
  var notesModalClose = document.getElementById("notesModalClose");
  var notesModalCancel = document.getElementById("notesModalCancel");
  var notesModalSave = document.getElementById("notesModalSave");
  var notesModalMeta = document.getElementById("notesModalMeta");
  var notesModalQuote = document.getElementById("notesModalQuote");
  var notesModalText = document.getElementById("notesModalText");
  var _notesCtx = {
    root: "",
    path: "",
    headingId: "",
    headingTitle: "",
    selectedText: "",
    mode: "create",
    range: null,
  };

  function closeNotesModal() {
    if (notesModal) notesModal.style.display = "none";
  }

  async function saveNotesModalText() {
    try {
      if (!_notesCtx.root || !_notesCtx.path) return;
      var payload = {
        root: _notesCtx.root,
        path: _notesCtx.path,
        clip:
          _notesCtx.mode === "edit"
            ? null
            : {
                quote: String(_notesCtx.selectedText || ""),
                note: String((notesModalText && notesModalText.value) || ""),
                headingId: String(_notesCtx.headingId || ""),
                headingTitle: String(_notesCtx.headingTitle || ""),
                range: _notesCtx.range,
              },
        clipUpdate:
          _notesCtx.mode === "edit"
            ? {
                range: _notesCtx.range,
                note: String((notesModalText && notesModalText.value) || ""),
              }
            : null,
      };
      await fetch("/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      closeNotesModal();
      try {
        var w =
          typeof contentFrame !== "undefined" &&
          contentFrame &&
          contentFrame.contentWindow
            ? contentFrame.contentWindow
            : null;
        if (w && w.mdViewerApplyClip) {
          if (_notesCtx.mode === "edit") {
            w.mdViewerApplyClip({
              range: _notesCtx.range,
              note: String((notesModalText && notesModalText.value) || ""),
              isUpdate: true,
            });
          } else {
            w.mdViewerApplyClip({
              quote: String(_notesCtx.selectedText || ""),
              note: String((notesModalText && notesModalText.value) || ""),
              headingId: String(_notesCtx.headingId || ""),
              headingTitle: String(_notesCtx.headingTitle || ""),
              range: _notesCtx.range,
              createdAt: Date.now(),
            });
          }
        }
      } catch (e) {}
    } catch (e) {}
  }

  function openNotesModal(ctx) {
    try {
      _notesCtx.root = String((ctx && ctx.root) || currentDoc.root || "");
      _notesCtx.path = String((ctx && ctx.path) || currentDoc.path || "");
      _notesCtx.headingId = String((ctx && ctx.headingId) || "");
      _notesCtx.headingTitle = String((ctx && ctx.headingTitle) || "");
      _notesCtx.selectedText = String((ctx && ctx.text) || "");
      _notesCtx.mode = String((ctx && ctx.op) || "create");
      _notesCtx.range = ctx && ctx.range ? ctx.range : null;
      _notesCtx.existingNote = String((ctx && ctx.note) || "");
      if (!_notesCtx.root || !_notesCtx.path) return;
      if (!_notesCtx.selectedText || !_notesCtx.selectedText.trim()) return;

      if (notesModalMeta) {
        var meta = "File: " + _notesCtx.path;
        if (_notesCtx.headingId)
          meta +=
            "\\nHeading: " + (_notesCtx.headingTitle || _notesCtx.headingId);
        notesModalMeta.textContent = meta;
      }

      if (notesModalQuote) {
        notesModalQuote.textContent = String(
          _notesCtx.selectedText || "",
        ).trim();
      }
      if (notesModalText)
        notesModalText.value =
          _notesCtx.mode === "edit" ? _notesCtx.existingNote : "";
      if (notesModal) notesModal.style.display = "flex";
    } catch (e) {}
  }

  window.mdViewerOpenNotesModal = openNotesModal;

  if (notesModalClose) notesModalClose.onclick = closeNotesModal;
  if (notesModalCancel) notesModalCancel.onclick = closeNotesModal;
  if (notesModalSave)
    notesModalSave.onclick = function () {
      saveNotesModalText();
    };
  if (notesModal) {
    notesModal.addEventListener("click", function (e) {
      if (e.target === notesModal) closeNotesModal();
    });
  }
  document.getElementById("toggleFocusBtn").onclick = toggleFocusMode;
  document.getElementById("favoriteBtn").onclick = toggleFavorite;
  document.getElementById("completeBtn").onclick = toggleCompleted;
  document.getElementById("readerWidthBtn").onclick = cycleWidth;
  var ttsPanelToggle = document.getElementById("ttsPanelToggle");
  var ttsPanel = document.getElementById("ttsPanel");
  var ttsSpeakCmd = document.getElementById("ttsSpeakCmd");
  var ttsPauseCmd = document.getElementById("ttsPauseCmd");
  var ttsStopCmd = document.getElementById("ttsStopCmd");
  var ttsNow = document.getElementById("ttsNow");
  var ttsNowText = document.getElementById("ttsNowText");
  var ttsNowTimeout = null;
  var ttsSpeedRange = document.getElementById("ttsSpeedRange");
  var ttsSpeedLabel = document.getElementById("ttsSpeedLabel");
  var ttsSilRange = document.getElementById("ttsSentenceSilenceRange");
  var ttsSilLabel = document.getElementById("ttsSentenceSilenceLabel");
  var ttsFollow = document.getElementById("ttsFollow");

  function setTtsVisible(v) {
    if (!ttsPanel) return;
    ttsPanel.style.display = v ? "flex" : "none";
  }
  function updateTtsLabels() {
    try {
      if (ttsSpeedRange && ttsSpeedLabel)
        ttsSpeedLabel.textContent =
          "x" + Number(ttsSpeedRange.value || 1).toFixed(2);
      if (ttsSilRange && ttsSilLabel)
        ttsSilLabel.textContent =
          Number(ttsSilRange.value || 0).toFixed(2) + "s";
    } catch (e) {}
  }

  window.mdViewerTtsNow = function (text) {
    try {
      if (!ttsNow || !ttsNowText) return;
      var t = String(text || "").trim();
      if (!t) {
        ttsNow.style.display = "none";
        return;
      }
      ttsNowText.textContent = t;
      ttsNow.style.display = "block";
      if (ttsNowTimeout) {
        clearTimeout(ttsNowTimeout);
        ttsNowTimeout = null;
      }
      ttsNowTimeout = setTimeout(function () {
        try {
          if (!ttsNow || !ttsNowText) return;
          ttsNowText.textContent = "—";
          ttsNow.style.display = "none";
        } catch (e) {}
      }, 15000);
    } catch (e) {}
  };

  function isFollowOn() {
    try {
      return !!(ttsFollow && ttsFollow.checked);
    } catch (e) {
      return true;
    }
  }

  window.mdViewerTtsSync = function (text) {
    try {
      var t = String(text || "").trim();
      if (!t) return;
      if (window.mdViewerTtsNow) window.mdViewerTtsNow(t);
      var w = contentFrame && contentFrame.contentWindow;
      if (w) {
        try {
          w.postMessage(
            { type: "tts-highlight", text: t, follow: isFollowOn() },
            "*",
          );
        } catch (e) {}
      }
    } catch (e) {}
  };

  window.mdViewerTtsClear = function () {
    try {
      var cw =
        typeof contentFrame !== "undefined" &&
        contentFrame &&
        contentFrame.contentWindow
          ? contentFrame.contentWindow
          : null;
      if (cw) {
        cw.postMessage({ type: "tts-highlight-clear" }, "*");
      }
    } catch (e) {}
    try {
      if (!ttsNow || !ttsNowText) return;
      if (ttsNowTimeout) {
        clearTimeout(ttsNowTimeout);
        ttsNowTimeout = null;
      }
      ttsNowText.textContent = "—";
      ttsNow.style.display = "none";
    } catch (e) {}
  };

  if (ttsPanelToggle) {
    ttsPanelToggle.onclick = function (e) {
      e.preventDefault();
      var cur = ttsPanel && ttsPanel.style.display === "flex";
      setTtsVisible(!cur);
      updateTtsLabels();
    };
  }

  try {
    if (window.chatBridge) {
      if (ttsSpeedRange && window.chatBridge.ttsGetSpeed)
        ttsSpeedRange.value = String(window.chatBridge.ttsGetSpeed() || "1.00");
      if (ttsSilRange && window.chatBridge.ttsGetSentenceSilence)
        ttsSilRange.value = String(
          window.chatBridge.ttsGetSentenceSilence() || "0.25",
        );
      updateTtsLabels();
    }
  } catch (e) {}

  if (ttsSpeedRange) {
    ttsSpeedRange.oninput = function () {
      updateTtsLabels();
      try {
        if (window.chatBridge && window.chatBridge.ttsSetSpeed)
          window.chatBridge.ttsSetSpeed(
            parseFloat(ttsSpeedRange.value || "1.0"),
          );
      } catch (e) {}
    };
  }
  if (ttsSilRange) {
    ttsSilRange.oninput = function () {
      updateTtsLabels();
      try {
        if (window.chatBridge && window.chatBridge.ttsSetSentenceSilence)
          window.chatBridge.ttsSetSentenceSilence(
            parseFloat(ttsSilRange.value || "0.25"),
          );
      } catch (e) {}
    };
  }

  function speakSelOrDoc() {
    try {
      if (!window.chatBridge) return;
      var frame = document.getElementById("contentFrame");
      var sel = "";
      try {
        if (frame && frame.contentWindow && frame.contentWindow.getSelection) {
          sel = String(
            frame.contentWindow.getSelection().toString() || "",
          ).trim();
        }
      } catch (e) {}
      if (sel && window.chatBridge.ttsSpeakText)
        window.chatBridge.ttsSpeakText(sel);
      else if (window.chatBridge.ttsSpeakCurrentDoc)
        window.chatBridge.ttsSpeakCurrentDoc();
    } catch (e) {}
  }

  if (ttsSpeakCmd)
    ttsSpeakCmd.onclick = function () {
      speakSelOrDoc();
    };
  if (ttsPauseCmd)
    ttsPauseCmd.onclick = function () {
      try {
        if (window.chatBridge && window.chatBridge.ttsTogglePause)
          window.chatBridge.ttsTogglePause();
      } catch (e) {}
    };
  if (ttsStopCmd)
    ttsStopCmd.onclick = function () {
      try {
        if (window.chatBridge && window.chatBridge.ttsStop)
          window.chatBridge.ttsStop();
      } catch (e) {}
    };
  document.getElementById("readerFontDown").onclick = function () {
    readerPrefs.fontScale = Math.max(
      0.9,
      Math.round((readerPrefs.fontScale - 0.05) * 100) / 100,
    );
    saveJson("study_md_desk_reader_prefs", readerPrefs);
    applyReaderPrefs();
    scheduleSettingsSync();
  };
  metaPrefs = safeParse("study_md_desk_meta_prefs", metaPrefs);
  var secCb = document.getElementById("toggleMetaSection");
  var progCb = document.getElementById("toggleMetaProgress");
  var readCb = document.getElementById("toggleMetaReading");
  if (secCb) secCb.checked = metaPrefs.section !== false;
  if (progCb) progCb.checked = metaPrefs.progress !== false;
  if (readCb) readCb.checked = metaPrefs.reading !== false;
  updateMetaStrip();
  if (secCb)
    secCb.onchange = function () {
      metaPrefs.section = !!secCb.checked;
      saveJson("study_md_desk_meta_prefs", metaPrefs);
      updateMetaStrip();
      scheduleSettingsSync();
    };
  if (progCb)
    progCb.onchange = function () {
      metaPrefs.progress = !!progCb.checked;
      saveJson("study_md_desk_meta_prefs", metaPrefs);
      updateMetaStrip();
      scheduleSettingsSync();
    };
  if (readCb)
    readCb.onchange = function () {
      metaPrefs.reading = !!readCb.checked;
      saveJson("study_md_desk_meta_prefs", metaPrefs);
      updateMetaStrip();
      scheduleSettingsSync();
    };
  var sepiaCb = document.getElementById("toggleSepia");
  if (sepiaCb) {
    sepiaCb.checked = !!readerPrefs.sepia;
    sepiaCb.onchange = function () {
      readerPrefs.sepia = !!sepiaCb.checked;
      saveJson("study_md_desk_reader_prefs", readerPrefs);
      applyReaderPrefs();
      scheduleSettingsSync();
    };
  }
  document.getElementById("readerFontReset").onclick = function () {
    readerPrefs.fontScale = 1;
    saveJson("study_md_desk_reader_prefs", readerPrefs);
    applyReaderPrefs();
    scheduleSettingsSync();
  };
  document.getElementById("readerFontUp").onclick = function () {
    readerPrefs.fontScale = Math.min(
      1.3,
      Math.round((readerPrefs.fontScale + 0.05) * 100) / 100,
    );
    saveJson("study_md_desk_reader_prefs", readerPrefs);
    applyReaderPrefs();
    scheduleSettingsSync();
  };
  document.querySelectorAll(".folder[data-toggle]").forEach(function (el) {
    el.onclick = function (e) {
      e.preventDefault();
      el.classList.toggle("expanded");
      el.classList.toggle("collapsed");
    };
  });
})();
window.mdViewerSetPanel = function (name, checked) {
  setPanelState(name, checked);
};
window.mdViewerGetPanels = function () {
  return JSON.stringify(panelState);
};
window.mdViewerScrollToAnchor = function (anchorId) {
  var id = String(anchorId || "").trim();
  if (!id) return false;
  try {
    var frame = document.getElementById("contentFrame");
    if (!frame || !frame.contentWindow || !frame.contentDocument) return false;
    var doc = frame.contentDocument;
    var el = doc.getElementById(id);
    if (!el) {
      el = doc.querySelector('[id=\"' + CSS.escape(id) + '\"]');
    }
    if (!el) return false;
    if (el.scrollIntoView)
      el.scrollIntoView({ block: "center", behavior: "auto" });
    return true;
  } catch (e) {
    return false;
  }
};
window.mdViewerFindInDoc = function (text) {
  var q = String(text || "").trim();
  if (!q) return false;
  try {
    var frame = document.getElementById("contentFrame");
    if (!frame || !frame.contentWindow) return false;
    var win = frame.contentWindow;
    if (win.find) {
      return !!win.find(q, false, false, true, false, true, false);
    }
  } catch (e) {}
  return false;
};
document.addEventListener("keydown", function (e) {
  if (e.ctrlKey && e.key === "f") {
    e.preventDefault();
    var filter = document.getElementById("libraryFilter");
    if (filter) filter.focus();
  }
});
function loadToc() {
  try {
    var loc = contentFrame.contentWindow.location;
    if (loc.pathname.indexOf("/view/") !== 0) {
      hideContentLoading();
      return;
    }
    var path = loc.pathname.replace("/view/", "").split("?")[0];
    try {
      path = decodeURIComponent(path);
    } catch (e) {}
    var root = (loc.search.match(/root=([^&]+)/) || [])[1] || "";
    if (root) root = decodeURIComponent(root);
    currentDoc.path = path || "";
    currentDoc.root = root || "";
    if (!currentDoc.title || currentDoc.title === "No document open")
      currentDoc.title = prettyName(path);
    fetch(
      "/toc?path=" +
        encodeURIComponent(path) +
        "&root=" +
        encodeURIComponent(root),
    )
      .then(function (r) {
        return r.text();
      })
      .then(function (html) {
        document.getElementById("tocContent").innerHTML =
          html || '<p style="color:#6b7280;font-size:11px;">No table of contents</p>';
        highlightActiveToc("");
        if (
          typeof INITIAL_STATE === "object" &&
          INITIAL_STATE &&
          INITIAL_STATE.activeTocId
        ) {
          var anchor = INITIAL_STATE.activeTocId;
          highlightActiveToc(anchor);
          try {
            var win = contentFrame.contentWindow;
            if (win && win.document) {
              var el = win.document.getElementById(anchor);
              if (el && el.scrollIntoView)
                el.scrollIntoView({ block: "start", behavior: "auto" });
            }
          } catch (e) {}
          try {
            INITIAL_STATE.activeTocId = null;
          } catch (e) {}
        }
      })
      .catch(function () {
        document.getElementById("tocContent").innerHTML = "";
      });
    refreshStudyUI();
    if (docLoadingActive) {
      if (docLoadingFallbackTimer) clearTimeout(docLoadingFallbackTimer);
      docLoadingFallbackTimer = setTimeout(hideContentLoading, 5000);
    }
    applyReaderPrefs();
  } catch (e) {
    document.getElementById("tocContent").innerHTML = "";
    hideContentLoading();
  }
}
contentFrame.addEventListener("load", loadToc);
loadToc();
var cmEditor = null;
(function initCodeMirror() {
  if (typeof CodeMirror === "undefined") return;
  var wrap = document.getElementById("replCodeMirror");
  var ta = document.getElementById("replCode");
  cmEditor = CodeMirror(wrap, {
    value: "",
    mode: "python",
    lineNumbers: false,
    indentUnit: 4,
    extraKeys: {
      "Ctrl-Enter": function () {
        runCode();
      },
    },
  });
  wrap.style.display = "block";
  ta.style.display = "none";
  cmEditor.getWrapperElement().classList.add("CodeMirror-wrap");
  cmEditor.setOption("theme", "monokai");
  setTimeout(function () {
    if (cmEditor) cmEditor.refresh();
  }, 100);
  window.addEventListener("resize", function () {
    if (cmEditor) cmEditor.refresh();
  });
})();
function getReplCode() {
  return cmEditor
    ? cmEditor.getValue()
    : document.getElementById("replCode").value;
}
function setReplCode(v) {
  if (cmEditor) cmEditor.setValue(v);
  else document.getElementById("replCode").value = v;
}
function runCode() {
  var code = getReplCode();
  if (!code.trim()) return;
  var pythonPath = document.getElementById("pythonSelect").value;
  var out = document.getElementById("replOut");
  out.textContent = "Running...";
  fetch("/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code, python: pythonPath }),
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      var s = data.stdout || "";
      if (data.stderr) s += (s ? "\\n" : "") + "stderr:\\n" + data.stderr;
      if (s === "") s = "(no output)";
      out.textContent = s;
    })
    .catch(function (err) {
      out.textContent = "Error: " + err;
    });
}
document.getElementById("replRun").onclick = runCode;
window.addEventListener("message", function (e) {
  if (e.data && e.data.type === "doc-render-ready") {
    try {
      if (
        contentFrame &&
        contentFrame.contentWindow &&
        e.source === contentFrame.contentWindow
      ) {
        hideContentLoading();
      }
    } catch (err) {}
  }
  if (e.data && e.data.type === "doc-meta") {
    currentDoc.title =
      e.data.title || currentDoc.title || prettyName(currentDoc.path);
    currentDoc.section = e.data.section || currentDoc.section || "";
    currentDoc.readingTime = e.data.readingTime || currentDoc.readingTime || "";
    refreshStudyUI();
    persistCurrentDoc();
  }
  if (e.data && e.data.type === "active-section") {
    currentDoc.section = e.data.title || currentDoc.section || "";
    highlightActiveToc(e.data.id || "");
    updateMetaStrip();
    scheduleSettingsSync();
  }
  if (e.data && e.data.type === "scroll-progress") {
    var pct = Math.min(100, Math.max(0, (e.data.progress || 0) * 100));
    document.getElementById("progressFill").style.width = pct + "%";
    currentDoc.progress = pct;
    updateMetaStrip();
    persistCurrentDoc();
  }
  if (e.data && e.data.type === "ask-in-chat" && e.data.prompt) {
    if (window.chatBridge) window.chatBridge.askInChat(e.data.prompt);
  }
  if (e.data && e.data.type === "tts-speak" && e.data.text) {
    try {
      if (window.chatBridge && window.chatBridge.ttsSpeakText)
        window.chatBridge.ttsSpeakText(String(e.data.text || ""));
    } catch (err) {}
  }
  if (e.data && e.data.type === "copy-text") {
    try {
      var t = String(e.data.text || "");
      if (window.chatBridge && window.chatBridge.copyToClipboard) {
        window.chatBridge.copyToClipboard(t);
      } else if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t);
      } else {
        var ta = document.createElement("textarea");
        ta.value = t;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        ta.style.top = "-9999px";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        try {
          document.execCommand("copy");
        } catch (e) {}
        document.body.removeChild(ta);
      }
    } catch (err) {}
  }

  if (e.data && e.data.type === "add-note") {
    try {
      if (window.mdViewerOpenNotesModal)
        window.mdViewerOpenNotesModal({
          text: String(e.data.text || ""),
          note: String(e.data.note || ""),
          op: String(e.data.op || "create"),
          range: e.data.range || null,
          headingId: String(e.data.headingId || ""),
          headingTitle: String(e.data.headingTitle || ""),
          root: String(currentDoc.root || ""),
          path: String(currentDoc.path || ""),
        });
    } catch (err) {}
  }
  if (e.data && e.data.type === "delete-note") {
    try {
      var q = String(e.data.quote || "");
      var hid = String(e.data.headingId || "");
      if (!q) return;
      var payload = {
        root: String(currentDoc.root || ""),
        path: String(currentDoc.path || ""),
        clipDelete: { quote: q, headingId: hid, range: e.data.range || null },
      };
      fetch("/notes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function () {
          try {
            var w =
              typeof contentFrame !== "undefined" &&
              contentFrame &&
              contentFrame.contentWindow
                ? contentFrame.contentWindow
                : null;
            if (w && w.mdViewerRemoveClip && e.data.range) {
              w.mdViewerRemoveClip(e.data.range);
            } else if (w && w.location && w.location.reload) {
              w.location.reload();
            } else {
              location.reload();
            }
          } catch (e) {
            try {
              location.reload();
            } catch (e2) {}
          }
        })
        .catch(function () {});
    } catch (err) {}
  }
});
