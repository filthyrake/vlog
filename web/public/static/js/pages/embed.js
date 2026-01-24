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

/**
 * Embed analytics tracker
 * Tracks view sessions with source='embed' and minimum playback validation
 */
class EmbedAnalytics {
    constructor(videoId, player, minPlaybackSeconds = 5) {
        this.videoId = videoId;
        this.player = player;
        this.minPlaybackSeconds = minPlaybackSeconds;
        this.sessionToken = null;
        this.sessionUUID = this.generateUUID();
        this.heartbeatInterval = null;
        this.playbackSeconds = 0;
        this.viewCounted = false;
        this.playbackTimer = null;
    }

    generateUUID() {
        // Generate a simple UUID for session tracking
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            const r = Math.random() * 16 | 0;
            const v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    async startSession(quality) {
        try {
            const res = await fetch('/api/analytics/session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    video_id: this.videoId,
                    quality: quality,
                    source: 'embed',
                    session_uuid: this.sessionUUID
                })
            });
            const data = await res.json();
            this.sessionToken = data.session_token;
            this.startHeartbeat();
            this.startPlaybackTracking();
        } catch (e) {
            console.error('Failed to start analytics session:', e);
        }
    }

    startHeartbeat() {
        this.heartbeatInterval = setInterval(() => this.sendHeartbeat(), 30000);
    }

    startPlaybackTracking() {
        // Track actual playback time (not just video time)
        this.playbackTimer = setInterval(() => {
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
        if (!this.sessionToken) return;

        try {
            await fetch('/api/analytics/heartbeat', {
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
            });
        } catch (e) {
            console.error('Heartbeat failed:', e);
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
        if (!this.sessionToken) return;

        clearInterval(this.heartbeatInterval);
        clearInterval(this.playbackTimer);

        try {
            const data = JSON.stringify({
                session_token: this.sessionToken,
                position: this.player.currentTime(),
                completed: completed
            });

            if (navigator.sendBeacon) {
                navigator.sendBeacon('/api/analytics/end', new Blob([data], { type: 'application/json' }));
            } else {
                await fetch('/api/analytics/end', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: data
                });
            }
        } catch (e) {
            console.error('Failed to end session:', e);
        }

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

        try {
            // Fetch video data
            const res = await fetch(`/api/videos/${encodeURIComponent(slug)}`);
            if (!res.ok) {
                this.showError('Video not found');
                return;
            }

            this.videoData = await res.json();

            // Check video is ready
            if (this.videoData.status !== 'ready') {
                this.showError('Video is being prepared');
                return;
            }

            // Initialize player
            this.showPlayer();
            this.initPlayer();

        } catch (e) {
            console.error('Failed to load video:', e);
            this.showError('Unable to load video');
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

        // End session on video complete or unload
        this.video.addEventListener('ended', () => {
            this.analytics.endSession(true);
        });

        window.addEventListener('beforeunload', () => {
            this.analytics.endSession(false);
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
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    const embedPlayer = new EmbedPlayer();
    embedPlayer.init();
});
