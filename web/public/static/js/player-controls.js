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
            hideControlsDelay: 3000,
            doubleTapDelay: 300,
            swipeThreshold: 30,
            brightnessMin: 0.5,
            brightnessMax: 1.5,
            ...options
        };

        // State
        this.controlsVisible = true;
        this.hideControlsTimeout = null;
        this.isSeeking = false;
        this.isInPiP = false;
        this.brightness = 1.0;
        this.currentVolume = 1.0;

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
        this.updateTimeDisplay();
        this.showControls();
    }

    createControlsUI() {
        // Gesture overlay (captures touch events above video)
        this.gestureOverlay = document.createElement('div');
        this.gestureOverlay.className = 'player-gesture-overlay';
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
            <button class="player-btn play-pause-btn" title="Play/Pause">
                <svg viewBox="0 0 24 24" fill="currentColor" class="play-icon">
                    <path d="M8 5v14l11-7z"/>
                </svg>
                <svg viewBox="0 0 24 24" fill="currentColor" class="pause-icon">
                    <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
                </svg>
            </button>
            <div class="player-progress-container">
                <div class="player-progress-bar">
                    <div class="player-progress-buffered"></div>
                    <div class="player-progress-played"></div>
                    <div class="player-progress-thumb"></div>
                </div>
                <div class="player-progress-tooltip"></div>
            </div>
            <span class="player-time-display">0:00 / 0:00</span>
            <div class="player-controls-right">
                <button class="player-btn volume-btn" title="Volume">
                    <svg viewBox="0 0 24 24" fill="currentColor" class="volume-high">
                        <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
                    </svg>
                    <svg viewBox="0 0 24 24" fill="currentColor" class="volume-muted">
                        <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/>
                    </svg>
                </button>
                <button class="player-btn quality-btn" title="Quality">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-8 12H9.5v-2h-2v2H6V9h1.5v2.5h2V9H11v6zm7-1c0 .55-.45 1-1 1h-.75v1.5h-1.5V15H14c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1h3c.55 0 1 .45 1 1v4zm-3.5-.5h2v-3h-2v3z"/>
                    </svg>
                    <span class="quality-label">Auto</span>
                </button>
                <button class="player-btn captions-btn" title="Captions" style="display: none;">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M19 4H5c-1.11 0-2 .9-2 2v12c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-8 7H9.5v-.5h-2v3h2V13H11v1c0 .55-.45 1-1 1H7c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1h3c.55 0 1 .45 1 1v1zm7 0h-1.5v-.5h-2v3h2V13H18v1c0 .55-.45 1-1 1h-3c-.55 0-1-.45-1-1v-4c0-.55.45-1 1-1h3c.55 0 1 .45 1 1v1z"/>
                    </svg>
                </button>
                <button class="player-btn pip-btn" title="Picture in Picture" style="display: none;">
                    <svg viewBox="0 0 24 24" fill="currentColor">
                        <path d="M19 7h-8v6h8V7zm2-4H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H3V5h18v14z"/>
                    </svg>
                </button>
                <button class="player-btn fullscreen-btn" title="Fullscreen">
                    <svg viewBox="0 0 24 24" fill="currentColor" class="fullscreen-enter">
                        <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/>
                    </svg>
                    <svg viewBox="0 0 24 24" fill="currentColor" class="fullscreen-exit">
                        <path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/>
                    </svg>
                </button>
            </div>
        `;
        this.container.appendChild(this.controlBar);

        // Quality modal (for mobile)
        this.qualityModal = document.createElement('div');
        this.qualityModal.className = 'player-quality-modal';
        this.qualityModal.innerHTML = `
            <div class="quality-modal-backdrop"></div>
            <div class="quality-modal-content">
                <div class="quality-modal-header">Quality</div>
                <div class="quality-modal-options"></div>
            </div>
        `;
        this.container.appendChild(this.qualityModal);

        // Cache DOM references
        this.playPauseBtn = this.controlBar.querySelector('.play-pause-btn');
        this.progressContainer = this.controlBar.querySelector('.player-progress-container');
        this.progressBar = this.controlBar.querySelector('.player-progress-bar');
        this.progressBuffered = this.controlBar.querySelector('.player-progress-buffered');
        this.progressPlayed = this.controlBar.querySelector('.player-progress-played');
        this.progressThumb = this.controlBar.querySelector('.player-progress-thumb');
        this.progressTooltip = this.controlBar.querySelector('.player-progress-tooltip');
        this.timeDisplay = this.controlBar.querySelector('.player-time-display');
        this.volumeBtn = this.controlBar.querySelector('.volume-btn');
        this.qualityBtn = this.controlBar.querySelector('.quality-btn');
        this.qualityLabel = this.qualityBtn.querySelector('.quality-label');
        this.captionsBtn = this.controlBar.querySelector('.captions-btn');
        this.pipBtn = this.controlBar.querySelector('.pip-btn');
        this.fullscreenBtn = this.controlBar.querySelector('.fullscreen-btn');
        this.qualityModalOptions = this.qualityModal.querySelector('.quality-modal-options');
    }

    bindEvents() {
        // Video events
        this.video.addEventListener('play', () => this.updatePlayPauseButton());
        this.video.addEventListener('pause', () => this.updatePlayPauseButton());
        this.video.addEventListener('timeupdate', () => this.updateProgress());
        this.video.addEventListener('progress', () => this.updateBuffered());
        this.video.addEventListener('loadedmetadata', () => this.updateTimeDisplay());
        this.video.addEventListener('durationchange', () => this.updateTimeDisplay());
        this.video.addEventListener('waiting', () => this.showLoading());
        this.video.addEventListener('canplay', () => this.hideLoading());
        this.video.addEventListener('playing', () => this.hideLoading());
        this.video.addEventListener('volumechange', () => this.updateVolumeButton());
        this.video.addEventListener('ended', () => this.showControls());

        // PiP events
        this.video.addEventListener('enterpictureinpicture', () => {
            this.isInPiP = true;
            this.hideControls();
        });
        this.video.addEventListener('leavepictureinpicture', () => {
            this.isInPiP = false;
            this.showControls();
        });

        // Fullscreen events
        document.addEventListener('fullscreenchange', () => this.updateFullscreenButton());
        document.addEventListener('webkitfullscreenchange', () => this.updateFullscreenButton());

        // Control bar buttons
        this.playPauseBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.togglePlayPause();
        });
        this.volumeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleMute();
        });
        this.qualityBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.showQualityModal();
        });
        this.captionsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.onCaptionsToggle();
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

        // Quality modal
        this.qualityModal.querySelector('.quality-modal-backdrop').addEventListener('click', () => {
            this.hideQualityModal();
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

        // Keyboard controls
        document.addEventListener('keydown', (e) => this.handleKeyboard(e));

        // Check PiP support
        if (document.pictureInPictureEnabled && !this.video.disablePictureInPicture) {
            this.pipBtn.style.display = '';
        }
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

        const seekTime = percent * this.video.duration;
        this.video.currentTime = seekTime;

        this.progressPlayed.style.width = `${percent * 100}%`;
        this.progressThumb.style.left = `${percent * 100}%`;

        // Show tooltip
        this.progressTooltip.textContent = this.formatTime(seekTime);
        this.progressTooltip.style.left = `${percent * 100}%`;
        this.progressTooltip.classList.add('visible');
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
        this.volumeBtn.classList.toggle('muted', muted);
    }

    setVolume(value) {
        this.video.volume = Math.max(0, Math.min(1, value));
        this.video.muted = false;
        this.currentVolume = this.video.volume;
    }

    // Quality
    setQualities(levels, currentIndex = -1) {
        this.qualities = levels;
        this.currentQualityIndex = currentIndex;
        this.updateQualityLabel();
        this.updateQualityModal();
    }

    updateQualityLabel() {
        if (this.currentQualityIndex === -1) {
            this.qualityLabel.textContent = 'Auto';
        } else if (this.qualities && this.qualities[this.currentQualityIndex]) {
            this.qualityLabel.textContent = this.qualities[this.currentQualityIndex].height + 'p';
        }
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
            option.textContent = level.height + 'p';
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
        this.qualityModal.classList.add('visible');
    }

    hideQualityModal() {
        this.qualityModal.classList.remove('visible');
    }

    // Captions
    setCaptionsAvailable(available) {
        this.captionsBtn.style.display = available ? '' : 'none';
    }

    setCaptionsEnabled(enabled) {
        this.captionsBtn.classList.toggle('active', enabled);
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
            console.error('PiP error:', err);
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
            console.error('Fullscreen error:', err);
        }
    }

    updateFullscreenButton() {
        const isFullscreen = !!(document.fullscreenElement || document.webkitFullscreenElement);
        this.fullscreenBtn.classList.toggle('is-fullscreen', isFullscreen);
        this.container.classList.toggle('is-fullscreen', isFullscreen);
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

        if (!this.isGesturing && touchDuration < 300) {
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
        // Calculate seek amount (scale: 100px = 30 seconds)
        const seekAmount = (deltaX / 100) * 30;
        const newTime = Math.max(0, Math.min(this.video.duration, this.gestureStartValue + seekAmount));

        // Update seek preview
        this.seekPreview.textContent = this.formatTime(newTime);
        this.seekPreview.classList.add('visible');

        // Apply seek
        this.video.currentTime = newTime;
    }

    handleVolumeGesture(deltaY) {
        // Invert deltaY (swipe up = increase)
        const volumeChange = -deltaY / 150;
        const newVolume = Math.max(0, Math.min(1, this.gestureStartValue + volumeChange));

        this.setVolume(newVolume);
        this.showAdjustmentIndicator('volume', newVolume);
    }

    handleBrightnessGesture(deltaY) {
        // Invert deltaY (swipe up = increase)
        const brightnessChange = -deltaY / 150;
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
        const now = Date.now();
        const touch = e.changedTouches ? e.changedTouches[0] : e;
        const tapX = touch.clientX;

        // Check for double tap
        if (now - this.lastTapTime < this.options.doubleTapDelay &&
            Math.abs(tapX - this.lastTapX) < 50) {
            // Double tap
            clearTimeout(this.tapTimeout);
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

        if (relativeX < 0.33) {
            // Left third - skip back
            this.skip(-this.options.skipSeconds);
            this.showSkipIndicator('left');
        } else if (relativeX > 0.67) {
            // Right third - skip forward
            this.skip(this.options.skipSeconds);
            this.showSkipIndicator('right');
        } else {
            // Center - toggle play/pause
            this.togglePlayPause();
            this.showCenterIndicator();
        }
    }

    skip(seconds) {
        this.video.currentTime = Math.max(0, Math.min(this.video.duration, this.video.currentTime + seconds));
    }

    showSkipIndicator(side) {
        const indicator = side === 'left' ? this.skipIndicatorLeft : this.skipIndicatorRight;
        indicator.classList.add('visible');
        setTimeout(() => {
            indicator.classList.remove('visible');
        }, 500);
    }

    showCenterIndicator() {
        this.centerPlayIndicator.classList.toggle('show-play', this.video.paused);
        this.centerPlayIndicator.classList.toggle('show-pause', !this.video.paused);
        this.centerPlayIndicator.classList.add('visible');
        setTimeout(() => {
            this.centerPlayIndicator.classList.remove('visible');
        }, 500);
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

        if (relativeX < 0.33) {
            this.skip(-this.options.skipSeconds);
            this.showSkipIndicator('left');
        } else if (relativeX > 0.67) {
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
        }
    }

    // Cleanup
    destroy() {
        clearTimeout(this.hideControlsTimeout);
        clearTimeout(this.tapTimeout);
        // Remove event listeners would go here if needed
    }
}

// Export for use
window.VLogPlayerControls = VLogPlayerControls;
