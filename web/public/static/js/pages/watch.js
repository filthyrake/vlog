/**
 * VLog Watch Page Module
 * Handles video playback, analytics, and player initialization
 *
 * Used by: watch.html
 * Dependencies: utils.js, player-controls.js, shaka-player, hls.js
 */

// Debug logging - only enabled on localhost
const DEBUG_MODE = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
function debugLog(...args) {
    if (DEBUG_MODE) console.log(...args);
}

// Slug validation pattern
const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * Playback analytics tracker
 * Tracks watch sessions and sends heartbeats to the analytics API
 * Coupled to watchPage - not extracted separately due to single-page usage
 */
class PlaybackAnalytics {
    constructor(videoId, player) {
        this.videoId = videoId;
        this.player = player;
        this.sessionToken = null;
        this.heartbeatInterval = null;
    }

    async startSession(quality) {
        try {
            const res = await VLogUtils.fetchWithTimeout('/api/analytics/session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    video_id: this.videoId,
                    quality: quality
                })
            }, 5000);
            const data = await res.json();
            this.sessionToken = data.session_token;
            this.startHeartbeat();
        } catch (e) {
            console.error('Failed to start analytics session:', e);
        }
    }

    startHeartbeat() {
        this.heartbeatInterval = setInterval(() => this.sendHeartbeat(), 30000);
    }

    async sendHeartbeat() {
        if (!this.sessionToken) return;

        try {
            await VLogUtils.fetchWithTimeout('/api/analytics/heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    session_token: this.sessionToken,
                    position: this.player.currentTime(),
                    quality: this.getCurrentQuality(),
                    playing: !this.player.paused()
                })
            }, 5000);
        } catch (e) {
            console.error('Heartbeat failed:', e);
        }
    }

    getCurrentQuality() {
        // Get quality from current player
        const qualityLevels = this.player.qualityLevels?.();
        if (qualityLevels) {
            // Shaka Player: tracks have .active property
            const activeTrack = Array.isArray(qualityLevels)
                ? qualityLevels.find(t => t.active)
                : null;
            if (activeTrack) {
                return activeTrack.height + 'p';
            }
            // HLS.js: check enabled property
            for (let i = 0; i < qualityLevels.length; i++) {
                if (qualityLevels[i].enabled) {
                    return qualityLevels[i].height + 'p';
                }
            }
        }
        return null;
    }

    async endSession(completed = false) {
        if (!this.sessionToken) return;

        clearInterval(this.heartbeatInterval);

        try {
            // Use sendBeacon for reliability on page unload
            const data = JSON.stringify({
                session_token: this.sessionToken,
                position: this.player.currentTime(),
                completed: completed
            });

            if (navigator.sendBeacon) {
                navigator.sendBeacon('/api/analytics/end', new Blob([data], { type: 'application/json' }));
            } else {
                await VLogUtils.fetchWithTimeout('/api/analytics/end', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: data
                }, 5000);
            }
        } catch (e) {
            console.error('Failed to end session:', e);
        }

        this.sessionToken = null;
    }
}

/**
 * Watch page Alpine.js component
 * Manages video playback state and player initialization
 */
function watchPage() {
    return {
        video: null,
        loading: true,
        error: null,
        player: null,
        playerControls: null,
        analytics: null,
        hls: null,
        shakaPlayer: null,
        hlsLevels: [],
        captionsEnabled: false,
        captionsTrack: null,
        watermark: null,
        mobileNavOpen: false,
        previousFocus: null,
        searchQuery: '',

        navigateToSearch() {
            const query = this.searchQuery?.trim();
            if (query) {
                window.location.href = '/?search=' + encodeURIComponent(query);
            }
        },

        openMobileNav() {
            this.previousFocus = document.activeElement;
            this.mobileNavOpen = true;
            document.body.style.overflow = 'hidden';
            this.$nextTick(() => {
                this.$refs.closeBtn?.focus();
            });
        },

        closeMobileNav() {
            this.mobileNavOpen = false;
            document.body.style.overflow = '';
            this.$nextTick(() => {
                if (this.previousFocus) {
                    this.previousFocus.focus();
                    this.previousFocus = null;
                }
            });
        },

        async init() {
            // Fetch watermark config (non-blocking)
            this.loadWatermarkConfig();

            const slug = window.location.pathname.split('/').pop();
            if (!slug || !SLUG_PATTERN.test(slug)) {
                this.error = 'Video not found';
                this.loading = false;
                return;
            }

            try {
                const res = await VLogUtils.fetchWithTimeout(`/api/videos/${encodeURIComponent(slug)}`, {}, 10000);
                if (!res.ok) {
                    throw new Error('Video not found');
                }
                this.video = await res.json();

                if (this.video.status !== 'ready') {
                    this.error = 'Video is still processing...';
                    this.loading = false;
                    return;
                }

                document.title = `${this.video.title} - Damen's VLog`;
                this.loading = false;

                // Initialize player after DOM update
                this.$nextTick(() => {
                    this.initPlayer();
                });
            } catch (e) {
                this.error = e.message;
                this.loading = false;
            }
        },

        async loadWatermarkConfig() {
            try {
                const res = await VLogUtils.fetchWithTimeout('/api/config/watermark', {}, 5000);
                if (res.ok) {
                    this.watermark = await res.json();
                }
            } catch (e) {
                // Watermark is optional, don't show errors
                debugLog('Failed to load watermark config:', e);
            }
        },

        initPlayer() {
            const self = this;
            const video = document.getElementById('player');
            const container = document.getElementById('player-container');
            const streamUrl = this.video.stream_url;
            const dashUrl = this.video.dash_url;
            const streamingFormat = this.video.streaming_format || 'hls_ts';

            debugLog('Initializing player, format:', streamingFormat);
            debugLog('Stream URL:', streamUrl);
            debugLog('DASH URL:', dashUrl);

            // Use Shaka Player for CMAF/DASH content (preferred)
            if (typeof shaka !== 'undefined' && dashUrl && streamingFormat === 'cmaf') {
                this.initShakaPlayer(video, dashUrl, streamUrl);
            } else if (typeof Hls !== 'undefined' && Hls.isSupported()) {
                // Fallback to HLS.js for legacy HLS/TS content
                this.initHlsPlayer(video, streamUrl);
            } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                // Safari has native HLS support
                debugLog('Using native HLS support');
                video.src = streamUrl;
                this.player = video;
            } else {
                this.error = 'Video playback not supported in this browser';
                return;
            }

            // Initialize custom player controls with fallback to native controls
            try {
                if (window.VLogPlayerControls) {
                    // Remove native controls as custom controls are available
                    video.removeAttribute('controls');
                    this.playerControls = new VLogPlayerControls(container, video, {
                        onQualityChange: (index) => this.changeQuality(index),
                        onCaptionsToggle: () => this.toggleCaptions()
                    });

                    // Set captions availability
                    if (this.video.captions_url) {
                        this.playerControls.setCaptionsAvailable(true);
                    }
                } else {
                    // Fallback: keep native controls if custom controls are unavailable
                    console.warn('Custom player controls not available, using native controls');
                    this.playerControls = null;
                }
            } catch (e) {
                // Fallback: re-enable native controls if custom controls initialization fails
                console.error('Failed to initialize custom player controls:', e);
                video.controls = true;
                this.playerControls = null;
            }

            // Initialize analytics
            this.analytics = new PlaybackAnalytics(this.video.id, {
                currentTime: () => video.currentTime,
                paused: () => video.paused,
                qualityLevels: () => {
                    if (self.shakaPlayer) {
                        return self.shakaPlayer.getVariantTracks();
                    }
                    return self.hls ? self.hls.levels : null;
                }
            });

            // Start session on first play (use once: true for one-time listener)
            video.addEventListener('play', () => {
                let quality = 'auto';
                if (self.shakaPlayer) {
                    const tracks = self.shakaPlayer.getVariantTracks();
                    const active = tracks.find(t => t.active);
                    if (active) quality = active.height + 'p';
                } else if (self.hls?.levels?.[self.hls.currentLevel]) {
                    quality = self.hls.levels[self.hls.currentLevel].height + 'p';
                }
                self.analytics.startSession(quality);
            }, { once: true });

            // End session on video complete
            video.addEventListener('ended', () => {
                this.analytics.endSession(true);
            });

            // Handle page unload
            window.addEventListener('beforeunload', () => {
                this.analytics.endSession(false);
                if (this.playerControls) {
                    this.playerControls.destroy();
                }
                if (this.shakaPlayer) {
                    this.shakaPlayer.destroy();
                }
                if (this.hls) {
                    this.hls.destroy();
                }
            });

            // Send heartbeat when tab becomes hidden
            document.addEventListener('visibilitychange', () => {
                if (document.hidden && this.analytics) {
                    this.analytics.sendHeartbeat();
                }
            });

            // Add captions track if available
            if (this.video.captions_url) {
                this.addCaptionsTrack(video, this.video.captions_url);
            }
        },

        initShakaPlayer(videoElement, dashUrl, hlsFallbackUrl) {
            const self = this;
            debugLog('Initializing Shaka Player for DASH/CMAF');

            // Install polyfills
            shaka.polyfill.installAll();

            // Check if browser supports Shaka
            if (!shaka.Player.isBrowserSupported()) {
                console.warn('Shaka not supported, falling back to HLS.js');
                if (hlsFallbackUrl) {
                    this.initHlsPlayer(videoElement, hlsFallbackUrl);
                } else {
                    console.error('No HLS fallback URL available and Shaka not supported');
                    this.error = 'Your browser does not support this video format';
                }
                return;
            }

            const player = new shaka.Player();
            player.attach(videoElement);
            this.shakaPlayer = player;
            this.player = videoElement;

            // Configure player with codec preferences
            player.configure({
                preferredVideoCodecs: ['hvc1', 'hev1', 'av01', 'avc1'],
                preferredAudioCodecs: ['mp4a', 'ac-3', 'ec-3'],
                abr: {
                    enabled: true,
                    defaultBandwidthEstimate: 5000000
                },
                streaming: {
                    bufferingGoal: 30,
                    rebufferingGoal: 2,
                    bufferBehind: 30
                }
            });

            // Error handling
            player.addEventListener('error', (event) => {
                const error = event.detail;
                console.error('Shaka Player error:', error);

                // Try fallback to HLS if DASH fails
                if (hlsFallbackUrl && !self.hls) {
                    console.warn('DASH failed, falling back to HLS');
                    player.destroy();
                    self.shakaPlayer = null;
                    self.initHlsPlayer(videoElement, hlsFallbackUrl);
                } else if (!hlsFallbackUrl) {
                    self.error = 'Video playback error';
                }
            });

            // Track quality levels when manifest is parsed
            player.addEventListener('trackschanged', () => {
                const tracks = player.getVariantTracks();
                debugLog('Shaka tracks available:', tracks.length);

                // Build quality levels from tracks (deduplicate by height)
                const heightMap = new Map();
                tracks.forEach(track => {
                    if (!heightMap.has(track.height) ||
                        track.videoBandwidth > heightMap.get(track.height).videoBandwidth) {
                        heightMap.set(track.height, track);
                    }
                });

                // Sort by height descending
                const qualities = Array.from(heightMap.values())
                    .sort((a, b) => b.height - a.height);

                self.hlsLevels = qualities.map(t => ({
                    height: t.height,
                    width: t.width,
                    bitrate: t.videoBandwidth,
                    id: t.id
                }));

                debugLog('Quality levels:', self.hlsLevels);

                // Update quality options in player controls
                if (self.playerControls && self.hlsLevels.length > 0) {
                    self.playerControls.setQualities(self.hlsLevels);
                }
            });

            // Load the manifest
            player.load(dashUrl).then(() => {
                debugLog('Shaka loaded DASH manifest successfully');
            }).catch((error) => {
                console.error('Shaka load error:', error);
                // Fallback to HLS
                if (hlsFallbackUrl) {
                    console.warn('DASH load failed, falling back to HLS');
                    player.destroy();
                    self.shakaPlayer = null;
                    self.initHlsPlayer(videoElement, hlsFallbackUrl);
                } else {
                    self.error = 'Failed to load video';
                }
            });
        },

        initHlsPlayer(videoElement, streamUrl) {
            const self = this;
            debugLog('Initializing HLS.js player');

            const hlsConfig = {
                debug: DEBUG_MODE,
                enableWorker: true,
                lowLatencyMode: false,
                backBufferLength: 90
            };

            const hls = new Hls(hlsConfig);
            this.hls = hls;
            this.player = videoElement;

            hls.loadSource(streamUrl);
            hls.attachMedia(videoElement);

            // Store quality levels when manifest is parsed
            hls.on(Hls.Events.MANIFEST_PARSED, (event, data) => {
                debugLog('HLS manifest parsed, levels:', data.levels.length);
                // Sort levels by height descending for quality selector
                self.hlsLevels = data.levels
                    .map((level, index) => ({
                        height: level.height,
                        width: level.width,
                        bitrate: level.bitrate,
                        index: index
                    }))
                    .sort((a, b) => b.height - a.height);

                // Update quality options in player controls
                if (self.playerControls && self.hlsLevels.length > 0) {
                    self.playerControls.setQualities(self.hlsLevels);
                }
            });

            // Handle HLS errors
            hls.on(Hls.Events.ERROR, (event, data) => {
                if (data.fatal) {
                    console.error('HLS fatal error:', data.type, data.details);
                    switch (data.type) {
                        case Hls.ErrorTypes.NETWORK_ERROR:
                            hls.startLoad();
                            break;
                        case Hls.ErrorTypes.MEDIA_ERROR:
                            hls.recoverMediaError();
                            break;
                        default:
                            self.error = 'Video playback error';
                            hls.destroy();
                            break;
                    }
                }
            });
        },

        addCaptionsTrack(videoElement, captionsUrl) {
            // Create track element for captions
            const track = document.createElement('track');
            track.kind = 'captions';
            track.label = 'English';
            track.srclang = 'en';
            track.src = captionsUrl;

            videoElement.appendChild(track);
            this.captionsTrack = track;

            // Default to hidden
            track.track.mode = 'hidden';
            this.captionsEnabled = false;

            debugLog('Captions track added:', captionsUrl);
        },

        toggleCaptions() {
            if (!this.captionsTrack) return;

            if (this.captionsEnabled) {
                this.captionsTrack.track.mode = 'hidden';
                this.captionsEnabled = false;
            } else {
                this.captionsTrack.track.mode = 'showing';
                this.captionsEnabled = true;
            }
            // Update player controls button state
            if (this.playerControls) {
                this.playerControls.setCaptionsEnabled(this.captionsEnabled);
            }
            debugLog('Captions:', this.captionsEnabled ? 'on' : 'off');
        },

        formatDuration(seconds) {
            return VLogUtils.formatDuration(seconds);
        },

        formatDate(dateStr) {
            return VLogUtils.formatDate(dateStr, 'long');
        },

        changeQuality(levelIndex) {
            const idx = parseInt(levelIndex, 10);

            // Handle Shaka Player (DASH/CMAF)
            if (this.shakaPlayer) {
                if (idx === -1) {
                    // Auto quality - enable ABR
                    this.shakaPlayer.configure({ abr: { enabled: true } });
                    debugLog('Quality set to auto (ABR enabled)');
                } else {
                    // Manual quality selection - disable ABR and select track
                    const targetLevel = this.hlsLevels[idx];
                    const tracks = this.shakaPlayer.getVariantTracks();
                    const targetTrack = tracks.find(t => t.height === targetLevel.height);

                    if (targetTrack) {
                        this.shakaPlayer.configure({ abr: { enabled: false } });
                        this.shakaPlayer.selectVariantTrack(targetTrack, true);
                        debugLog('Quality set to', targetLevel.height + 'p');
                    }
                }
                return;
            }

            // Handle HLS.js (legacy HLS/TS)
            if (this.hls) {
                if (idx === -1) {
                    // Auto quality - let hls.js decide
                    this.hls.nextLevel = -1;
                    debugLog('Quality set to auto');
                } else {
                    // Find the actual hls.js level index for this height
                    const targetLevel = this.hlsLevels[idx];
                    const hlsIndex = this.hls.levels.findIndex(
                        l => l.height === targetLevel.height
                    );
                    if (hlsIndex !== -1) {
                        // Use nextLevel for smooth switching
                        this.hls.nextLevel = hlsIndex;
                        debugLog('Quality set to', targetLevel.height + 'p');
                    }
                }
            }
        }
    };
}

// CRITICAL: Register with Alpine.js using alpine:init event
// This ensures the component is registered before Alpine parses the DOM
document.addEventListener('alpine:init', () => {
    Alpine.data('watchPage', watchPage);
});

// Export for testing (optional)
window.VLogWatchPage = { PlaybackAnalytics, watchPage };
