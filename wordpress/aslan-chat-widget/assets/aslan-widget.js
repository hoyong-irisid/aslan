(function () {
  function bootAslanWidget() {
      if (window.__ASLAN_WIDGET_INIT) return;
      var root = document.getElementById("aslan-widget-root");
      if (!root) return;

      var CFG = window.ASLAN_WIDGET || {};
      var API_BASE = String(CFG.apiBase || "").replace(/\/$/, "");
      var ASSETS_BASE = String(CFG.assetsBase || "").replace(/\/$/, "");
      var TRANSCRIPT_RECIPIENT = CFG.transcriptRecipient || "hoyong.lee@irisid.com";
      var PARTNER_TOKEN_KEY = "aslan.partner_token";

      function clientTimezone() {
        try {
          return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
        } catch (e) {
          return null;
        }
      }
      
      var chatHistory = [];
      function resolveAssetUrl(u) {
        var s = String(u || "");
        if (!s) return s;
        if (/^https?:\/\//i.test(s)) return s;
        var assetMatch = s.match(/^\/partner\/asset\/([^/?#]+)(?:\?(.*))?$/);
        if (assetMatch && CFG.useWpProxy && CFG.assetProxyUrl) {
          var id = decodeURIComponent(assetMatch[1]);
          var token = "";
          var qs = assetMatch[2] || "";
          qs.split("&").forEach(function (pair) {
            var p = pair.split("=");
            if (p[0] === "token" && p[1]) token = decodeURIComponent(p[1]);
          });
          return (
            CFG.assetProxyUrl +
            "&asset_id=" +
            encodeURIComponent(id) +
            "&token=" +
            encodeURIComponent(token)
          );
        }
        if (s.charAt(0) === "/" && API_BASE) return API_BASE + s;
        return s;
      }


      function getPartnerToken() {
        try { return window.sessionStorage.getItem(PARTNER_TOKEN_KEY) || null; }
        catch (e) { return null; }
      }
      function setPartnerToken(tok) {
        try {
          if (tok) window.sessionStorage.setItem(PARTNER_TOKEN_KEY, tok);
          else window.sessionStorage.removeItem(PARTNER_TOKEN_KEY);
        } catch (e) {}
      }

      var fab = root.querySelector("#aslanFab");
      if (!fab) return;
      var panel = root.querySelector("#aslanPanel");
      if (!panel) return;
      var closeBtn = root.querySelector("#aslanClose");
      var partnerBadge = root.querySelector("#aslanPartnerBadge");
      var messagesEl = root.querySelector("#aslanMessages");
      var input = root.querySelector("#aslanInput");
      var sendBtn = root.querySelector("#aslanSend");
      var micBtn = root.querySelector("#aslanMic");
      var chipsEl = root.querySelector("#aslanChips");
      var toastEl = root.querySelector("#aslanToast");
      var imageLightbox = root.querySelector("#aslanImageLightbox");
      var imageLightboxImg = root.querySelector("#aslanImageLightboxImg");
      var imageLightboxClose = root.querySelector("#aslanImageLightboxClose");
      var typingEl = null;
      var toastTimer = null;
      var closingPanel = false;

      var SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
      var voiceRec = null;
      var voiceBaseText = "";

      var WELCOME_TEXT =
        "Hi, I’m the Iris ID assistant. Ask anything, or tap a suggestion below.";

      var suggestions = [
        "How can I contact support?",
        "Request a demo",
        "Product documentation",
        "Office location",
      ];

      function _extractImageUrls(text) {
        var out = [];
        var lines = String(text || "").split(/\n/);
        lines.forEach(function (ln) {
          var m = ln.match(/^\s*IMAGE_URL:\s*(\S+)\s*$/i);
          if (m && m[1]) out.push(m[1]);
        });
        return out;
      }

      function _extractFigureOptions(text) {
        var out = [];
        var lines = String(text || "").split(/\n/);
        lines.forEach(function (ln) {
          var m = ln.match(/^\s*FIGURE_OPTION:\s*([a-z0-9\-_.]+)\|(.*)$/i);
          if (!m) return;
          var id = (m[1] || "").trim();
          var title = (m[2] || "").trim() || id;
          if (id) out.push({ id: id, title: title });
        });
        return out;
      }

      function _extractChoiceOptions(text) {
        var src = String(text || "");
        var lines = src.split(/\n/);
        var opts = [];
        var kept = [];
        lines.forEach(function (ln) {
          var m = ln.match(/^\s*(?:[-*•]|\d+[.)])\s*["']?([^"'\n]+?)["']?\s*$/);
          if (m && m[1]) {
            var option = m[1].trim();
            if (option.length >= 3) {
              opts.push(option);
              return;
            }
          }
          kept.push(ln);
        });
        // De-dupe and keep only reasonable, explicit options.
        var uniq = [];
        var seen = {};
        opts.forEach(function (o) {
          var k = o.toLowerCase();
          if (seen[k]) return;
          seen[k] = true;
          uniq.push(o);
        });
        if (uniq.length < 2 || uniq.length > 8) {
          return { options: [], stripped: src };
        }
        return { options: uniq, stripped: kept.join("\n").trim() || src };
      }

      function syncPartnerBadge() {
        if (!partnerBadge) return;
        var verified = !!getPartnerToken();
        partnerBadge.classList.toggle("verified", verified);
        if (verified) {
          partnerBadge.innerHTML =
            '<span class="picon-check" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12Zm13.36-1.814a.75.75 0 1 0-1.22-.872l-3.236 4.53L9.53 12.22a.75.75 0 0 0-1.06 1.06l2.25 2.25a.75.75 0 0 0 1.14-.094l3.75-5.25Z" clip-rule="evenodd"/></svg></span><span>Verified</span>';
          partnerBadge.setAttribute("aria-label", "Verified partner");
        } else {
          partnerBadge.innerHTML =
            '<span class="picon-dot" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 1a4.5 4.5 0 0 0-4.5 4.5V9H5a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2h-.5V5.5A4.5 4.5 0 0 0 10 1Zm3 8V5.5a3 3 0 1 0-6 0V9h6Z" clip-rule="evenodd"/></svg></span><span>Partner</span>';
          partnerBadge.setAttribute("aria-label", "Partner login");
        }
      }

      function appendMessage(role, text, isError) {
        var raw = String(text || "");
        if (role === "user") {
          chatHistory.push({ role: "user", text: raw });
        } else {
          chatHistory.push({ role: "assistant", text: raw });
        }
        var div = document.createElement("div");
        div.className = "msg " + (isError ? "error" : role === "user" ? "user" : "bot");
        var imageUrls = _extractImageUrls(raw);
        var figureOptions = _extractFigureOptions(raw);
        var genericChoice = _extractChoiceOptions(raw);
        var clean = raw
          .replace(/^\s*IMAGE_URL:\s*\S+\s*$/gim, "")
          .replace(/^\s*FIGURE_OPTION:\s*([a-z0-9\-_.]+)\|(.*)$/gim, "")
          .trim();
        var displayBase = genericChoice.options.length > 0 ? genericChoice.stripped : (clean || raw);
        var displayText = displayBase.replace(/^(\s*)\*\s+/gm, "$1• ");
        renderInlineBold(div, displayText);
        imageUrls.forEach(function (u) {
          var img = document.createElement("img");
          img.className = "msg-image";
          img.src = resolveAssetUrl(u);
          img.alt = "Partner figure";
          img.loading = "lazy";
          img.addEventListener("click", function () {
            openImageLightbox(resolveAssetUrl(u), img.alt || "Expanded chat image");
          });
          div.appendChild(img);
        });
        if (figureOptions.length > 0) {
          var wrap = document.createElement("div");
          wrap.className = "figure-options";
          figureOptions.forEach(function (opt) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "figure-option-btn";
            b.textContent = opt.title;
            b.addEventListener("click", function () {
              var tok = getPartnerToken();
              if (!tok) {
                appendMessage("bot", "Partner session expired. Please verify partner code again.", true);
                return;
              }
              appendMessage("user", "Show image: " + opt.title);
              appendMessage(
                "bot",
                "Figure: " + opt.title + "\nIMAGE_URL: /partner/asset/" + encodeURIComponent(opt.id) + "?token=" + encodeURIComponent(tok)
              );
            });
            wrap.appendChild(b);
          });
          div.appendChild(wrap);
        } else if (genericChoice.options.length > 0) {
          var choiceWrap = document.createElement("div");
          choiceWrap.className = "choice-options";
          var radioName = "aslan-choice-" + Date.now() + "-" + Math.floor(Math.random() * 10000);
          genericChoice.options.forEach(function (opt, idx) {
            var row = document.createElement("label");
            row.className = "choice-option-row";
            var r = document.createElement("input");
            r.type = "radio";
            r.name = radioName;
            r.value = opt;
            if (idx === 0) r.checked = true;
            var s = document.createElement("span");
            renderInlineBold(s, opt);
            row.appendChild(r);
            row.appendChild(s);
            choiceWrap.appendChild(row);
          });
          var action = document.createElement("div");
          action.className = "choice-option-actions";
          var chooseBtn = document.createElement("button");
          chooseBtn.type = "button";
          chooseBtn.className = "choice-option-send";
          chooseBtn.textContent = "Select";
          chooseBtn.addEventListener("click", function () {
            var selected = choiceWrap.querySelector('input[name="' + radioName + '"]:checked');
            if (!selected) return;
            input.value = selected.value;
            sendMessage();
          });
          action.appendChild(chooseBtn);
          choiceWrap.appendChild(action);
          div.appendChild(choiceWrap);
        }
        messagesEl.appendChild(div);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function openImageLightbox(src, alt) {
        if (!imageLightbox || !imageLightboxImg) return;
        imageLightboxImg.src = resolveAssetUrl(src);
        imageLightboxImg.alt = String(alt || "Expanded chat image");
        imageLightbox.classList.add("open");
        imageLightbox.setAttribute("aria-hidden", "false");
      }

      function closeImageLightbox() {
        if (!imageLightbox || !imageLightboxImg) return;
        imageLightbox.classList.remove("open");
        imageLightbox.setAttribute("aria-hidden", "true");
        imageLightboxImg.removeAttribute("src");
      }

      function _appendTextWithLinks(el, text) {
        var src = String(text || "");
        var re = /(https?:\/\/[^\s<>"')\]]+)/gi;
        var last = 0;
        var m;
        while ((m = re.exec(src)) !== null) {
          if (m.index > last) {
            el.appendChild(document.createTextNode(src.slice(last, m.index)));
          }
          var a = document.createElement("a");
          a.href = m[1];
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = m[1];
          el.appendChild(a);
          last = re.lastIndex;
        }
        if (last < src.length) {
          el.appendChild(document.createTextNode(src.slice(last)));
        }
      }

      function renderInlineBold(el, text) {
        var src = String(text || "");
        var re = /\*\*(.+?)\*\*/g;
        var last = 0;
        var m;
        while ((m = re.exec(src)) !== null) {
          if (m.index > last) {
            _appendTextWithLinks(el, src.slice(last, m.index));
          }
          var strong = document.createElement("strong");
          _appendTextWithLinks(strong, m[1]);
          el.appendChild(strong);
          last = re.lastIndex;
        }
        if (last < src.length) {
          _appendTextWithLinks(el, src.slice(last));
        }
      }

      function showTyping() {
        if (typingEl) return;
        typingEl = document.createElement("div");
        typingEl.className = "msg typing";
        typingEl.setAttribute("aria-label", "Assistant is typing");
        typingEl.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
        messagesEl.appendChild(typingEl);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function hideTyping() {
        if (!typingEl) return;
        typingEl.remove();
        typingEl = null;
      }

      function setLoading(loading) {
        if (loading) stopVoiceInput();
        sendBtn.disabled = loading;
        if (micBtn) micBtn.disabled = loading;
        if (loading) sendBtn.textContent = "…";
        else sendBtn.textContent = "Send";
      }

      function showToast(message, isError) {
        if (!toastEl) return;
        if (toastTimer) {
          window.clearTimeout(toastTimer);
          toastTimer = null;
        }
        toastEl.textContent = String(message || "");
        toastEl.classList.toggle("error", !!isError);
        toastEl.classList.add("show");
        toastTimer = window.setTimeout(function () {
          toastEl.classList.remove("show");
        }, isError ? 3600 : 2600);
      }


      function stopVoiceInput() {
        if (voiceRec) {
          try {
            voiceRec.onresult = null;
            voiceRec.onerror = null;
            voiceRec.onend = null;
            voiceRec.stop();
          } catch (e) {}
          voiceRec = null;
        }
        if (micBtn) {
          micBtn.classList.remove("listening");
          micBtn.setAttribute("aria-pressed", "false");
        }
      }

      function toggleVoiceInput() {
        if (!SpeechRec) {
          appendMessage(
            "bot",
            "Voice input is not supported in this browser. Try Chrome or Edge over HTTPS (or localhost).",
            true
          );
          return;
        }
        if (micBtn && micBtn.classList.contains("listening")) {
          stopVoiceInput();
          return;
        }
        stopVoiceInput();
        voiceBaseText = (input.value || "").trim();
        if (voiceBaseText) voiceBaseText += " ";

        voiceRec = new SpeechRec();
        voiceRec.lang = (navigator.language || "en-US").replace(/_/g, "-");
        voiceRec.interimResults = true;
        voiceRec.continuous = false;
        voiceRec.maxAlternatives = 1;

        voiceRec.onresult = function (event) {
          var piece = "";
          for (var i = 0; i < event.results.length; i++) {
            piece += event.results[i][0].transcript;
          }
          input.value = voiceBaseText + piece;
        };
        voiceRec.onerror = function (event) {
          var msg = event.error || "unknown";
          if (msg === "not-allowed") {
            appendMessage("bot", "Microphone permission denied. Allow mic for this site and try again.", true);
          } else if (msg !== "aborted" && msg !== "no-speech") {
            appendMessage("bot", "Voice input error: " + msg, true);
          }
          stopVoiceInput();
        };
        voiceRec.onend = function () {
          stopVoiceInput();
        };

        try {
          if (micBtn) {
            micBtn.classList.add("listening");
            micBtn.setAttribute("aria-pressed", "true");
          }
          voiceRec.start();
        } catch (e) {
          stopVoiceInput();
          appendMessage("bot", "Could not start voice input.", true);
        }
      }

      function renderChips() {
        chipsEl.innerHTML = "";
        suggestions.forEach(function (label) {
          var b = document.createElement("button");
          b.type = "button";
          b.className = "chip";
          b.textContent = label;
          b.addEventListener("click", function () {
            input.value = label;
            sendMessage();
          });
          chipsEl.appendChild(b);
        });
      }

      function resetChatUI() {
        stopVoiceInput();
        hideTyping();
        messagesEl.innerHTML = "";
        chatHistory = [];
        input.value = "";
        setLoading(false);
        syncPartnerBadge();
      }

      function openPanelFresh() {
        resetChatUI();
        appendMessage("bot", WELCOME_TEXT);
        messagesEl.scrollTop = 0;
        syncPartnerBadge();
      }

      function togglePanel(open) {
        var wasOpen = panel.classList.contains("open");
        if (open && !wasOpen) {
          openPanelFresh();
        } else if (!open && wasOpen) {
          resetChatUI();
          // Partner auth is tied to this chat session only; closing ends it.
          setPartnerToken(null);
          syncPartnerBadge();
        }
        panel.classList.toggle("open", open);
        document.body.classList.toggle("aslan-chat-open", open);
      }

      async function postTranscriptEmail() {
        var tok = getPartnerToken();
        if (!tok) throw new Error("Partner session required");
        var res = await fetch(API_BASE + "/email/chat-transcript", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            partner_token: tok,
            to_email: TRANSCRIPT_RECIPIENT,
            messages: chatHistory,
          }),
        });
        var data = await res.json().catch(function () {
          return {};
        });
        if (!res.ok) {
          throw new Error(data.detail || res.statusText || "Email failed");
        }
        return data;
      }

      async function closePanelWithOptionalEmail() {
        if (closingPanel) return;
        closingPanel = true;
        try {
          var tok = getPartnerToken();
          if (tok && chatHistory.length > 0) {
            try {
              await postTranscriptEmail();
              showToast("Transcript sent to hoyong.lee@irisid.com.");
            } catch (err) {
              showToast(err && err.message ? err.message : String(err), true);
            }
          }
        } finally {
          // Closing the chat always ends partner session for this widget session.
          setPartnerToken(null);
          syncPartnerBadge();
          togglePanel(false);
          closingPanel = false;
        }
      }

      fab.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (closingPanel) return;
        if (panel.classList.contains("open")) {
          closePanelWithOptionalEmail();
        } else {
          togglePanel(true);
        }
      });
      closeBtn.addEventListener("click", function (e) {
        e.preventDefault();
        if (closingPanel) return;
        closePanelWithOptionalEmail();
      });
      if (partnerBadge) {
        partnerBadge.addEventListener("click", function () {
          if (getPartnerToken()) return;
          appendMessage(
            "bot",
            "Please enter your Partner access ID in the message box below."
          );
          input.focus();
        });
      }

      async function sendMessage() {
        stopVoiceInput();
        var text = (input.value || "").trim();
        if (!text) return;
        input.value = "";
        appendMessage("user", text);
        setLoading(true);
        showTyping();
        try {
          var res = await fetch(API_BASE + "/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              message: text,
              region_hint: null,
              client_timezone: clientTimezone(),
              partner_token: getPartnerToken(),
              chat_history: chatHistory.slice(0, -1),
            }),
          });
          var data = await res.json().catch(function () { return {}; });
          hideTyping();
          if (!res.ok) {
            appendMessage("bot", data.detail || res.statusText || "Request failed", true);
            return;
          }
          if (data.partner_authenticated && data.partner_token) {
            setPartnerToken(data.partner_token);
            syncPartnerBadge();
          }
          appendMessage("bot", data.reply || "(empty reply)");
        } catch (e) {
          hideTyping();
          appendMessage("bot", "Network error: " + (e && e.message ? e.message : e), true);
        } finally {
          hideTyping();
          setLoading(false);
        }
      }

      sendBtn.addEventListener("click", sendMessage);
      if (micBtn) micBtn.addEventListener("click", toggleVoiceInput);
      // IME (Korean, Japanese, …): Enter commits composition — ignore that keydown or text duplicates / double send.
      input.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" || e.shiftKey) return;
        if (e.isComposing || e.keyCode === 229) return;
        e.preventDefault();
        sendMessage();
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && imageLightbox && imageLightbox.classList.contains("open")) {
          closeImageLightbox();
          return;
        }
      });
      if (imageLightbox) {
        imageLightbox.addEventListener("click", function (e) {
          if (e.target === imageLightbox) closeImageLightbox();
        });
      }
      if (imageLightboxClose) {
        imageLightboxClose.addEventListener("click", closeImageLightbox);
      }

      // Partner auth is per-chat/session only. Never restore from previous page lifetime.
      window.__ASLAN_WIDGET_INIT = true;
      setPartnerToken(null);
      renderChips();
      syncPartnerBadge();
  }

  function scheduleBoot() {
    bootAslanWidget();
    if (!window.__ASLAN_WIDGET_INIT) {
      window.setTimeout(bootAslanWidget, 250);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scheduleBoot);
  } else {
    scheduleBoot();
  }
  window.addEventListener("load", bootAslanWidget);
})();