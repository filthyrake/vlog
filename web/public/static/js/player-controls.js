/**
 * VLog Custom Video Player Controls
 * Touch-optimized controls with gesture support for mobile devices
 */

class VLogPlayerControls {
    constructor(container, video, options = {}) {
        this.container = container;
        this.video = video;
        this.options = {
            skipSeconds: 10,
            hideControlsDelay: 5000, // Increased from 3000ms to 5000ms for better usability
            doubleTapDelay: 300,
            swipeThreshold: 30,
            brightnessMin: 0.5,
            brightnessMax: 1.5,
            // Tap zone boundaries for skip/play-pause detection
            leftZoneEnd: 0.33,
            rightZoneStart: 0.67,
            // Double-tap distance threshold in pixels
            doubleTapDistanceThreshold: 50,
            // Seek gesture sensitivity (pixels per second of seek)
            seekPixelsPerSecond: 100 / 30,  // 100px = 30 seconds
            // Vertical swipe sensitivity in pixels
            verticalSwipeSensitivity: 150,
            // Indicator display duration in ms
            indicatorDisplayDuration: 500,
            // Volume adjustment step for keyboard controls
            volumeStep: 0.05,
            ...options
        };

        // Bound event handlers for proper cleanup
        this._boundHandlers = {};

        // State
        this.controlsVisible = true;
        this.hideControlsTimeout = null;
        this.isSeeking = false;
        this.isInPiP = false;
        this.brightness = 1.0;
        this.currentVolume = 1.0;
        this.currentSpeed = 1.0;
        this.theaterMode = false;

        // Playback speed options
        this.speedOptions = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

        // Gesture tracking
        this.touchStartX = 0;
        this.touchStartY = 0;
        this.touchStartTime = 0;
        this.lastTapTime = 0;
        this.lastTapX = 0;
        this.tapCount = 0;
        this.tapTimeout = null;
        this.isGesturing = false;
        this.gestureType = null; // 'seek', 'volume', 'brightness'
        this.gestureStartValue = 0;

        // Callbacks
        this.onQualityChange = options.onQualityChange || (() => {});
        this.onCaptionsToggle = options.onCaptionsToggle || (() => {});

        this.init();
    }

    init() {
        this.createControlsUI();
        this.bindEvents();
        this.updatePlayPauseButton();
        this.updateVolumeButton();
        this.updateTimeDisplay();
        this.showControls();
    }

    createControlsUI() {
        // Gesture overlay (captures touch events above video)
        this.gestureOverlay = document.createElement('div');
        this.gestureOverlay.className = 'player-gesture-overlay';
        this.gestureOverlay.setAttribute('aria-label', 'Video gesture controls');
        this.gestureOverlay.setAttribute('role', 'application');
        this.container.appendChild(this.gestureOverlay);

        // Skip indicators
        this.skipIndicatorLeft = document.createElement('div');
        this.skipIndicatorLeft.className = 'player-skip-indicator left';
        this.skipIndicatorLeft.innerHTML = `
            <svg viewBox="0 0 24 24" fill="currentColor" class="w-8 h-8">
                <path d="M12.5 8c-2.65 0-5.05.99-6.9 2.6L2 7v9h9l-3.62-3.62c1.39-1.16 3.16-1.88 5.12-1.88 3.54 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 11.03 17.15 8 12.5 8z"/>
            </svg>
            <span class="skip-text">-10s</span>
        `;
        this.container.appendChild(this.skipIndicatorLeft);

        this.skipIndicatorRight = document.createElement('div');
        this.skipIndicatorRight.className = 'player-skip-indicator right';
        this.skipIndicatorRight.innerHTML = `
            <svg viewBox="0 0 24 24" fill="currentColor" class="w-8 h-8">
                <path d="M11.5 8c2.65 0 5.05.99 6.9 2.6L22 7v9h-9l3.62-3.62c-1.39-1.16-3.16-1.88-5.12-1.88-3.54 0-6.55 2.31-7.6 5.5l-2.37-.78C2.92 11.03 6.85 8 11.5 8z"/>
            </svg>
            <span class="skip-text">+10s</span>
        `;
        this.container.appendChild(this.skipIndicatorRight);

        // Center play indicator (for double-tap center play/pause)
        this.centerPlayIndicator = document.createElement('div');
        this.centerPlayIndicator.className = 'player-center-indicator';
        this.centerPlayIndicator.innerHTML = `
            <svg viewBox="0 0 24 24" fill="currentColor" class="play-icon w-12 h-12">
                <path d="M8 5v14l11-7z"/>
            </svg>
            <svg viewBox="0 0 24 24" fill="currentColor" class="pause-icon w-12 h-12">
                <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
            </svg>
        `;
        this.container.appendChild(this.centerPlayIndicator);

        // Adjustment indicator (volume/brightness)
        this.adjustmentIndicator = document.createElement('div');
        this.adjustmentIndicator.className = 'player-adjustment-indicator';
        this.adjustmentIndicator.innerHTML = `
            <div class="adjustment-icon"></div>
            <div class="adjustment-bar">
                <div class="adjustment-fill"></div>
            </div>
        `;
        this.container.appendChild(this.adjustmentIndicator);

        // Seek preview tooltip
        this.seekPreview = document.createElement('div');
        this.seekPreview.className = 'player-seek-preview';
        this.container.appendChild(this.seekPreview);

        // Loading spinner
        this.loadingSpinner = document.createElement('div');
        this.loadingSpinner.className = 'player-loading-spinner';
        this.loadingSpinner.innerHTML = `
            <div class="spinner"></div>
        `;
        this.container.appendChild(this.loadingSpinner);

        // Control bar
        this.controlBar = document.createElement('div');
        this.controlBar.className = 'player-control-bar';
        this.controlBar.innerHTML = `
            <button class="player-btn play-pause-btn" title="Play/Pause (K or Space)" aria-label="Play video" aria-pressed="false">
                <svg viewBox="0 0 24 24" fill="currentColor" class="play-icon" aria-hidden="true">
                    <path d="M8 5v14l11-7z"/>
                </svg>
                <svg viewBox="0 0 24 24" fill="currentColor" class="pause-icon" aria-hidden="true">
                    <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
                </svg>
            </button>
            <div class="player-progress-container" role="slider" aria-label="Video progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" aria-valuetext="0:00 of 0:00" tabindex="0">
                <div class="player-progress-bar">
                    <div class="player-progress-buffered"></div>
                    <div class="player-progress-played"></div>
                    <div class="player-progress-thumb"></div>
                </div>
                <div class="player-progress-tooltip"></div>
            </div>
            <span class="player-time-display">0:00 / 0:00</span>
            <div class="player-controls-right">
                <div class="player-volume-container">
                    <button class="player-btn volume-btn" title="Mute/Unmute (M)" aria-label="Mute" aria-pressed="false">
                        <svg viewBox="0 0 24 24" fill="currentColor" class="volume-high" aria-hidden="true">
                            <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
                        </svg>
                        <svg viewBox="0 0 24 24" fill="currentColor" class="volume-low" aria-hidden="true">
                            <path d="M18.5 12c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM5 9v6h4l5 5V4L9 9H5z"/>
                        </svg>
                        <svg viewBox="0 0 24 24" fill="currentColor" class="volume-muted" aria-hidden="true">
                            <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/>
                        </svg>
                    </button>
                    <div class="player-volume-slider-container">
                        <div class="player-volume-slider" role="slider" aria-label="Volume" aria-valuemin="0" aria-valuemax="100" aria-valuenow="100" tabindex="0">
                            <div class="player-volume-track">
                                <div class="player-volume-fill"></div>
                                <div class="player-volume-thumb"></div>
                            </div>
                        </div>
                    </div>
                </div>
                <button class="player-btn quality-btn" title="Quality" aria-label="Video quality: Auto" aria-haspopup="true" aria-expanded="false">
                    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                        <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-8 12H9.5v-2h-2v2H6V9h1.5v2.5h2V9H11v6zm7-1c0 .55-.45 1-1 1h-.75v1.5h-1.5V15H14c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1h3c.55 0 1 .45 1 1v4zm-3.5-.5h2v-3h-2v3z"/>
                    </svg>
                    <span class="quality-label">Auto</span>
                </button>
                <button class="player-btn speed-btn" title="Playback speed (Shift+> / Shift+<)" aria-label="Playback speed: Normal" aria-haspopup="true" aria-expanded="false">
                    <span class="speed-label">1x</span>
                </button>
                <button class="player-btn captions-btn hidden" title="Captions (C)" aria-label="Toggle captions" aria-pressed="false">
                    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                        <path d="M19 4H5c-1.11 0-2 .9-2 2v12c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 7H9.5v-.5h-2v3h2V13H11v1c0 .55-.45 1-1 1H7c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1h3c.55 0 1 .45 1 1v1zm7 0h-1.5v-.5h-2v3h2V13H18v1c0 .55-.45 1-1 1h-3c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1h3c.55 0 1 .45 1 1v1z"/>
                    </svg>
                </button>
                <button class="player-btn share-btn" title="Share" aria-label="Share video" aria-haspopup="true" aria-expanded="false">
                    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                        <path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92s2.92-1.31 2.92-2.92-1.31-2.92-2.92-2.92z"/>
                    </svg>
                </button>
                <button class="player-btn theater-btn" title="Theater mode (T)" aria-label="Enter theater mode" aria-pressed="false">
                    <svg viewBox="0 0 24 24" fill="currentColor" class="theater-enter" aria-hidden="true">
                        <path d="M19 6H5c-1.1 0-2 .9-2 2v8c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 10H5V8h14v8z"/>
                    </svg>
                    <svg viewBox="0 0 24 24" fill="currentColor" class="theater-exit" aria-hidden="true">
                        <path d="M19 4H5c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H5V6h14v12z"/>
                    </svg>
                </button>
                <button class="player-btn pip-btn hidden" title="Picture in Picture (P)" aria-label="Picture in picture">
                    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                        <path d="M19 7h-8v6h8V7zm2-4H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H3V5h18v14z"/>
                    </svg>
                </button>
                <button class="player-btn fullscreen-btn" title="Fullscreen (F)" aria-label="Enter fullscreen" aria-pressed="false">
                    <svg viewBox="0 0 24 24" fill="currentColor" class="fullscreen-enter" aria-hidden="true">
                        <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/>
                    </svg>
                    <svg viewBox="0 0 24 24" fill="currentColor" class="fullscreen-exit" aria-hidden="true">
                        <path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/>
                    </svg>
                </button>
            </div>
        `;
        this.container.appendChild(this.controlBar);

        // Live region for screen reader announcements
        this.liveRegion = document.createElement('div');
        this.liveRegion.className = 'player-sr-only';
        this.liveRegion.setAttribute('role', 'status');
        this.liveRegion.setAttribute('aria-live', 'polite');
        this.liveRegion.setAttribute('aria-atomic', 'true');
        this.container.appendChild(this.liveRegion);

        // Quality modal (for mobile)
        this.qualityModal = document.createElement('div');
        this.qualityModal.className = 'player-quality-modal';
        this.qualityModal.setAttribute('role', 'dialog');
        this.qualityModal.setAttribute('aria-label', 'Video quality selection');
        this.qualityModal.setAttribute('aria-hidden', 'true');
        this.qualityModal.innerHTML = `
            <div class="quality-modal-backdrop" aria-hidden="true"></div>
            <div class="quality-modal-content">
                <div class="quality-modal-header">Quality</div>
                <div class="quality-modal-options" role="listbox" aria-label="Quality options"></div>
            </div>
        `;
        this.container.appendChild(this.qualityModal);

        // Speed modal
        this.speedModal = document.createElement('div');
        this.speedModal.className = 'player-speed-modal';
        this.speedModal.setAttribute('role', 'dialog');
        this.speedModal.setAttribute('aria-label', 'Playback speed selection');
        this.speedModal.setAttribute('aria-hidden', 'true');
        this.speedModal.innerHTML = `
            <div class="speed-modal-backdrop" aria-hidden="true"></div>
            <div class="speed-modal-content">
                <div class="speed-modal-header">Playback Speed</div>
                <div class="speed-modal-options" role="listbox" aria-label="Speed options"></div>
            </div>
        `;
        this.container.appendChild(this.speedModal);
        this.buildSpeedOptions();

        // Keyboard shortcuts help modal
        this.shortcutsModal = document.createElement('div');
        this.shortcutsModal.className = 'player-shortcuts-modal';
        this.shortcutsModal.setAttribute('role', 'dialog');
        this.shortcutsModal.setAttribute('aria-label', 'Keyboard shortcuts');
        this.shortcutsModal.setAttribute('aria-modal', 'true');
        this.shortcutsModal.setAttribute('aria-hidden', 'true');
        this.shortcutsModal.innerHTML = `
            <div class="shortcuts-modal-backdrop" aria-hidden="true"></div>
            <div class="shortcuts-modal-content">
                <div class="shortcuts-modal-header">
                    <span>Keyboard Shortcuts</span>
                    <button class="shortcuts-close-btn" aria-label="Close shortcuts help">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                        </svg>
                    </button>
                </div>
                <div class="shortcuts-modal-body">
                    <div class="shortcut-row"><kbd>Space</kbd> or <kbd>K</kbd><span>Play/Pause</span></div>
                    <div class="shortcut-row"><kbd>←</kbd><span>Rewind 10s</span></div>
                    <div class="shortcut-row"><kbd>→</kbd><span>Forward 10s</span></div>
                    <div class="shortcut-row"><kbd>↑</kbd><span>Volume up</span></div>
                    <div class="shortcut-row"><kbd>↓</kbd><span>Volume down</span></div>
                    <div class="shortcut-row"><kbd>M</kbd><span>Mute/Unmute</span></div>
                    <div class="shortcut-row"><kbd>F</kbd><span>Fullscreen</span></div>
                    <div class="shortcut-row"><kbd>T</kbd><span>Theater mode</span></div>
                    <div class="shortcut-row"><kbd>C</kbd><span>Toggle captions</span></div>
                    <div class="shortcut-row"><kbd>P</kbd><span>Picture in Picture</span></div>
                    <div class="shortcut-row"><kbd>Shift</kbd>+<kbd>></kbd><span>Speed up</span></div>
                    <div class="shortcut-row"><kbd>Shift</kbd>+<kbd><</kbd><span>Slow down</span></div>
                    <div class="shortcut-row"><kbd>0-9</kbd><span>Jump to 0-90%</span></div>
                    <div class="shortcut-row"><kbd>?</kbd><span>Show shortcuts</span></div>
                </div>
            </div>
        `;
        this.container.appendChild(this.shortcutsModal);

        // Share modal (Issue #413 Phase 5)
        this.shareModal = document.createElement('div');
        this.shareModal.className = 'player-share-modal';
        this.shareModal.setAttribute('role', 'dialog');
        this.shareModal.setAttribute('aria-label', 'Share video');
        this.shareModal.setAttribute('aria-modal', 'true');
        this.shareModal.setAttribute('aria-hidden', 'true');
        this.shareModal.innerHTML = `
            <div class="share-modal-backdrop" aria-hidden="true"></div>
            <div class="share-modal-content">
                <div class="share-modal-header">
                    <span>Share</span>
                    <button class="share-close-btn" aria-label="Close share dialog">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                        </svg>
                    </button>
                </div>
                <div class="share-modal-body">
                    <input type="text" class="share-modal-input" readonly aria-label="Video URL">
                    <button class="share-modal-copy" aria-label="Copy link">
                        <svg viewBox="0 0 24 24" fill="currentColor" class="share-copy-icon">
                            <path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/>
                        </svg>
                        <span class="share-modal-copy-text">Copy</span>
                    </button>
                </div>
            </div>
        `;
        this.container.appendChild(this.shareModal);

        // Cache DOM references
        this.playPauseBtn = this.controlBar.querySelector('.play-pause-btn');
        this.progressContainer = this.controlBar.querySelector('.player-progress-container');
        this.progressBar = this.controlBar.querySelector('.player-progress-bar');
        this.progressBuffered = this.controlBar.querySelector('.player-progress-buffered');
        this.progressPlayed = this.controlBar.querySelector('.player-progress-played');
        this.progressThumb = this.controlBar.querySelector('.player-progress-thumb');
        this.progressTooltip = this.controlBar.querySelector('.player-progress-tooltip');
        this.timeDisplay = this.controlBar.querySelector('.player-time-display');
        this.volumeContainer = this.controlBar.querySelector('.player-volume-container');
        this.volumeBtn = this.controlBar.querySelector('.volume-btn');
        this.volumeSliderContainer = this.controlBar.querySelector('.player-volume-slider-container');
        this.volumeSlider = this.controlBar.querySelector('.player-volume-slider');
        this.volumeTrack = this.controlBar.querySelector('.player-volume-track');
        this.volumeFill = this.controlBar.querySelector('.player-volume-fill');
        this.volumeThumb = this.controlBar.querySelector('.player-volume-thumb');
        this.qualityBtn = this.controlBar.querySelector('.quality-btn');
        this.qualityLabel = this.qualityBtn.querySelector('.quality-label');
        this.speedBtn = this.controlBar.querySelector('.speed-btn');
        this.speedLabel = this.speedBtn.querySelector('.speed-label');
        this.captionsBtn = this.controlBar.querySelector('.captions-btn');
        this.theaterBtn = this.controlBar.querySelector('.theater-btn');
        this.pipBtn = this.controlBar.querySelector('.pip-btn');
        this.fullscreenBtn = this.controlBar.querySelector('.fullscreen-btn');
        this.qualityModalOptions = this.qualityModal.querySelector('.quality-modal-options');
        this.speedModalOptions = this.speedModal.querySelector('.speed-modal-options');

        // Share modal references (Issue #413 Phase 5)
        this.shareBtn = this.controlBar.querySelector('.share-btn');
        this.shareInput = this.shareModal.querySelector('.share-modal-input');
        this.shareCopyBtn = this.shareModal.querySelector('.share-modal-copy');
        this.shareCloseBtn = this.shareModal.querySelector('.share-close-btn');
    }

    bindEvents() {
        // Create bound handlers for later removal
        this._boundHandlers.onPlay = () => this.updatePlayPauseButton();
        this._boundHandlers.onPause = () => this.updatePlayPauseButton();
        this._boundHandlers.onTimeUpdate = this._throttle(() => this.updateProgress(), 250);
        this._boundHandlers.onProgress = () => this.updateBuffered();
        this._boundHandlers.onLoadedMetadata = () => this.updateTimeDisplay();
        this._boundHandlers.onDurationChange = () => this.updateTimeDisplay();
        this._boundHandlers.onWaiting = () => this.showLoading();
        this._boundHandlers.onCanPlay = () => this.hideLoading();
        this._boundHandlers.onPlaying = () => this.hideLoading();
        this._boundHandlers.onVolumeChange = () => this.updateVolumeButton();
        this._boundHandlers.onEnded = () => this.showControls();
        this._boundHandlers.onEnterPiP = () => {
            this.isInPiP = true;
            this.hideControls();
        };
        this._boundHandlers.onLeavePiP = () => {
            this.isInPiP = false;
            this.showControls();
        };
        this._boundHandlers.onFullscreenChange = () => this.updateFullscreenButton();
        this._boundHandlers.onKeyDown = (e) => this.handleKeyboard(e);

        // Video events
        this.video.addEventListener('play', this._boundHandlers.onPlay);
        this.video.addEventListener('pause', this._boundHandlers.onPause);
        this.video.addEventListener('timeupdate', this._boundHandlers.onTimeUpdate);
        this.video.addEventListener('progress', this._boundHandlers.onProgress);
        this.video.addEventListener('loadedmetadata', this._boundHandlers.onLoadedMetadata);
        this.video.addEventListener('durationchange', this._boundHandlers.onDurationChange);
        this.video.addEventListener('waiting', this._boundHandlers.onWaiting);
        this.video.addEventListener('canplay', this._boundHandlers.onCanPlay);
        this.video.addEventListener('playing', this._boundHandlers.onPlaying);
        this.video.addEventListener('volumechange', this._boundHandlers.onVolumeChange);
        this.video.addEventListener('ended', this._boundHandlers.onEnded);

        // PiP events
        this.video.addEventListener('enterpictureinpicture', this._boundHandlers.onEnterPiP);
        this.video.addEventListener('leavepictureinpicture', this._boundHandlers.onLeavePiP);

        // Fullscreen events
        document.addEventListener('fullscreenchange', this._boundHandlers.onFullscreenChange);
        document.addEventListener('webkitfullscreenchange', this._boundHandlers.onFullscreenChange);

        // Control bar buttons
        this.playPauseBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.togglePlayPause();
        });
        this.volumeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleMute();
        });

        // Volume slider interaction
        this._boundHandlers.onVolumeSliderMouseDown = (e) => this.startVolumeSeek(e);
        this._boundHandlers.onVolumeSliderTouchStart = (e) => this.handleVolumeSliderTouchStart(e);
        this._boundHandlers.onVolumeSliderKeyDown = (e) => this.handleVolumeSliderKeyboard(e);
        this.volumeSlider.addEventListener('mousedown', this._boundHandlers.onVolumeSliderMouseDown);
        this.volumeSlider.addEventListener('touchstart', this._boundHandlers.onVolumeSliderTouchStart, { passive: false });
        this.volumeSlider.addEventListener('keydown', this._boundHandlers.onVolumeSliderKeyDown);
        this.qualityBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.showQualityModal();
        });
        this.speedBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.showSpeedModal();
        });
        this.captionsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.onCaptionsToggle();
        });
        this.theaterBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleTheaterMode();
        });
        this.pipBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.togglePiP();
        });
        this.fullscreenBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleFullscreen();
        });

        // Progress bar interaction
        this.progressContainer.addEventListener('mousedown', (e) => this.startProgressSeek(e));
        this.progressContainer.addEventListener('touchstart', (e) => {
            e.stopPropagation();
            this.startProgressSeek(e);
        }, { passive: false });
        this.progressContainer.addEventListener('mousemove', (e) => this.showProgressTooltip(e));
        this.progressContainer.addEventListener('mouseleave', () => this.hideProgressTooltip());
        this._boundHandlers.onProgressSliderKeyDown = (e) => this.handleProgressSliderKeyboard(e);
        this.progressContainer.addEventListener('keydown', this._boundHandlers.onProgressSliderKeyDown);

        // Quality modal
        this.qualityModal.querySelector('.quality-modal-backdrop').addEventListener('click', () => {
            this.hideQualityModal();
        });

        // Speed modal
        this.speedModal.querySelector('.speed-modal-backdrop').addEventListener('click', () => {
            this.hideSpeedModal();
        });

        // Share button and modal (Issue #413 Phase 5)
        this.shareBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.showShareModal();
        });
        this.shareModal.querySelector('.share-modal-backdrop').addEventListener('click', () => {
            this.hideShareModal();
        });
        this.shareCloseBtn.addEventListener('click', () => {
            this.hideShareModal();
        });
        this.shareCopyBtn.addEventListener('click', () => {
            this.copyShareLink();
        });

        // Shortcuts modal
        this.shortcutsModal.querySelector('.shortcuts-modal-backdrop').addEventListener('click', () => {
            this.hideShortcutsModal();
        });
        this.shortcutsModal.querySelector('.shortcuts-close-btn').addEventListener('click', () => {
            this.hideShortcutsModal();
        });

        // Gesture overlay - touch events
        this.gestureOverlay.addEventListener('touchstart', (e) => this.handleTouchStart(e), { passive: false });
        this.gestureOverlay.addEventListener('touchmove', (e) => this.handleTouchMove(e), { passive: false });
        this.gestureOverlay.addEventListener('touchend', (e) => this.handleTouchEnd(e), { passive: false });
        this.gestureOverlay.addEventListener('touchcancel', (e) => this.handleTouchEnd(e), { passive: false });

        // Gesture overlay - mouse events (for desktop)
        this.gestureOverlay.addEventListener('click', (e) => this.handleClick(e));
        this.gestureOverlay.addEventListener('dblclick', (e) => this.handleDoubleClick(e));
        this.gestureOverlay.addEventListener('mousemove', () => this.showControls());

        // Control bar hover keeps controls visible
        this.controlBar.addEventListener('mouseenter', () => this.showControls());
        this.controlBar.addEventListener('mousemove', () => this.showControls());

        // Keyboard controls - scoped to container for focus-based handling
        this.container.setAttribute('tabindex', '0');
        this.container.addEventListener('keydown', this._boundHandlers.onKeyDown);

        // Check PiP support
        if (document.pictureInPictureEnabled && !this.video.disablePictureInPicture) {
            this.pipBtn.classList.remove('hidden');
        }
    }

    // Throttle utility to limit function call frequency
    _throttle(func, limit) {
        let inThrottle = false;
        return function(...args) {
            if (!inThrottle) {
                func.apply(this, args);
                inThrottle = true;
                setTimeout(() => inThrottle = false, limit);
            }
        };
    }

    // Playback controls
    togglePlayPause() {
        if (this.video.paused) {
            this.video.play();
        } else {
            this.video.pause();
        }
    }

    updatePlayPauseButton() {
        const isPlaying = !this.video.paused;
        this.playPauseBtn.classList.toggle('playing', isPlaying);
        this.playPauseBtn.title = isPlaying ? 'Pause' : 'Play';
        this.playPauseBtn.setAttribute('aria-label', isPlaying ? 'Pause video' : 'Play video');
        this.playPauseBtn.setAttribute('aria-pressed', isPlaying.toString());
    }

    // Progress/seeking
    updateProgress() {
        if (this.isSeeking) return;
        const progress = (this.video.currentTime / this.video.duration) * 100 || 0;
        this.progressPlayed.style.width = `${progress}%`;
        this.progressThumb.style.left = `${progress}%`;
        this.updateTimeDisplay();
    }

    updateBuffered() {
        if (this.video.buffered.length > 0) {
            const bufferedEnd = this.video.buffered.end(this.video.buffered.length - 1);
            const buffered = (bufferedEnd / this.video.duration) * 100 || 0;
            this.progressBuffered.style.width = `${buffered}%`;
        }
    }

    updateTimeDisplay() {
        const current = this.formatTime(this.video.currentTime);
        const duration = this.formatTime(this.video.duration);
        this.timeDisplay.textContent = `${current} / ${duration}`;
        // Update progress slider ARIA attributes
        const progress = (this.video.currentTime / this.video.duration) * 100 || 0;
        this.progressContainer.setAttribute('aria-valuenow', Math.round(progress));
        this.progressContainer.setAttribute('aria-valuetext', `${current} of ${duration}`);
    }

    formatTime(seconds) {
        if (isNaN(seconds) || !isFinite(seconds)) return '0:00';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        if (h > 0) {
            return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }
        return `${m}:${s.toString().padStart(2, '0')}`;
    }

    startProgressSeek(e) {
        e.preventDefault();
        this.isSeeking = true;
        this.updateSeekPosition(e);

        const onMove = (moveEvent) => {
            this.updateSeekPosition(moveEvent);
        };

        const onEnd = () => {
            this.isSeeking = false;
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onEnd);
            document.removeEventListener('touchmove', onMove);
            document.removeEventListener('touchend', onEnd);
            this.hideProgressTooltip();
        };

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onEnd);
        document.addEventListener('touchmove', onMove, { passive: false });
        document.addEventListener('touchend', onEnd);
    }

    updateSeekPosition(e) {
        const rect = this.progressBar.getBoundingClientRect();
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        let percent = (clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));

        // Only seek if video metadata is loaded and duration is valid
        if (this.video.readyState >= 1 && Number.isFinite(this.video.duration)) {
            const seekTime = percent * this.video.duration;
            this.video.currentTime = seekTime;

            this.progressPlayed.style.width = `${percent * 100}%`;
            this.progressThumb.style.left = `${percent * 100}%`;

            // Show tooltip
            this.progressTooltip.textContent = this.formatTime(seekTime);
            this.progressTooltip.style.left = `${percent * 100}%`;
            this.progressTooltip.classList.add('visible');
        } else {
            // Show tooltip at current position even if seeking is not possible
            this.progressTooltip.textContent = '0:00';
            this.progressTooltip.style.left = `${percent * 100}%`;
            this.progressTooltip.classList.add('visible');
        }
    }

    showProgressTooltip(e) {
        if (this.isSeeking) return;
        const rect = this.progressBar.getBoundingClientRect();
        let percent = (e.clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));
        const time = percent * this.video.duration;

        this.progressTooltip.textContent = this.formatTime(time);
        this.progressTooltip.style.left = `${percent * 100}%`;
        this.progressTooltip.classList.add('visible');
    }

    hideProgressTooltip() {
        this.progressTooltip.classList.remove('visible');
    }

    // Volume
    toggleMute() {
        if (this.video.muted || this.video.volume === 0) {
            this.video.muted = false;
            this.video.volume = this.currentVolume || 1;
        } else {
            this.currentVolume = this.video.volume;
            this.video.muted = true;
        }
    }

    updateVolumeButton() {
        const muted = this.video.muted || this.video.volume === 0;
        const volume = this.video.muted ? 0 : this.video.volume;

        // Update icon based on volume level
        this.volumeBtn.classList.toggle('muted', muted);
        this.volumeBtn.classList.toggle('low', !muted && volume > 0 && volume < 0.5);
        this.volumeBtn.classList.toggle('high', !muted && volume >= 0.5);

        // Update ARIA states
        this.volumeBtn.setAttribute('aria-label', muted ? 'Unmute' : 'Mute');
        this.volumeBtn.setAttribute('aria-pressed', muted.toString());

        // Update slider fill
        this.updateVolumeSlider(volume);
    }

    updateVolumeSlider(volume) {
        const percent = volume * 100;
        this.volumeFill.style.width = `${percent}%`;
        this.volumeThumb.style.left = `${percent}%`;
        this.volumeSlider.setAttribute('aria-valuenow', Math.round(percent));
    }

    handleVolumeSliderTouchStart(e) {
        e.stopPropagation();
        this.startVolumeSeek(e);
    }

    startVolumeSeek(e) {
        e.preventDefault();
        e.stopPropagation();
        this.updateVolumeFromEvent(e);

        const onMove = (moveEvent) => {
            this.updateVolumeFromEvent(moveEvent);
        };

        const onEnd = () => {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onEnd);
            document.removeEventListener('touchmove', onMove);
            document.removeEventListener('touchend', onEnd);
        };

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onEnd);
        document.addEventListener('touchmove', onMove, { passive: false });
        document.addEventListener('touchend', onEnd);
    }

    updateVolumeFromEvent(e) {
        const rect = this.volumeTrack.getBoundingClientRect();
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        let percent = (clientX - rect.left) / rect.width;
        percent = Math.max(0, Math.min(1, percent));
        this.setVolume(percent);
    }

    setVolume(value) {
        this.video.volume = Math.max(0, Math.min(1, value));
        this.video.muted = false;
        this.currentVolume = this.video.volume;
    }

    handleVolumeSliderKeyboard(e) {
        // Arrow keys for volume adjustment
        if (e.key === 'ArrowUp' || e.key === 'ArrowRight') {
            e.preventDefault();
            e.stopPropagation();
            const newVolume = Math.min(1, this.video.volume + this.options.volumeStep);
            this.setVolume(newVolume);
        } else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') {
            e.preventDefault();
            e.stopPropagation();
            const newVolume = Math.max(0, this.video.volume - this.options.volumeStep);
            this.setVolume(newVolume);
        } else if (e.key === 'Home') {
            e.preventDefault();
            e.stopPropagation();
            this.setVolume(0);
        } else if (e.key === 'End') {
            e.preventDefault();
            e.stopPropagation();
            this.setVolume(1);
        }
    }

    handleProgressSliderKeyboard(e) {
        // Arrow keys for seeking (5 seconds per press)
        const seekStep = 5;
        const duration = this.video.duration || 0;
        if (!duration) return;

        if (e.key === 'ArrowRight') {
            e.preventDefault();
            e.stopPropagation();
            this.video.currentTime = Math.min(duration, this.video.currentTime + seekStep);
        } else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            e.stopPropagation();
            this.video.currentTime = Math.max(0, this.video.currentTime - seekStep);
        } else if (e.key === 'ArrowUp') {
            // Larger seek (30 seconds)
            e.preventDefault();
            e.stopPropagation();
            this.video.currentTime = Math.min(duration, this.video.currentTime + 30);
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            e.stopPropagation();
            this.video.currentTime = Math.max(0, this.video.currentTime - 30);
        } else if (e.key === 'Home') {
            e.preventDefault();
            e.stopPropagation();
            this.video.currentTime = 0;
        } else if (e.key === 'End') {
            e.preventDefault();
            e.stopPropagation();
            this.video.currentTime = duration;
        }
    }

    // Quality
    setQualities(levels, currentIndex = -1) {
        this.qualities = levels;
        this.currentQualityIndex = currentIndex;
        this.currentAutoQuality = null; // Track actual quality when in auto mode
        this.updateQualityLabel();
        this.updateQualityModal();
    }

    // Called by the player to update the display when ABR changes quality
    setCurrentAutoQuality(height) {
        this.currentAutoQuality = height;
        this.updateQualityLabel();
    }

    updateQualityLabel() {
        let qualityText;
        if (this.currentQualityIndex === -1) {
            // Auto mode - show current resolution if available
            if (this.currentAutoQuality) {
                qualityText = this.currentAutoQuality + 'p (Auto)';
            } else {
                qualityText = 'Auto';
            }
        } else if (this.qualities && this.qualities[this.currentQualityIndex]) {
            const level = this.qualities[this.currentQualityIndex];
            qualityText = level.isOriginal ? 'Original' : level.height + 'p';
        } else {
            qualityText = 'Auto';
        }
        this.qualityLabel.textContent = qualityText;
        this.qualityBtn.setAttribute('aria-label', `Video quality: ${qualityText}`);
    }

    updateQualityModal() {
        if (!this.qualities) return;

        this.qualityModalOptions.innerHTML = '';

        // Auto option
        const autoOption = document.createElement('button');
        autoOption.className = 'quality-option' + (this.currentQualityIndex === -1 ? ' active' : '');
        autoOption.textContent = 'Auto';
        autoOption.addEventListener('click', () => {
            this.selectQuality(-1);
        });
        this.qualityModalOptions.appendChild(autoOption);

        // Quality levels
        this.qualities.forEach((level, index) => {
            const option = document.createElement('button');
            option.className = 'quality-option' + (index === this.currentQualityIndex ? ' active' : '');
            option.textContent = level.isOriginal ? 'Original' : level.height + 'p';
            option.addEventListener('click', () => {
                this.selectQuality(index);
            });
            this.qualityModalOptions.appendChild(option);
        });
    }

    selectQuality(index) {
        this.currentQualityIndex = index;
        this.updateQualityLabel();
        this.updateQualityModal();
        this.hideQualityModal();
        this.onQualityChange(index);
    }

    showQualityModal() {
        this._hideAllModals();
        this._lastFocusedElement = document.activeElement;
        this.qualityModal.classList.add('visible');
        this.qualityModal.setAttribute('aria-hidden', 'false');
        this.qualityBtn.setAttribute('aria-expanded', 'true');
        // Focus first option
        const firstOption = this.qualityModalOptions.querySelector('.quality-option');
        if (firstOption) {
            firstOption.focus();
            this._trapFocus(this.qualityModal);
        }
    }

    hideQualityModal() {
        this.qualityModal.classList.remove('visible');
        this.qualityModal.setAttribute('aria-hidden', 'true');
        this.qualityBtn.setAttribute('aria-expanded', 'false');
        this._removeFocusTrap();
        this._restoreFocus();
    }

    // Speed control
    buildSpeedOptions() {
        this.speedModalOptions.innerHTML = '';

        this.speedOptions.forEach(speed => {
            const option = document.createElement('button');
            option.className = 'speed-option' + (speed === this.currentSpeed ? ' active' : '');
            option.textContent = speed === 1 ? 'Normal' : speed + 'x';
            option.addEventListener('click', () => {
                this.selectSpeed(speed);
            });
            this.speedModalOptions.appendChild(option);
        });
    }

    selectSpeed(speed) {
        try {
            this.video.playbackRate = speed;
            // Verify the rate was actually set (browsers may clamp values)
            this.currentSpeed = this.video.playbackRate;
        } catch (e) {
            console.warn('Failed to set playback rate:', e);
            this.currentSpeed = this.video.playbackRate;
        }
        this.updateSpeedLabel();
        this.buildSpeedOptions();
        this.hideSpeedModal();
        // Announce to screen readers
        this._announce(`Playback speed changed to ${this.currentSpeed === 1 ? 'normal' : this.currentSpeed + 'x'}`);
    }

    updateSpeedLabel() {
        const labelText = this.currentSpeed === 1 ? '1x' : this.currentSpeed + 'x';
        this.speedLabel.textContent = labelText;
        this.speedBtn.setAttribute('aria-label', `Playback speed: ${this.currentSpeed === 1 ? 'Normal' : this.currentSpeed + 'x'}`);
    }

    showSpeedModal() {
        this._hideAllModals();
        this._lastFocusedElement = document.activeElement;
        this.speedModal.classList.add('visible');
        this.speedModal.setAttribute('aria-hidden', 'false');
        this.speedBtn.setAttribute('aria-expanded', 'true');
        // Focus first option
        const firstOption = this.speedModalOptions.querySelector('.speed-option');
        if (firstOption) {
            firstOption.focus();
            this._trapFocus(this.speedModal);
        }
    }

    hideSpeedModal() {
        this.speedModal.classList.remove('visible');
        this.speedModal.setAttribute('aria-hidden', 'true');
        this.speedBtn.setAttribute('aria-expanded', 'false');
        this._removeFocusTrap();
        this._restoreFocus();
    }

    increaseSpeed() {
        const currentIndex = this.speedOptions.indexOf(this.currentSpeed);
        // Guard against currentSpeed not being in the array
        if (currentIndex === -1) {
            // Find nearest speed option and use that
            this.selectSpeed(1); // Reset to normal
            return;
        }
        if (currentIndex < this.speedOptions.length - 1) {
            this.selectSpeed(this.speedOptions[currentIndex + 1]);
        }
    }

    decreaseSpeed() {
        const currentIndex = this.speedOptions.indexOf(this.currentSpeed);
        // Guard against currentSpeed not being in the array
        if (currentIndex === -1) {
            this.selectSpeed(1); // Reset to normal
            return;
        }
        if (currentIndex > 0) {
            this.selectSpeed(this.speedOptions[currentIndex - 1]);
        }
    }

    // Theater mode
    toggleTheaterMode() {
        this.theaterMode = !this.theaterMode;
        this.container.classList.toggle('theater-mode', this.theaterMode);
        // Add body class for CSS :has() fallback (older browsers)
        document.body.classList.toggle('player-theater-active', this.theaterMode);
        this.theaterBtn.classList.toggle('active', this.theaterMode);
        this.theaterBtn.setAttribute('aria-pressed', this.theaterMode.toString());
        this.theaterBtn.setAttribute('aria-label', this.theaterMode ? 'Exit theater mode' : 'Enter theater mode');

        // Announce to screen readers
        this._announce(this.theaterMode ? 'Theater mode enabled' : 'Theater mode disabled');

        // Dispatch custom event for parent components to react
        this.container.dispatchEvent(new CustomEvent('theatermodechange', {
            detail: { theaterMode: this.theaterMode },
            bubbles: true
        }));
    }

    // Keyboard shortcuts help
    showShortcutsModal() {
        this._hideAllModals();
        this._lastFocusedElement = document.activeElement;
        this.shortcutsModal.classList.add('visible');
        this.shortcutsModal.setAttribute('aria-hidden', 'false');
        // Focus the close button for accessibility
        const closeBtn = this.shortcutsModal.querySelector('.shortcuts-close-btn');
        if (closeBtn) {
            closeBtn.focus();
            this._trapFocus(this.shortcutsModal);
        }
    }

    hideShortcutsModal() {
        this.shortcutsModal.classList.remove('visible');
        this.shortcutsModal.setAttribute('aria-hidden', 'true');
        this._removeFocusTrap();
        this._restoreFocus();
    }

    // Share modal (Issue #413 Phase 5)
    showShareModal() {
        this._hideAllModals();
        this._lastFocusedElement = document.activeElement;

        // Set canonical URL (excludes query params/fragments for security)
        const shareUrl = window.location.origin + window.location.pathname;
        this.shareInput.value = shareUrl;

        this.shareModal.classList.add('visible');
        this.shareModal.setAttribute('aria-hidden', 'false');
        this.shareBtn.setAttribute('aria-expanded', 'true');

        // Focus the copy button for accessibility
        this.shareCopyBtn.focus();
        this._trapFocus(this.shareModal);
    }

    hideShareModal() {
        this.shareModal.classList.remove('visible');
        this.shareModal.setAttribute('aria-hidden', 'true');
        this.shareBtn.setAttribute('aria-expanded', 'false');
        this._removeFocusTrap();
        this._restoreFocus();
    }

    async copyShareLink() {
        const url = this.shareInput.value;
        const copyText = this.shareCopyBtn.querySelector('.share-modal-copy-text');

        try {
            // Feature detect clipboard API (requires HTTPS or localhost)
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(url);
            } else {
                // Fallback for HTTP or older browsers
                this.shareInput.select();
                this.shareInput.setSelectionRange(0, 99999); // Mobile support
                // Critical fix (Margo): Check execCommand return value
                const success = document.execCommand('copy');
                if (!success) {
                    throw new Error('execCommand copy returned false');
                }
            }

            copyText.textContent = 'Copied!';
            this.shareCopyBtn.classList.add('copied');
            this._announce('Link copied to clipboard');

            // Reset after 2 seconds
            setTimeout(() => {
                copyText.textContent = 'Copy';
                this.shareCopyBtn.classList.remove('copied');
            }, 2000);
        } catch (err) {
            console.error('Copy failed:', err);

            // Provide more specific error messages
            const message = err.name === 'NotAllowedError'
                ? 'Permission denied'
                : 'Copy failed';
            copyText.textContent = message;

            setTimeout(() => {
                copyText.textContent = 'Copy';
            }, 2000);
        }
    }

    // Helper: Hide all modals
    _hideAllModals() {
        if (this.qualityModal.classList.contains('visible')) {
            this.qualityModal.classList.remove('visible');
            this.qualityModal.setAttribute('aria-hidden', 'true');
            this.qualityBtn.setAttribute('aria-expanded', 'false');
        }
        if (this.speedModal.classList.contains('visible')) {
            this.speedModal.classList.remove('visible');
            this.speedModal.setAttribute('aria-hidden', 'true');
            this.speedBtn.setAttribute('aria-expanded', 'false');
        }
        if (this.shortcutsModal.classList.contains('visible')) {
            this.shortcutsModal.classList.remove('visible');
            this.shortcutsModal.setAttribute('aria-hidden', 'true');
        }
        if (this.shareModal.classList.contains('visible')) {
            this.shareModal.classList.remove('visible');
            this.shareModal.setAttribute('aria-hidden', 'true');
            this.shareBtn.setAttribute('aria-expanded', 'false');
        }
        this._removeFocusTrap();
    }

    // Helper: Trap focus within a modal
    _trapFocus(modalElement) {
        const focusableSelectors = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
        const focusableElements = modalElement.querySelectorAll(focusableSelectors);
        if (focusableElements.length === 0) return;

        const firstFocusable = focusableElements[0];
        const lastFocusable = focusableElements[focusableElements.length - 1];

        this._focusTrapHandler = (e) => {
            if (e.key !== 'Tab') return;

            if (e.shiftKey) {
                if (document.activeElement === firstFocusable) {
                    e.preventDefault();
                    lastFocusable.focus();
                }
            } else {
                if (document.activeElement === lastFocusable) {
                    e.preventDefault();
                    firstFocusable.focus();
                }
            }
        };

        modalElement.addEventListener('keydown', this._focusTrapHandler);
        this._currentTrapModal = modalElement;
    }

    // Helper: Remove focus trap
    _removeFocusTrap() {
        if (this._focusTrapHandler && this._currentTrapModal) {
            this._currentTrapModal.removeEventListener('keydown', this._focusTrapHandler);
            this._focusTrapHandler = null;
            this._currentTrapModal = null;
        }
    }

    // Helper: Restore focus to last focused element
    _restoreFocus() {
        if (this._lastFocusedElement && typeof this._lastFocusedElement.focus === 'function') {
            try {
                this._lastFocusedElement.focus();
            } catch (e) {
                // Focus failed, try container
                if (this.container) {
                    this.container.focus();
                }
            }
            this._lastFocusedElement = null;
        } else if (this.container) {
            this.container.focus();
        }
    }

    // Helper: Announce message to screen readers
    _announce(message) {
        if (this.liveRegion) {
            this.liveRegion.textContent = message;
        }
    }

    // Captions
    setCaptionsAvailable(available) {
        this.captionsBtn.classList.toggle('hidden', !available);
    }

    setCaptionsEnabled(enabled) {
        this.captionsBtn.classList.toggle('active', enabled);
        this.captionsBtn.setAttribute('aria-pressed', enabled.toString());
        this.captionsBtn.setAttribute('aria-label', enabled ? 'Captions on' : 'Captions off');
    }

    // Picture-in-Picture
    async togglePiP() {
        try {
            if (document.pictureInPictureElement) {
                await document.exitPictureInPicture();
            } else if (this.video.webkitSetPresentationMode) {
                // Safari
                const mode = this.video.webkitPresentationMode === 'picture-in-picture'
                    ? 'inline'
                    : 'picture-in-picture';
                this.video.webkitSetPresentationMode(mode);
            } else {
                await this.video.requestPictureInPicture();
            }
        } catch (err) {
            // Only log unexpected errors; NotAllowedError is expected if user cancels PiP
            if (!(err && err.name === 'NotAllowedError')) {
                console.error('PiP error:', err);
            }
        }
    }

    // Fullscreen
    async toggleFullscreen() {
        try {
            if (document.fullscreenElement || document.webkitFullscreenElement) {
                if (document.exitFullscreen) {
                    await document.exitFullscreen();
                } else if (document.webkitExitFullscreen) {
                    document.webkitExitFullscreen();
                }
                // Unlock orientation
                if (screen.orientation && screen.orientation.unlock) {
                    screen.orientation.unlock();
                }
            } else {
                if (this.container.requestFullscreen) {
                    await this.container.requestFullscreen();
                } else if (this.container.webkitRequestFullscreen) {
                    this.container.webkitRequestFullscreen();
                }
                // Lock to landscape
                if (screen.orientation && screen.orientation.lock) {
                    try {
                        await screen.orientation.lock('landscape');
                    } catch (e) {
                        // Orientation lock not supported or denied
                    }
                }
            }
        } catch (err) {
            // Filter out expected errors (user cancellation, permission denied, etc.)
            const expectedErrorNames = ['AbortError', 'NotAllowedError', 'SecurityError'];
            if (!(err && expectedErrorNames.includes(err.name))) {
                console.error('Fullscreen error:', err);
            }
        }
    }

    updateFullscreenButton() {
        const isFullscreen = !!(document.fullscreenElement || document.webkitFullscreenElement);
        this.fullscreenBtn.classList.toggle('is-fullscreen', isFullscreen);
        this.container.classList.toggle('is-fullscreen', isFullscreen);
        this.fullscreenBtn.setAttribute('aria-label', isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen');
        this.fullscreenBtn.setAttribute('aria-pressed', isFullscreen.toString());
    }

    // Loading state
    showLoading() {
        this.loadingSpinner.classList.add('visible');
    }

    hideLoading() {
        this.loadingSpinner.classList.remove('visible');
    }

    // Controls visibility
    showControls() {
        if (this.isInPiP) return;

        this.controlsVisible = true;
        this.controlBar.classList.add('visible');
        this.gestureOverlay.classList.add('controls-visible');

        // Reset hide timeout
        clearTimeout(this.hideControlsTimeout);
        if (!this.video.paused) {
            this.hideControlsTimeout = setTimeout(() => {
                this.hideControls();
            }, this.options.hideControlsDelay);
        }
    }

    hideControls() {
        if (this.video.paused || this.isSeeking) return;

        this.controlsVisible = false;
        this.controlBar.classList.remove('visible');
        this.gestureOverlay.classList.remove('controls-visible');
    }

    // Touch gesture handling
    handleTouchStart(e) {
        if (e.touches.length !== 1) return;

        const touch = e.touches[0];
        this.touchStartX = touch.clientX;
        this.touchStartY = touch.clientY;
        this.touchStartTime = Date.now();
        this.isGesturing = false;
        this.gestureType = null;
    }

    handleTouchMove(e) {
        if (e.touches.length !== 1) return;

        const touch = e.touches[0];
        const deltaX = touch.clientX - this.touchStartX;
        const deltaY = touch.clientY - this.touchStartY;
        const absDeltaX = Math.abs(deltaX);
        const absDeltaY = Math.abs(deltaY);

        // Determine gesture type if not already gesturing
        if (!this.isGesturing && (absDeltaX > this.options.swipeThreshold || absDeltaY > this.options.swipeThreshold)) {
            this.isGesturing = true;

            if (absDeltaX > absDeltaY) {
                // Horizontal swipe - seeking
                this.gestureType = 'seek';
                this.gestureStartValue = this.video.currentTime;
            } else {
                // Vertical swipe - volume or brightness
                const rect = this.gestureOverlay.getBoundingClientRect();
                const relativeX = (this.touchStartX - rect.left) / rect.width;

                if (relativeX < 0.5) {
                    this.gestureType = 'brightness';
                    this.gestureStartValue = this.brightness;
                } else {
                    this.gestureType = 'volume';
                    this.gestureStartValue = this.video.volume;
                }
            }
        }

        if (this.isGesturing) {
            e.preventDefault();

            if (this.gestureType === 'seek') {
                this.handleSeekGesture(deltaX);
            } else if (this.gestureType === 'volume') {
                this.handleVolumeGesture(deltaY);
            } else if (this.gestureType === 'brightness') {
                this.handleBrightnessGesture(deltaY);
            }
        }
    }

    handleTouchEnd(e) {
        const touchDuration = Date.now() - this.touchStartTime;

        if (!this.isGesturing && touchDuration < this.options.doubleTapDelay) {
            // This was a tap, not a swipe
            this.handleTap(e);
        }

        // Hide adjustment indicators
        this.adjustmentIndicator.classList.remove('visible');
        this.seekPreview.classList.remove('visible');

        this.isGesturing = false;
        this.gestureType = null;
    }

    handleSeekGesture(deltaX) {
        // Calculate seek amount using configurable sensitivity
        const seekAmount = deltaX / this.options.seekPixelsPerSecond;
        let newTime;
        if (Number.isFinite(this.video.duration)) {
            newTime = Math.max(0, Math.min(this.video.duration, this.gestureStartValue + seekAmount));
        } else {
            // If duration is not available, do not seek
            newTime = this.gestureStartValue;
        }

        // Update seek preview
        this.seekPreview.textContent = this.formatTime(newTime);
        this.seekPreview.classList.add('visible');

        // Apply seek
        this.video.currentTime = newTime;
    }

    handleVolumeGesture(deltaY) {
        // Invert deltaY (swipe up = increase)
        const volumeChange = -deltaY / this.options.verticalSwipeSensitivity;
        const newVolume = Math.max(0, Math.min(1, this.gestureStartValue + volumeChange));

        this.setVolume(newVolume);
        this.showAdjustmentIndicator('volume', newVolume);
    }

    handleBrightnessGesture(deltaY) {
        // Invert deltaY (swipe up = increase)
        const brightnessChange = -deltaY / this.options.verticalSwipeSensitivity;
        const newBrightness = Math.max(this.options.brightnessMin,
            Math.min(this.options.brightnessMax, this.gestureStartValue + brightnessChange));

        this.brightness = newBrightness;
        this.video.style.filter = `brightness(${newBrightness})`;
        this.showAdjustmentIndicator('brightness', (newBrightness - this.options.brightnessMin) /
            (this.options.brightnessMax - this.options.brightnessMin));
    }

    showAdjustmentIndicator(type, value) {
        const icon = this.adjustmentIndicator.querySelector('.adjustment-icon');
        const fill = this.adjustmentIndicator.querySelector('.adjustment-fill');

        if (type === 'volume') {
            icon.innerHTML = value === 0
                ? '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>'
                : '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>';
        } else {
            icon.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 8.69V4h-4.69L12 .69 8.69 4H4v4.69L.69 12 4 15.31V20h4.69L12 23.31 15.31 20H20v-4.69L23.31 12 20 8.69zM12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6 6 2.69 6 6-2.69 6-6 6zm0-10c-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4-1.79-4-4-4z"/></svg>';
        }

        fill.style.height = `${value * 100}%`;
        this.adjustmentIndicator.classList.add('visible');
    }

    handleTap(e) {
        // Clear any pending tap timeout to prevent race conditions
        clearTimeout(this.tapTimeout);

        const now = Date.now();
        const touch = e.changedTouches ? e.changedTouches[0] : e;
        const tapX = touch.clientX;

        // Check for double tap
        if (now - this.lastTapTime < this.options.doubleTapDelay &&
            Math.abs(tapX - this.lastTapX) < this.options.doubleTapDistanceThreshold) {
            // Double tap
            this.handleDoubleTap(tapX);
            this.lastTapTime = 0;
        } else {
            // Potential single tap - wait to see if double tap
            this.lastTapTime = now;
            this.lastTapX = tapX;

            this.tapTimeout = setTimeout(() => {
                if (this.controlsVisible) {
                    this.hideControls();
                } else {
                    this.showControls();
                }
            }, this.options.doubleTapDelay);
        }
    }

    handleDoubleTap(tapX) {
        const rect = this.gestureOverlay.getBoundingClientRect();
        const relativeX = (tapX - rect.left) / rect.width;

        if (relativeX < this.options.leftZoneEnd) {
            // Left zone - skip back
            this.skip(-this.options.skipSeconds);
            this.showSkipIndicator('left');
        } else if (relativeX > this.options.rightZoneStart) {
            // Right zone - skip forward
            this.skip(this.options.skipSeconds);
            this.showSkipIndicator('right');
        } else {
            // Center - toggle play/pause
            this.togglePlayPause();
            this.showCenterIndicator();
        }
    }

    skip(seconds) {
        if (Number.isFinite(this.video.duration)) {
            this.video.currentTime = Math.max(0, Math.min(this.video.duration, this.video.currentTime + seconds));
        } else {
            // If duration is not finite, just add seconds but clamp to zero
            this.video.currentTime = Math.max(0, this.video.currentTime + seconds);
        }
    }

    showSkipIndicator(side) {
        const indicator = side === 'left' ? this.skipIndicatorLeft : this.skipIndicatorRight;
        indicator.classList.add('visible');
        setTimeout(() => {
            indicator.classList.remove('visible');
        }, this.options.indicatorDisplayDuration);
    }

    showCenterIndicator() {
        this.centerPlayIndicator.classList.toggle('show-play', this.video.paused);
        this.centerPlayIndicator.classList.toggle('show-pause', !this.video.paused);
        this.centerPlayIndicator.classList.add('visible');
        setTimeout(() => {
            this.centerPlayIndicator.classList.remove('visible');
        }, this.options.indicatorDisplayDuration);
    }

    // Mouse click handling (desktop)
    handleClick(e) {
        // Single click toggles controls on desktop
        if (this.controlsVisible) {
            this.hideControls();
        } else {
            this.showControls();
        }
    }

    handleDoubleClick(e) {
        const rect = this.gestureOverlay.getBoundingClientRect();
        const relativeX = (e.clientX - rect.left) / rect.width;

        if (relativeX < this.options.leftZoneEnd) {
            this.skip(-this.options.skipSeconds);
            this.showSkipIndicator('left');
        } else if (relativeX > this.options.rightZoneStart) {
            this.skip(this.options.skipSeconds);
            this.showSkipIndicator('right');
        } else {
            this.toggleFullscreen();
        }
    }

    // Keyboard controls
    handleKeyboard(e) {
        // Only handle if video is in viewport and no input is focused
        if (document.activeElement.tagName === 'INPUT' ||
            document.activeElement.tagName === 'TEXTAREA') {
            return;
        }

        // Handle Escape key for closing modals and exiting theater mode
        if (e.key === 'Escape') {
            if (this.shortcutsModal.classList.contains('visible')) {
                this.hideShortcutsModal();
                e.preventDefault();
                return;
            }
            if (this.shareModal.classList.contains('visible')) {
                this.hideShareModal();
                e.preventDefault();
                return;
            }
            if (this.speedModal.classList.contains('visible')) {
                this.hideSpeedModal();
                e.preventDefault();
                return;
            }
            if (this.qualityModal.classList.contains('visible')) {
                this.hideQualityModal();
                e.preventDefault();
                return;
            }
            // Exit theater mode on Escape (after modals are closed)
            if (this.theaterMode) {
                this.toggleTheaterMode();
                e.preventDefault();
                return;
            }
        }

        // Handle number keys for seeking to percentage (0-9)
        if (e.key >= '0' && e.key <= '9' && !e.shiftKey && !e.ctrlKey && !e.altKey) {
            e.preventDefault();
            const percent = parseInt(e.key, 10) / 10;
            if (Number.isFinite(this.video.duration)) {
                this.video.currentTime = this.video.duration * percent;
            }
            return;
        }

        // Handle Shift+< and Shift+> for speed control
        if (e.shiftKey && e.key === '<') {
            e.preventDefault();
            this.decreaseSpeed();
            return;
        }
        if (e.shiftKey && e.key === '>') {
            e.preventDefault();
            this.increaseSpeed();
            return;
        }

        switch (e.key) {
            case ' ':
            case 'k':
                e.preventDefault();
                this.togglePlayPause();
                break;
            case 'ArrowLeft':
                e.preventDefault();
                this.skip(-this.options.skipSeconds);
                break;
            case 'ArrowRight':
                e.preventDefault();
                this.skip(this.options.skipSeconds);
                break;
            case 'ArrowUp':
                e.preventDefault();
                this.setVolume(this.video.volume + 0.1);
                break;
            case 'ArrowDown':
                e.preventDefault();
                this.setVolume(this.video.volume - 0.1);
                break;
            case 'f':
                e.preventDefault();
                this.toggleFullscreen();
                break;
            case 'm':
                e.preventDefault();
                this.toggleMute();
                break;
            case 'c':
                e.preventDefault();
                this.onCaptionsToggle();
                break;
            case 't':
                e.preventDefault();
                this.toggleTheaterMode();
                break;
            case 'p':
                e.preventDefault();
                this.togglePiP();
                break;
            case '?':
                e.preventDefault();
                this.showShortcutsModal();
                break;
        }
    }

    // Cleanup
    destroy() {
        clearTimeout(this.hideControlsTimeout);
        clearTimeout(this.tapTimeout);

        // Exit theater mode if active to clean up body styles
        if (this.theaterMode) {
            this.theaterMode = false;
            this.container.classList.remove('theater-mode');
            document.body.classList.remove('player-theater-active');
        }

        // Remove focus trap if active
        this._removeFocusTrap();

        // Remove video event listeners
        this.video.removeEventListener('play', this._boundHandlers.onPlay);
        this.video.removeEventListener('pause', this._boundHandlers.onPause);
        this.video.removeEventListener('timeupdate', this._boundHandlers.onTimeUpdate);
        this.video.removeEventListener('progress', this._boundHandlers.onProgress);
        this.video.removeEventListener('loadedmetadata', this._boundHandlers.onLoadedMetadata);
        this.video.removeEventListener('durationchange', this._boundHandlers.onDurationChange);
        this.video.removeEventListener('waiting', this._boundHandlers.onWaiting);
        this.video.removeEventListener('canplay', this._boundHandlers.onCanPlay);
        this.video.removeEventListener('playing', this._boundHandlers.onPlaying);
        this.video.removeEventListener('volumechange', this._boundHandlers.onVolumeChange);
        this.video.removeEventListener('ended', this._boundHandlers.onEnded);
        this.video.removeEventListener('enterpictureinpicture', this._boundHandlers.onEnterPiP);
        this.video.removeEventListener('leavepictureinpicture', this._boundHandlers.onLeavePiP);

        // Remove document event listeners
        document.removeEventListener('fullscreenchange', this._boundHandlers.onFullscreenChange);
        document.removeEventListener('webkitfullscreenchange', this._boundHandlers.onFullscreenChange);

        // Remove container event listener
        this.container.removeEventListener('keydown', this._boundHandlers.onKeyDown);

        // Remove volume slider event listeners
        if (this.volumeSlider) {
            this.volumeSlider.removeEventListener('mousedown', this._boundHandlers.onVolumeSliderMouseDown);
            this.volumeSlider.removeEventListener('touchstart', this._boundHandlers.onVolumeSliderTouchStart);
            this.volumeSlider.removeEventListener('keydown', this._boundHandlers.onVolumeSliderKeyDown);
        }

        // Remove created DOM elements
        if (this.gestureOverlay && this.gestureOverlay.parentNode) {
            this.gestureOverlay.parentNode.removeChild(this.gestureOverlay);
        }
        if (this.skipIndicatorLeft && this.skipIndicatorLeft.parentNode) {
            this.skipIndicatorLeft.parentNode.removeChild(this.skipIndicatorLeft);
        }
        if (this.skipIndicatorRight && this.skipIndicatorRight.parentNode) {
            this.skipIndicatorRight.parentNode.removeChild(this.skipIndicatorRight);
        }
        if (this.centerPlayIndicator && this.centerPlayIndicator.parentNode) {
            this.centerPlayIndicator.parentNode.removeChild(this.centerPlayIndicator);
        }
        if (this.adjustmentIndicator && this.adjustmentIndicator.parentNode) {
            this.adjustmentIndicator.parentNode.removeChild(this.adjustmentIndicator);
        }
        if (this.seekPreview && this.seekPreview.parentNode) {
            this.seekPreview.parentNode.removeChild(this.seekPreview);
        }
        if (this.loadingSpinner && this.loadingSpinner.parentNode) {
            this.loadingSpinner.parentNode.removeChild(this.loadingSpinner);
        }
        if (this.controlBar && this.controlBar.parentNode) {
            this.controlBar.parentNode.removeChild(this.controlBar);
        }
        if (this.qualityModal && this.qualityModal.parentNode) {
            this.qualityModal.parentNode.removeChild(this.qualityModal);
        }
        if (this.speedModal && this.speedModal.parentNode) {
            this.speedModal.parentNode.removeChild(this.speedModal);
        }
        if (this.shortcutsModal && this.shortcutsModal.parentNode) {
            this.shortcutsModal.parentNode.removeChild(this.shortcutsModal);
        }
        if (this.liveRegion && this.liveRegion.parentNode) {
            this.liveRegion.parentNode.removeChild(this.liveRegion);
        }

        // Clear bound handlers reference
        this._boundHandlers = {};
    }
}

// Export for use
window.VLogPlayerControls = VLogPlayerControls;
