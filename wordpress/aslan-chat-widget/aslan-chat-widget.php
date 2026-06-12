<?php
/**
 * Plugin Name: ASLAN Chat Widget
 * Description: IRIS ID floating chat widget (bottom-right). Connects to the ASLAN FastAPI backend.
 * Version: 0.1.9
 * Author: IRIS ID
 * Text Domain: aslan-chat-widget
 */

if (!defined('ABSPATH')) {
    exit;
}

define('ASLAN_WIDGET_VERSION', '0.1.9');
define('ASLAN_WIDGET_FILE', __FILE__);
define('ASLAN_WIDGET_DIR', plugin_dir_path(__FILE__));
define('ASLAN_WIDGET_URL', plugin_dir_url(__FILE__));

/**
 * Default options (overridden in Settings → ASLAN Chat).
 */
function aslan_widget_default_options(): array
{
    return [
        'api_base_url' => '',
        // Comma-separated page slugs, e.g. chat-widget-test,home-copy
        'page_slugs' => 'chat-widget-test',
        'subtitle' => 'IRIS ID · ASLAN',
        'transcript_recipient' => 'hoyong.lee@irisid.com',
        // Server-side only: FastAPI on the same VPS (not exposed to the browser).
        'api_internal_url' => 'http://127.0.0.1:8010',
        // Route /chat via WordPress REST (fixes nginx proxy gaps on chat-api subdomain).
        'use_wp_proxy' => '1',
    ];
}

function aslan_widget_get_option(string $key): string
{
    $defaults = aslan_widget_default_options();
    $value = get_option('aslan_widget_' . $key, $defaults[$key] ?? '');
    return is_string($value) ? $value : '';
}

function aslan_widget_should_load(): bool
{
    if (is_admin()) {
        return false;
    }

    // Shortcode on this page/post.
    global $post;
    if ($post instanceof WP_Post && has_shortcode($post->post_content, 'aslan_chat')) {
        return true;
    }

    $slugs = array_filter(array_map('trim', explode(',', aslan_widget_get_option('page_slugs'))));
    if (!$slugs) {
        return false;
    }

    if (!is_page()) {
        return false;
    }

    $current = get_post_field('post_name', get_queried_object_id());
    return in_array($current, $slugs, true);
}

function aslan_widget_enqueue_assets(): void
{
    if (!aslan_widget_should_load()) {
        return;
    }

    wp_enqueue_style(
        'aslan-chat-widget',
        ASLAN_WIDGET_URL . 'assets/aslan-widget.css',
        [],
        ASLAN_WIDGET_VERSION
    );

    // JS is printed inline after widget markup in wp_footer for reliable init order.
}
add_action('wp_enqueue_scripts', 'aslan_widget_enqueue_assets');

function aslan_widget_internal_api_base(): string
{
    $url = aslan_widget_get_option('api_internal_url');
    if ($url === '') {
        $url = aslan_widget_default_options()['api_internal_url'];
    }
    return rtrim($url, '/');
}

function aslan_widget_use_wp_proxy(): bool
{
    return aslan_widget_get_option('use_wp_proxy') === '1';
}

function aslan_widget_public_api_base(): string
{
    if (aslan_widget_use_wp_proxy()) {
        return rtrim(rest_url('aslan/v1'), '/');
    }
    return rtrim(aslan_widget_get_option('api_base_url'), '/');
}

function aslan_widget_proxy_json(string $method, string $path, ?string $json_body = null, array $query = []): WP_REST_Response
{
    $url = aslan_widget_internal_api_base() . $path;
    if ($query !== []) {
        $url = add_query_arg($query, $url);
    }

    $args = [
        'method' => $method,
        'timeout' => 120,
        'headers' => ['Content-Type' => 'application/json'],
    ];
    if ($json_body !== null) {
        $args['body'] = $json_body;
    }

    $response = wp_remote_request($url, $args);
    if (is_wp_error($response)) {
        return new WP_REST_Response(
            ['detail' => $response->get_error_message()],
            502
        );
    }

    $code = (int) wp_remote_retrieve_response_code($response);
    $body = wp_remote_retrieve_body($response);
    $decoded = json_decode($body, true);
    if (json_last_error() === JSON_ERROR_NONE) {
        return new WP_REST_Response($decoded, $code);
    }

    return new WP_REST_Response(['detail' => $body !== '' ? $body : 'Upstream error'], $code);
}

function aslan_widget_rest_chat(WP_REST_Request $request): WP_REST_Response
{
    $payload = $request->get_json_params();
    if (!is_array($payload)) {
        return new WP_REST_Response(['detail' => 'Invalid JSON body'], 400);
    }
    return aslan_widget_proxy_json('POST', '/chat', wp_json_encode($payload));
}

function aslan_widget_rest_email_transcript(WP_REST_Request $request): WP_REST_Response
{
    $payload = $request->get_json_params();
    if (!is_array($payload)) {
        return new WP_REST_Response(['detail' => 'Invalid JSON body'], 400);
    }
    return aslan_widget_proxy_json('POST', '/email/chat-transcript', wp_json_encode($payload));
}

function aslan_widget_stream_partner_asset(): void
{
    $asset_id = isset($_REQUEST['asset_id']) ? sanitize_text_field(wp_unslash($_REQUEST['asset_id'])) : '';
    $token = isset($_REQUEST['token']) ? sanitize_text_field(wp_unslash($_REQUEST['token'])) : '';
    if ($asset_id === '') {
        status_header(400);
        header('Content-Type: application/json; charset=utf-8');
        echo wp_json_encode(['detail' => 'Missing asset_id']);
        exit;
    }

    $url = aslan_widget_internal_api_base()
        . '/partner/asset/' . rawurlencode($asset_id)
        . '?token=' . rawurlencode($token);

    $response = wp_remote_get($url, ['timeout' => 60]);
    if (is_wp_error($response)) {
        status_header(502);
        header('Content-Type: application/json; charset=utf-8');
        echo wp_json_encode(['detail' => $response->get_error_message()]);
        exit;
    }

    $code = (int) wp_remote_retrieve_response_code($response);
    $body = wp_remote_retrieve_body($response);
    $content_type = wp_remote_retrieve_header($response, 'content-type');
    if (!is_string($content_type) || $content_type === '') {
        $content_type = 'application/octet-stream';
    }

    status_header($code);
    header('Content-Type: ' . $content_type);
    header('Cache-Control: private, no-store');
    if ($code === 200 && $body !== '') {
        header('Content-Length: ' . (string) strlen($body));
    }
    echo $body;
    exit;
}
add_action('wp_ajax_nopriv_aslan_partner_asset', 'aslan_widget_stream_partner_asset');
add_action('wp_ajax_aslan_partner_asset', 'aslan_widget_stream_partner_asset');

function aslan_widget_rest_partner_asset(WP_REST_Request $request): WP_REST_Response|WP_Error
{
    $asset_id = (string) $request->get_param('asset_id');
    $token = (string) $request->get_param('token');
    if ($asset_id === '') {
        return new WP_REST_Response(['detail' => 'Missing asset_id'], 400);
    }

    $url = aslan_widget_internal_api_base()
        . '/partner/asset/' . rawurlencode($asset_id)
        . '?token=' . rawurlencode($token);

    $response = wp_remote_get($url, ['timeout' => 60]);
    if (is_wp_error($response)) {
        return new WP_REST_Response(['detail' => $response->get_error_message()], 502);
    }

    $code = (int) wp_remote_retrieve_response_code($response);
    $body = wp_remote_retrieve_body($response);
    $content_type = wp_remote_retrieve_header($response, 'content-type');

    $rest = new WP_REST_Response($body, $code);
    if (is_string($content_type) && $content_type !== '') {
        $rest->header('Content-Type', $content_type);
    }
    return $rest;
}

function aslan_widget_register_rest_routes(): void
{
    register_rest_route('aslan/v1', '/chat', [
        'methods' => 'POST',
        'callback' => 'aslan_widget_rest_chat',
        'permission_callback' => '__return_true',
    ]);
    register_rest_route('aslan/v1', '/email/chat-transcript', [
        'methods' => 'POST',
        'callback' => 'aslan_widget_rest_email_transcript',
        'permission_callback' => '__return_true',
    ]);
    register_rest_route('aslan/v1', '/partner/asset/(?P<asset_id>[a-zA-Z0-9._-]+)', [
        'methods' => 'GET',
        'callback' => 'aslan_widget_rest_partner_asset',
        'permission_callback' => '__return_true',
    ]);
}
add_action('rest_api_init', 'aslan_widget_register_rest_routes');

function aslan_widget_config(): array
{
    return [
        'apiBase' => aslan_widget_public_api_base(),
        'assetsBase' => rtrim(ASLAN_WIDGET_URL . 'assets', '/'),
        'assetProxyUrl' => admin_url('admin-ajax.php?action=aslan_partner_asset'),
        'transcriptRecipient' => aslan_widget_get_option('transcript_recipient'),
        'subtitle' => aslan_widget_get_option('subtitle'),
        'useWpProxy' => aslan_widget_use_wp_proxy(),
    ];
}

function aslan_widget_render_markup(): void
{
    if (!aslan_widget_should_load()) {
        return;
    }

    $assets = rtrim(ASLAN_WIDGET_URL . 'assets', '/');
    $fab_icon = esc_url($assets . '/symbol-irisid-white.svg');
    $header_icon = esc_url($assets . '/symbol-irisid-color-light.svg');
    $subtitle = esc_html(aslan_widget_get_option('subtitle'));
    ?>
    <div id="aslan-widget-root" aria-hidden="false">
      <button type="button" class="aslan-fab" id="aslanFab" aria-label="Open chat">
        <img class="aslan-fab-logo" src="<?php echo $fab_icon; ?>" width="160" height="auto" alt="" />
      </button>

      <div class="aslan-panel" id="aslanPanel" role="dialog" aria-label="Chat">
        <div class="aslan-header">
          <div class="aslan-header-brand">
            <img class="aslan-header-symbol" src="<?php echo $header_icon; ?>" width="36" height="36" alt="" style="width:36px;height:36px;max-width:36px;max-height:36px;" />
            <div>
              <strong>Iris ID Assistant</strong><br />
              <span><?php echo $subtitle; ?></span>
            </div>
          </div>
          <div class="aslan-header-actions">
            <button type="button" class="aslan-partner-badge" id="aslanPartnerBadge" aria-label="Partner login">
              <span class="picon-dot" aria-hidden="true"></span><span>Partner</span>
            </button>
            <button type="button" class="aslan-close" id="aslanClose" aria-label="Close chat">×</button>
          </div>
        </div>
        <div class="aslan-messages" id="aslanMessages"></div>
        <div class="aslan-footer">
          <div class="chips" id="aslanChips"></div>
          <div class="row">
            <textarea class="aslan-input" id="aslanInput" rows="1" placeholder="Message…"></textarea>
            <button type="button" class="aslan-mic" id="aslanMic" aria-label="Voice input" aria-pressed="false" title="Voice input (browser)">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
                <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
              </svg>
            </button>
            <button type="button" class="aslan-send" id="aslanSend">Send</button>
          </div>
        </div>
      </div>

      <div class="aslan-toast" id="aslanToast"></div>
      <div class="aslan-image-lightbox" id="aslanImageLightbox" aria-hidden="true">
        <button type="button" class="aslan-image-lightbox-close" id="aslanImageLightboxClose" aria-label="Close image preview">×</button>
        <img id="aslanImageLightboxImg" alt="Expanded chat image" />
      </div>
    </div>
    <script id="aslan-widget-config">
      window.ASLAN_WIDGET = <?php echo wp_json_encode(aslan_widget_config()); ?>;
    </script>
    <script
      src="<?php echo esc_url(ASLAN_WIDGET_URL . 'assets/aslan-widget.js'); ?>?ver=<?php echo esc_attr(ASLAN_WIDGET_VERSION); ?>"
      defer
    ></script>
    <?php
}
add_action('wp_footer', 'aslan_widget_render_markup', 999);

/**
 * Optional shortcode — add [aslan_chat] anywhere to force-load the widget on that page.
 */
function aslan_widget_shortcode(): string
{
    return '<!-- ASLAN chat widget loaded via wp_footer -->';
}
add_shortcode('aslan_chat', 'aslan_widget_shortcode');

/**
 * Settings page.
 */
function aslan_widget_register_settings(): void
{
    register_setting('aslan_widget_settings', 'aslan_widget_api_base_url', [
        'type' => 'string',
        'sanitize_callback' => 'esc_url_raw',
        'default' => '',
    ]);
    register_setting('aslan_widget_settings', 'aslan_widget_page_slugs', [
        'type' => 'string',
        'sanitize_callback' => 'sanitize_text_field',
        'default' => 'chat-widget-test',
    ]);
    register_setting('aslan_widget_settings', 'aslan_widget_subtitle', [
        'type' => 'string',
        'sanitize_callback' => 'sanitize_text_field',
        'default' => 'IRIS ID · ASLAN',
    ]);
    register_setting('aslan_widget_settings', 'aslan_widget_transcript_recipient', [
        'type' => 'string',
        'sanitize_callback' => 'sanitize_email',
        'default' => 'hoyong.lee@irisid.com',
    ]);
    register_setting('aslan_widget_settings', 'aslan_widget_api_internal_url', [
        'type' => 'string',
        'sanitize_callback' => 'esc_url_raw',
        'default' => 'http://127.0.0.1:8010',
    ]);
    register_setting('aslan_widget_settings', 'aslan_widget_use_wp_proxy', [
        'type' => 'string',
        'sanitize_callback' => static function ($value) {
            return $value === '1' ? '1' : '0';
        },
        'default' => '1',
    ]);
}
add_action('admin_init', 'aslan_widget_register_settings');

function aslan_widget_settings_page(): void
{
    ?>
    <div class="wrap">
      <h1>ASLAN Chat Widget</h1>
      <form method="post" action="options.php">
        <?php settings_fields('aslan_widget_settings'); ?>
        <table class="form-table" role="presentation">
          <tr>
            <th scope="row"><label for="aslan_widget_api_base_url">API base URL</label></th>
            <td>
              <input type="url" class="regular-text" id="aslan_widget_api_base_url"
                     name="aslan_widget_api_base_url"
                     value="<?php echo esc_attr(aslan_widget_get_option('api_base_url')); ?>"
                     placeholder="https://chat-api.irisid.com" />
              <p class="description">Used when WP proxy is off. With proxy on, the browser calls <code>/wp-json/aslan/v1</code> instead.</p>
            </td>
          </tr>
          <tr>
            <th scope="row"><label for="aslan_widget_use_wp_proxy">Route via WordPress</label></th>
            <td>
              <input type="hidden" name="aslan_widget_use_wp_proxy" value="0" />
              <label>
                <input type="checkbox" id="aslan_widget_use_wp_proxy" name="aslan_widget_use_wp_proxy" value="1"
                  <?php checked(aslan_widget_get_option('use_wp_proxy'), '1'); ?> />
                Proxy API through this WordPress site (recommended on same VPS)
              </label>
              <p class="description">WordPress PHP forwards to the internal FastAPI URL below. Fixes subdomain nginx proxy issues.</p>
            </td>
          </tr>
          <tr>
            <th scope="row"><label for="aslan_widget_api_internal_url">Internal API URL</label></th>
            <td>
              <input type="url" class="regular-text" id="aslan_widget_api_internal_url"
                     name="aslan_widget_api_internal_url"
                     value="<?php echo esc_attr(aslan_widget_get_option('api_internal_url')); ?>"
                     placeholder="http://127.0.0.1:8010" />
              <p class="description">Server-side only (not sent to browsers). Default: <code>http://127.0.0.1:8010</code></p>
            </td>
          </tr>
          <tr>
            <th scope="row"><label for="aslan_widget_page_slugs">Auto-load page slugs</label></th>
            <td>
              <input type="text" class="regular-text" id="aslan_widget_page_slugs"
                     name="aslan_widget_page_slugs"
                     value="<?php echo esc_attr(aslan_widget_get_option('page_slugs')); ?>"
                     placeholder="chat-widget-test" />
              <p class="description">Comma-separated WordPress page slugs. Example: duplicate Home → slug <code>chat-widget-test</code>.</p>
            </td>
          </tr>
          <tr>
            <th scope="row"><label for="aslan_widget_subtitle">Header subtitle</label></th>
            <td>
              <input type="text" class="regular-text" id="aslan_widget_subtitle"
                     name="aslan_widget_subtitle"
                     value="<?php echo esc_attr(aslan_widget_get_option('subtitle')); ?>" />
            </td>
          </tr>
          <tr>
            <th scope="row"><label for="aslan_widget_transcript_recipient">Transcript email</label></th>
            <td>
              <input type="email" class="regular-text" id="aslan_widget_transcript_recipient"
                     name="aslan_widget_transcript_recipient"
                     value="<?php echo esc_attr(aslan_widget_get_option('transcript_recipient')); ?>" />
            </td>
          </tr>
        </table>
        <?php submit_button(); ?>
      </form>
    </div>
    <?php
}

function aslan_widget_add_settings_menu(): void
{
    add_options_page(
        'ASLAN Chat Widget',
        'ASLAN Chat',
        'manage_options',
        'aslan-chat-widget',
        'aslan_widget_settings_page'
    );
}
add_action('admin_menu', 'aslan_widget_add_settings_menu');
