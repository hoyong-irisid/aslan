# ASLAN Chat Widget — WordPress embed

Floating chat button (bottom-right) for irisid.com (or staging). Connects to the ASLAN FastAPI backend.

## Files

```
wordpress/aslan-chat-widget/
  aslan-chat-widget.php   # WordPress plugin
  assets/
    aslan-widget.css
    aslan-widget.js
    symbol-irisid-white.svg
    iris-logo-symbol-white.svg
```

## 1. Install the plugin

1. Zip the folder `aslan-chat-widget` (the folder itself, not only its contents).
2. WordPress Admin → **Plugins → Add New → Upload Plugin** → upload zip → **Activate**.

Or copy the folder to:

`wp-content/plugins/aslan-chat-widget/`

## 2. Configure API URL

**Settings → ASLAN Chat**

| Field | Example |
|-------|---------|
| API base URL | `https://your-aslan-api.example.com` |
| Auto-load page slugs | `chat-widget-test` |
| Header subtitle | `IRIS ID · ASLAN` |

For local testing from a live WP site you need a public tunnel (ngrok, Cloudflare Tunnel) pointing to your local uvicorn, e.g. `https://abc123.ngrok-free.app`.

## 3. Create a test page (duplicate Home)

1. **Pages → Home** (or your landing page) → **Duplicate** (Duplicate Post plugin or manual copy).
2. Title: `Chat widget test` (any title).
3. **Slug:** `chat-widget-test` (must match Settings → page slugs).
4. Publish (can stay **Private** for internal test).
5. Open the page — FAB appears bottom-right.

Optional: add shortcode `[aslan_chat]` in page content to force-load on any slug.

## 4. Run ASLAN API

```bash
cd /path/to/aslan
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8010
```

CORS is already open (`allow_origins=["*"]`) in `app/main.py` for cross-origin `/chat` calls.

## 5. Production checklist

- [ ] ASLAN API on HTTPS (not localhost)
- [ ] API base URL in WP settings
- [ ] Partner images: `/partner/asset/...` uses WP `admin-ajax.php?action=aslan_partner_asset` when proxy mode is on
- [ ] Resend/SMTP configured on API server for transcript email on chat close
- [ ] Remove or restrict test page slug before go-live

## Troubleshooting

| Issue | Fix |
|-------|-----|
| No chat button | Check page slug matches settings; plugin activated |
| "Network error" | API URL wrong, API down, or mixed content (HTTPS page → HTTP API blocked) |
| "GOOGLE_API_KEY is not set" | Fix `.env` on API server, restart uvicorn |
| Partner images broken | Ensure API base URL is set; images load from API host |
