/**
 * VLog Embed Player Module
 * Minimal video player for iframe embedding
 *
 * Used by: embed.html
 * Dependencies: utils.js, player-controls.js, shaka-player, hls.js
 */

// Debug logging - only enabled on localhost
const DEBUG_MODE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
function debugLog(...args) {
    if (DEBUG_MODE) console.log('[embed]', ...args);
}

// Slug validation pattern
const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

// Default minimum playback seconds before counting a view (matches backend EMBED_MIN_PLAYBACK_FOR_VIEW)
const DEFAULT_MIN_PLAYBACK_FOR_VIEW_SECONDS = 5;

// Network timeout defaults (in milliseconds)
const FETCH_TIMEOUT_SESSION = 5000;
const FETCH_TIMEOUT_HEARTBEAT = 5000;
const FETCH_TIMEOUT_VIDEO_DATA = 10000;

// Retry configuration
const MAX_RETRIES = 3;
const RETRY_BASE_DELAY = 1000;

// Heartbeat circuit breaker configuration
const MAX_HEARTBEAT_FAILURES = 3;
const HEARTBEAT_INTERVAL_MS = 30000;
const MAX_HEARTBEAT_BACKOFF_MS = 300000; // 5 minutes

/**
 * Fetch with timeout support
 * @param {string} url - URL to fetch
 * @param {object} options - Fetch options
 * @param {number} timeout - Timeout in milliseconds
 * @returns {Promise<Response>}
 */
async function fetchWithTimeout(url, options = {}, timeout = 10000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal
        });
        clearTimeout(timeoutId);
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === 'AbortError') {
            throw new Error('Request timeout');
        }
        throw error;
    }
}

/**
 * Generate a cryptographically secure UUID
 * @returns {string} UUID v4
 */
function generateSecureUUID() {
    // Use native crypto.randomUUID if available (modern browsers)
    if (crypto && crypto.randomUUID) {
        return crypto.randomUUID();
    }

    // Fallback using crypto.getRandomValues
    if (crypto && crypto.getRandomValues) {
        const bytes = new Uint8Array(16);
        crypto.getRandomValues(bytes);
        // Set version (4) and variant (8, 9, A, or B)
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;

        const hex = [...bytes].map(b => b.toString(16).padStart(2, '0'));
        return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`;
    }

    // Last resort fallback (not cryptographically secure, but functional)
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

/**
 * Embed analytics tracker
 * Tracks view sessions with source='embed' and minimum playback validation
 */
class EmbedAnalytics {
    constructor(videoId, player, minPlaybackSeconds = DEFAULT_MIN_PLAYBACK_FOR_VIEW_SECONDS) {
        this.videoId = videoId;
        this.player = player;
        this.minPlaybackSeconds = minPlaybackSeconds;
        this.sessionToken = null;
        this.sessionUUID = generateSecureUUID();
        this.heartbeatInterval = null;
        this.playbackSeconds = 0;
        this.viewCounted = false;
        this.playbackTimer = null;

        // Session state tracking
        this.sessionStarting = false;
        this.destroyed = false;

        // Circuit breaker for heartbeats
        this.heartbeatFailures = 0;
        this.heartbeatBackoff = HEARTBEAT_INTERVAL_MS;
    }

    async startSession(quality) {
        // Prevent duplicate session starts
        if (this.sessionToken || this.sessionStarting || this.destroyed) return;

        this.sessionStarting = true;

        try {
            const res = await fetchWithTimeout('/api/analytics/session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    video_id: this.videoId,
                    quality: quality,
                    source: 'embed',
                    session_uuid: this.sessionUUID
                })
            }, FETCH_TIMEOUT_SESSION);

            if (!res.ok) {
                throw new Error(`Session creation failed: ${res.status}`);
            }

            const data = await res.json();
            this.sessionToken = data.session_token;
            this.startHeartbeat();
            this.startPlaybackTracking();
            debugLog('Analytics session started');
        } catch (e) {
            console.error('Failed to start analytics session:', e);
            // Don't start heartbeat/tracking if session creation failed
        } finally {
            this.sessionStarting = false;
        }
    }

    startHeartbeat() {
        if (this.destroyed) return;
        this.heartbeatInterval = setInterval(() => this.sendHeartbeat(), this.heartbeatBackoff);
    }

    startPlaybackTracking() {
        if (this.destroyed) return;
        // Track actual playback time (not just video time)
        this.playbackTimer = setInterval(() => {
            if (this.destroyed) return;
            if (!this.player.paused()) {
                this.playbackSeconds++;

                // Count view after minimum playback threshold
                if (!this.viewCounted && this.playbackSeconds >= this.minPlaybackSeconds) {
                    this.viewCounted = true;
                    debugLog('View counted after', this.minPlaybackSeconds, 'seconds');
                }
            }
        }, 1000);
    }

    async sendHeartbeat() {
        if (!this.sessionToken || this.destroyed) return;

        // Circuit breaker: stop if too many failures
        if (this.heartbeatFailures >= MAX_HEARTBEAT_FAILURES) {
            debugLog('Heartbeat circuit breaker open, stopping heartbeats');
            this.clearHeartbeatInterval();
            return;
        }

        try {
            const res = await fetchWithTimeout('/api/analytics/heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    session_token: this.sessionToken,
                    position: this.player.currentTime(),
                    quality: this.getCurrentQuality(),
                    playing: !this.player.paused(),
                    session_uuid: this.sessionUUID
                })
            }, FETCH_TIMEOUT_HEARTBEAT);

            if (!res.ok) {
                throw new Error(`Heartbeat failed: ${res.status}`);
            }

            // Reset failure count and backoff on success
            this.heartbeatFailures = 0;
            if (this.heartbeatBackoff !== HEARTBEAT_INTERVAL_MS) {
                this.heartbeatBackoff = HEARTBEAT_INTERVAL_MS;
                this.restartHeartbeatWithBackoff();
            }
        } catch (e) {
            console.error('Heartbeat failed:', e);
            this.heartbeatFailures++;

            // Exponential backoff for retries
            if (this.heartbeatFailures < MAX_HEARTBEAT_FAILURES) {
                this.heartbeatBackoff = Math.min(
                    this.heartbeatBackoff * 2,
                    MAX_HEARTBEAT_BACKOFF_MS
                );
                this.restartHeartbeatWithBackoff();
                debugLog(`Heartbeat backoff increased to ${this.heartbeatBackoff}ms`);
            }
        }
    }

    restartHeartbeatWithBackoff() {
        this.clearHeartbeatInterval();
        if (!this.destroyed) {
            this.heartbeatInterval = setInterval(() => this.sendHeartbeat(), this.heartbeatBackoff);
        }
    }

    clearHeartbeatInterval() {
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }
    }

    clearPlaybackTimer() {
        if (this.playbackTimer) {
            clearInterval(this.playbackTimer);
            this.playbackTimer = null;
        }
    }

    getCurrentQuality() {
        const qualityLevels = this.player.qualityLevels?.();
        if (qualityLevels && Array.isArray(qualityLevels)) {
            const activeTrack = qualityLevels.find(t => t.active);
            if (activeTrack) {
                return activeTrack.height + 'p';
            }
        }
        return null;
    }

    async endSession(completed = false) {
        // Clear intervals first (idempotent)
        this.clearHeartbeatInterval();
        this.clearPlaybackTimer();

        if (!this.sessionToken) return;

        const token = this.sessionToken;
        this.sessionToken = null; // Clear immediately to prevent double-send

        try {
            const data = JSON.stringify({
                session_token: token,
                position: this.player.currentTime(),
                completed: completed
            });

            // Always prefer sendBeacon for reliability on unload
            if (navigator.sendBeacon) {
                navigator.sendBeacon('/api/analytics/end', new Blob([data], { type: 'application/json' }));
            } else {
                await fetchWithTimeout('/api/analytics/end', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: data,
                    keepalive: true // Important for unload scenarios
                }, 3000);
            }
        } catch (e) {
            console.error('Failed to end session:', e);
        }
    }

    destroy() {
        this.destroyed = true;
        this.clearHeartbeatInterval();
        this.clearPlaybackTimer();
        this.sessionToken = null;
    }
}

/**
 * Embed player initialization
 */
class EmbedPlayer {
    constructor() {
        this.video = null;
        this.videoData = null;
        this.playerControls = null;
        this.analytics = null;
        this.hls = null;
        this.shakaPlayer = null;
        this.destroyed = false;

        // Parse embed configuration from URL
        this.config = window.EMBED_CONFIG || {
            slug: window.location.pathname.split('/').pop(),
            params: new URLSearchParams(window.location.search)
        };

        // Parse query parameters
        this.autoplay = this.parseBoolean(this.config.params.get('autoplay'), false);
        this.startTime = this.parseNumber(this.config.params.get('start'), 0);
        this.showControls = this.parseBoolean(this.config.params.get('controls'), true);

        debugLog('Embed config:', {
            slug: this.config.slug,
            autoplay: this.autoplay,
            startTime: this.startTime,
            showControls: this.showControls
        });
    }

    parseBoolean(value, defaultValue) {
        if (value === null || value === undefined) return defaultValue;
        return value === '1' || value === 'true';
    }

    parseNumber(value, defaultValue) {
        if (value === null || value === undefined) return defaultValue;
        const num = parseInt(value, 10);
        return isNaN(num) || num < 0 ? defaultValue : num;
    }

    showError(message = 'Video unavailable') {
        document.getElementById('embed-loading').style.display = 'none';
        document.getElementById('embed-player-container').style.display = 'none';

        const errorEl = document.getElementById('embed-error');
        const errorMsg = document.getElementById('embed-error-message');
        errorMsg.textContent = message;
        errorEl.style.display = 'flex';
    }

    showPlayer() {
        document.getElementById('embed-loading').style.display = 'none';
        document.getElementById('embed-error').style.display = 'none';
        document.getElementById('embed-player-container').style.display = 'block';
    }

    async init() {
        const slug = this.config.slug;

        // Validate slug
        if (!slug || !SLUG_PATTERN.test(slug)) {
            this.showError('Video not found');
            return;
        }

        // Retry with exponential backoff
        for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
            try {
                const res = await fetchWithTimeout(
                    `/api/videos/${encodeURIComponent(slug)}`,
                    {},
                    FETCH_TIMEOUT_VIDEO_DATA
                );

                if (res.status === 404) {
                    // Don't retry 404s
                    this.showError('Video not found');
                    return;
                }

                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}`);
                }

                this.videoData = await res.json();

                // Validate required fields
                if (!this.videoData.status) {
                    throw new Error('Invalid video data');
                }

                // Check video is ready
                if (this.videoData.status !== 'ready') {
                    this.showError('Video is being prepared');
                    return;
                }

                // Validate stream URLs exist
                if (!this.videoData.stream_url && !this.videoData.dash_url) {
                    this.showError('Video files unavailable');
                    return;
                }

                // Initialize player
                this.showPlayer();
                this.initPlayer();
                return; // Success

            } catch (e) {
                console.error(`Failed to load video (attempt ${attempt + 1}/${MAX_RETRIES}):`, e);

                if (attempt === MAX_RETRIES - 1) {
                    // Last attempt failed
                    if (e.message === 'Request timeout') {
                        this.showError('Connection timeout - please try again');
                    } else {
                        this.showError('Unable to load video');
                    }
                } else {
                    // Wait before retry with exponential backoff + jitter
                    const delay = RETRY_BASE_DELAY * Math.pow(2, attempt) + Math.random() * 1000;
                    await new Promise(resolve => setTimeout(resolve, delay));
                }
            }
        }
    }

    initPlayer() {
        this.video = document.getElementById('player');
        const container = document.getElementById('player-container');
        const streamUrl = this.videoData.stream_url;
        const dashUrl = this.videoData.dash_url;
        const streamingFormat = this.videoData.streaming_format || 'hls_ts';

        debugLog('Initializing player, format:', streamingFormat);

        // Set poster image
        if (this.videoData.thumbnail_url) {
            this.video.poster = this.videoData.thumbnail_url;
        }

        // Use Shaka Player for CMAF/DASH content
        if (typeof shaka !== 'undefined' && dashUrl && streamingFormat === 'cmaf') {
            this.initShakaPlayer(dashUrl, streamUrl);
        } else if (typeof Hls !== 'undefined' && Hls.isSupported()) {
            this.initHlsPlayer(streamUrl);
        } else if (this.video.canPlayType('application/vnd.apple.mpegurl')) {
            // Safari native HLS
            debugLog('Using native HLS support');
            this.video.src = streamUrl;
        } else {
            this.showError('Playback not supported');
            return;
        }

        // Initialize custom controls or keep native
        if (this.showControls) {
            try {
                if (window.VLogPlayerControls) {
                    this.video.removeAttribute('controls');
                    this.playerControls = new VLogPlayerControls(container, this.video, {
                        onQualityChange: (index) => this.changeQuality(index),
                        // Disable share button in embed mode
                        disableShare: true
                    });

                    // Set chapters if available
                    if (this.videoData.chapters && this.videoData.chapters.length > 0) {
                        this.playerControls.setChapters(this.videoData.chapters);
                    }

                    // Set sprite sheet info
                    if (this.videoData.sprite_sheet_info) {
                        this.playerControls.setSpriteSheetInfo(this.videoData.sprite_sheet_info);
                    }
                }
            } catch (e) {
                console.error('Failed to init custom controls:', e);
                this.video.controls = true;
            }
        } else {
            // Hide controls if disabled
            this.video.controls = false;
        }

        // Initialize analytics
        this.analytics = new EmbedAnalytics(this.videoData.id, {
            currentTime: () => this.video.currentTime,
            paused: () => this.video.paused,
            qualityLevels: () => {
                if (this.shakaPlayer) {
                    return this.shakaPlayer.getVariantTracks();
                }
                return this.hls ? this.hls.levels : null;
            }
        });

        // Start session on first play
        this.video.addEventListener('play', () => {
            let quality = 'auto';
            if (this.shakaPlayer) {
                const tracks = this.shakaPlayer.getVariantTracks();
                const active = tracks.find(t => t.active);
                if (active) quality = active.height + 'p';
            } else if (this.hls?.levels?.[this.hls.currentLevel]) {
                quality = this.hls.levels[this.hls.currentLevel].height + 'p';
            }
            this.analytics.startSession(quality);
        }, { once: true });

        // End session on video complete
        this.video.addEventListener('ended', () => {
            this.analytics.endSession(true);
        });

        // Handle start time
        if (this.startTime > 0) {
            const seekToStart = () => {
                if (this.video.duration && this.startTime < this.video.duration) {
                    this.video.currentTime = this.startTime;
                    debugLog('Seeking to start time:', this.startTime);
                }
            };

            if (this.video.readyState >= 1) {
                seekToStart();
            } else {
                this.video.addEventListener('loadedmetadata', seekToStart, { once: true });
            }
        }

        // Handle autoplay
        if (this.autoplay) {
            this.video.muted = true; // Required for autoplay without user interaction
            this.video.play().catch(e => {
                debugLog('Autoplay failed:', e);
            });
        }
    }

    initShakaPlayer(dashUrl, hlsUrl) {
        shaka.polyfill.installAll();

        if (!shaka.Player.isBrowserSupported()) {
            debugLog('Shaka not supported, falling back to HLS');
            this.initHlsPlayer(hlsUrl);
            return;
        }

        this.shakaPlayer = new shaka.Player(this.video);

        this.shakaPlayer.addEventListener('error', (event) => {
            console.error('Shaka error:', event.detail);
            // Fallback to HLS on error
            this.shakaPlayer.destroy();
            this.shakaPlayer = null;
            this.initHlsPlayer(hlsUrl);
        });

        this.shakaPlayer.load(dashUrl).then(() => {
            debugLog('Shaka player loaded');
            this.updateQualityLevels();
        }).catch((error) => {
            console.error('Shaka load failed:', error);
            this.shakaPlayer.destroy();
            this.shakaPlayer = null;
            this.initHlsPlayer(hlsUrl);
        });
    }

    initHlsPlayer(streamUrl) {
        this.hls = new Hls({
            enableWorker: true,
            lowLatencyMode: false
        });

        this.hls.loadSource(streamUrl);
        this.hls.attachMedia(this.video);

        this.hls.on(Hls.Events.MANIFEST_PARSED, () => {
            debugLog('HLS manifest parsed');
            this.updateQualityLevels();
        });

        this.hls.on(Hls.Events.ERROR, (event, data) => {
            if (data.fatal) {
                console.error('HLS fatal error:', data.type, data.details);
                this.showError('Playback error');
            }
        });
    }

    updateQualityLevels() {
        if (!this.playerControls) return;

        let levels = [];

        if (this.shakaPlayer) {
            levels = this.shakaPlayer.getVariantTracks().map(track => ({
                height: track.height,
                width: track.width,
                bitrate: track.bandwidth
            }));
        } else if (this.hls) {
            levels = this.hls.levels.map(level => ({
                height: level.height,
                width: level.width,
                bitrate: level.bitrate
            }));
        }

        // Sort by height descending
        levels.sort((a, b) => b.height - a.height);

        // Add auto option
        const qualityOptions = [
            { label: 'Auto', value: -1 },
            ...levels.map((level, index) => ({
                label: level.height + 'p',
                value: index
            }))
        ];

        this.playerControls.setQualityLevels(qualityOptions);
    }

    changeQuality(index) {
        if (index === -1) {
            // Auto
            if (this.shakaPlayer) {
                this.shakaPlayer.configure({ abr: { enabled: true } });
            } else if (this.hls) {
                this.hls.currentLevel = -1;
            }
        } else {
            if (this.shakaPlayer) {
                this.shakaPlayer.configure({ abr: { enabled: false } });
                const tracks = this.shakaPlayer.getVariantTracks();
                if (tracks[index]) {
                    this.shakaPlayer.selectVariantTrack(tracks[index], true);
                }
            } else if (this.hls) {
                this.hls.currentLevel = index;
            }
        }
    }

    /**
     * Clean up all resources
     * Called on page unload or when embed is removed from DOM
     */
    destroy() {
        if (this.destroyed) return;
        this.destroyed = true;

        debugLog('Destroying embed player');

        // Clean up analytics
        if (this.analytics) {
            this.analytics.endSession(false);
            this.analytics.destroy();
            this.analytics = null;
        }

        // Clean up Shaka Player
        if (this.shakaPlayer) {
            try {
                this.shakaPlayer.destroy();
            } catch (e) {
                console.error('Failed to destroy Shaka player:', e);
            }
            this.shakaPlayer = null;
        }

        // Clean up HLS.js
        if (this.hls) {
            try {
                this.hls.destroy();
            } catch (e) {
                console.error('Failed to destroy HLS player:', e);
            }
            this.hls = null;
        }

        // Clean up custom controls
        if (this.playerControls && this.playerControls.destroy) {
            try {
                this.playerControls.destroy();
            } catch (e) {
                console.error('Failed to destroy player controls:', e);
            }
            this.playerControls = null;
        }

        // Clean up video element
        if (this.video) {
            this.video.pause();
            this.video.src = '';
            this.video.load();
            this.video = null;
        }
    }
}

// Global reference for cleanup
let embedPlayerInstance = null;

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    embedPlayerInstance = new EmbedPlayer();
    embedPlayerInstance.init();

    // Expose for external cleanup (e.g., parent page removing iframe)
    window.embedPlayerInstance = embedPlayerInstance;
});

// Clean up on page unload
window.addEventListener('beforeunload', () => {
    if (embedPlayerInstance) {
        embedPlayerInstance.destroy();
    }
});

// Also handle pagehide for mobile browsers
window.addEventListener('pagehide', () => {
    if (embedPlayerInstance) {
        embedPlayerInstance.destroy();
    }
});
