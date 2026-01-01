/**
 * Shared utility functions for VLog frontend
 * Issue #413 Phase 3: Enhanced with resilient storage, watch later, and preferences
 */
window.VLogUtils = {
    /**
     * Default timeout for API requests in milliseconds
     */
    DEFAULT_TIMEOUT: 10000,

    /**
     * Schema version for localStorage data migrations
     */
    SCHEMA_VERSION: 1,

    /**
     * Watch progress thresholds for "Continue Watching" feature
     * Videos below STARTED are considered "barely started" and excluded
     * Videos above FINISHED are considered "essentially complete" and excluded
     */
    WATCH_PROGRESS_STARTED: 5,    // Skip videos barely started (< 5%)
    WATCH_PROGRESS_FINISHED: 95,  // Skip videos essentially complete (> 95%)

    /**
     * Fetch with timeout support using AbortController
     * @param {string} url - URL to fetch
     * @param {Object} options - Fetch options (method, headers, body, etc.)
     * @param {number} timeoutMs - Timeout in milliseconds (default: 10000)
     * @returns {Promise<Response>} Fetch response
     * @throws {Error} Throws 'Request timed out' on timeout
     */
    async fetchWithTimeout(url, options = {}, timeoutMs = 10000) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal
            });
            clearTimeout(timeoutId);
            return response;
        } catch (e) {
            clearTimeout(timeoutId);
            if (e.name === 'AbortError') {
                throw new Error('Request timed out');
            }
            throw e;
        }
    },

    /**
     * Format seconds into human-readable duration (H:MM:SS or M:SS)
     * @param {number} seconds - Duration in seconds
     * @param {string} fallback - Return value when seconds is falsy (default: '0:00')
     * @returns {string} Formatted duration string
     */
    formatDuration(seconds, fallback = '0:00') {
        if (!seconds) return fallback;
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        const s = Math.floor(seconds % 60);
        if (h > 0) {
            return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }
        return `${m}:${s.toString().padStart(2, '0')}`;
    },

    /**
     * Format date string for display
     * @param {string} dateStr - ISO date string
     * @param {string} monthFormat - 'short' (Dec) or 'long' (December)
     * @returns {string} Formatted date string
     */
    formatDate(dateStr, monthFormat = 'short') {
        if (!dateStr) return '';
        return new Date(dateStr).toLocaleDateString('en-US', {
            year: 'numeric',
            month: monthFormat,
            day: 'numeric'
        });
    },

    /**
     * Format view count for display (e.g., "1.2K views", "50 views")
     * @param {number} count - View count
     * @returns {string} Formatted view count string
     */
    formatViewCount(count) {
        if (!count || count <= 0) return '';
        if (count >= 1000000) {
            return (count / 1000000).toFixed(1).replace(/\.0$/, '') + 'M views';
        }
        if (count >= 1000) {
            return (count / 1000).toFixed(1).replace(/\.0$/, '') + 'K views';
        }
        return count + (count === 1 ? ' view' : ' views');
    },

    /**
     * Resilient localStorage wrapper with caching and error handling
     * Handles: disabled storage, quota errors, schema versioning
     */
    storage: {
        _cache: {},
        _cacheTime: {},
        CACHE_TTL: 5000, // 5 seconds

        /**
         * Check if localStorage is available
         * @returns {boolean} True if localStorage is available
         */
        isAvailable() {
            try {
                const test = '__vlog_storage_test__';
                localStorage.setItem(test, test);
                localStorage.removeItem(test);
                return true;
            } catch (e) {
                return false;
            }
        },

        /**
         * Safely get a value from localStorage with caching
         * @param {string} key - Storage key
         * @param {*} defaultValue - Default value if key doesn't exist
         * @returns {*} Parsed value or default
         */
        safeGet(key, defaultValue = null) {
            // Check cache first
            const now = Date.now();
            if (this._cache[key] !== undefined && (now - this._cacheTime[key]) < this.CACHE_TTL) {
                return this._cache[key];
            }

            if (!this.isAvailable()) {
                return defaultValue;
            }

            try {
                const raw = localStorage.getItem(key);
                if (raw === null) {
                    return defaultValue;
                }
                const parsed = JSON.parse(raw);
                // Update cache
                this._cache[key] = parsed;
                this._cacheTime[key] = now;
                return parsed;
            } catch (e) {
                console.warn(`Failed to read localStorage key "${key}":`, e);
                return defaultValue;
            }
        },

        /**
         * Safely set a value in localStorage with quota error handling
         * @param {string} key - Storage key
         * @param {*} value - Value to store (will be JSON stringified)
         * @returns {boolean} True if successful
         */
        safeSet(key, value) {
            if (!this.isAvailable()) {
                return false;
            }

            try {
                localStorage.setItem(key, JSON.stringify(value));
                // Update cache
                this._cache[key] = value;
                this._cacheTime[key] = Date.now();
                return true;
            } catch (e) {
                if (e.name === 'QuotaExceededError' || e.code === 22) {
                    console.warn('localStorage quota exceeded, clearing old data');
                    // Try to clear some old data and retry
                    this._evictOldest();
                    try {
                        localStorage.setItem(key, JSON.stringify(value));
                        this._cache[key] = value;
                        this._cacheTime[key] = Date.now();
                        return true;
                    } catch (e2) {
                        console.error('Failed to save to localStorage after eviction:', e2);
                        return false;
                    }
                }
                console.warn(`Failed to write localStorage key "${key}":`, e);
                return false;
            }
        },

        /**
         * Remove a key from localStorage
         * @param {string} key - Storage key
         */
        remove(key) {
            if (!this.isAvailable()) return;
            try {
                localStorage.removeItem(key);
                delete this._cache[key];
                delete this._cacheTime[key];
            } catch (e) {
                console.warn(`Failed to remove localStorage key "${key}":`, e);
            }
        },

        /**
         * Clear cache (forces fresh reads from localStorage)
         */
        clearCache() {
            this._cache = {};
            this._cacheTime = {};
        },

        /**
         * Evict oldest vlog entries when storage is full
         */
        _evictOldest() {
            const vlogKeys = [];
            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && key.startsWith('vlog_')) {
                    try {
                        const data = JSON.parse(localStorage.getItem(key));
                        if (data && data.timestamp) {
                            vlogKeys.push({ key, timestamp: data.timestamp });
                        }
                    } catch (e) {
                        // Skip malformed entries
                    }
                }
            }
            // Sort by timestamp and remove oldest 10%
            vlogKeys.sort((a, b) => a.timestamp - b.timestamp);
            const toRemove = Math.max(1, Math.floor(vlogKeys.length * 0.1));
            for (let i = 0; i < toRemove; i++) {
                localStorage.removeItem(vlogKeys[i].key);
            }
        }
    },

    /**
     * Watch history utility for tracking video playback position
     * Stores position data in localStorage with automatic pruning
     */
    watchHistory: {
        STORAGE_KEY: 'vlog_watch_history',
        MAX_ENTRIES: 50,

        /**
         * Save watch position for a video
         * @param {number} videoId - The video ID
         * @param {number} position - Current playback position in seconds
         * @param {number} duration - Total video duration in seconds
         */
        save(videoId, position, duration) {
            const history = this.getAll();
            // Clamp values to valid ranges
            const safePosition = Math.max(0, position);
            const safeDuration = Math.max(0, duration);
            const rawPercentage = safeDuration > 0 ? (safePosition / safeDuration) * 100 : 0;
            const percentage = Math.min(100, Math.max(0, rawPercentage));

            history[videoId] = {
                position: safePosition,
                duration: safeDuration,
                percentage,
                timestamp: Date.now()
            };
            // Prune old entries if exceeding max
            const entries = Object.entries(history);
            if (entries.length > this.MAX_ENTRIES) {
                entries.sort((a, b) => b[1].timestamp - a[1].timestamp);
                VLogUtils.storage.safeSet(this.STORAGE_KEY,
                    Object.fromEntries(entries.slice(0, this.MAX_ENTRIES)));
            } else {
                VLogUtils.storage.safeSet(this.STORAGE_KEY, history);
            }
        },

        /**
         * Get watch position for a specific video
         * @param {number} videoId - The video ID
         * @returns {Object|null} Position data or null if not found
         */
        get(videoId) {
            return this.getAll()[videoId] || null;
        },

        /**
         * Get all watch history entries
         * @returns {Object} Map of videoId to position data
         */
        getAll() {
            return VLogUtils.storage.safeGet(this.STORAGE_KEY, {});
        },

        /**
         * Get videos for "Continue Watching" row
         * Filters to videos with progress between STARTED and FINISHED thresholds
         * @param {number} limit - Max number of videos to return (default: 10)
         * @returns {Array<{videoId: number, position: number, duration: number, percentage: number}>}
         */
        getContinueWatching(limit = 10) {
            const history = this.getAll();
            const minProgress = VLogUtils.WATCH_PROGRESS_STARTED;
            const maxProgress = VLogUtils.WATCH_PROGRESS_FINISHED;

            return Object.entries(history)
                .filter(([videoId, data]) =>
                    data.percentage > minProgress &&
                    data.percentage < maxProgress &&
                    Number.isInteger(parseInt(videoId, 10)) &&
                    parseInt(videoId, 10) > 0
                )
                .sort((a, b) => b[1].timestamp - a[1].timestamp)
                .slice(0, limit)
                .map(([videoId, data]) => ({
                    videoId: parseInt(videoId, 10),
                    position: data.position,
                    duration: data.duration,
                    percentage: data.percentage
                }));
        },

        /**
         * Get progress map for displaying progress bars on video cards
         * @returns {Object} Map of videoId -> percentage for videos with active progress
         */
        getProgressMap() {
            const history = this.getAll();
            const minProgress = VLogUtils.WATCH_PROGRESS_STARTED;
            const maxProgress = VLogUtils.WATCH_PROGRESS_FINISHED;
            const progressMap = {};

            for (const [videoId, data] of Object.entries(history)) {
                if (data.percentage > minProgress && data.percentage < maxProgress) {
                    progressMap[videoId] = data.percentage;
                }
            }

            return progressMap;
        },

        /**
         * Clear watch history for a specific video
         * @param {number} videoId - The video ID to clear
         */
        clear(videoId) {
            const history = this.getAll();
            delete history[videoId];
            VLogUtils.storage.safeSet(this.STORAGE_KEY, history);
        },

        /**
         * Remove stale video IDs that no longer exist
         * @param {Array<number>} validIds - Array of valid video IDs
         */
        cleanupStale(validIds) {
            const history = this.getAll();
            const validSet = new Set(validIds);
            let changed = false;
            for (const videoId of Object.keys(history)) {
                if (!validSet.has(parseInt(videoId, 10))) {
                    delete history[videoId];
                    changed = true;
                }
            }
            if (changed) {
                VLogUtils.storage.safeSet(this.STORAGE_KEY, history);
            }
        }
    },

    /**
     * Watch Later queue for saving videos to watch later
     * Stores video IDs in localStorage with bounded size
     */
    watchLater: {
        STORAGE_KEY: 'vlog_watch_later',
        MAX_ENTRIES: 100,

        /**
         * Add a video to watch later
         * @param {number} videoId - The video ID
         * @returns {boolean} True if added (false if already exists or failed)
         */
        add(videoId) {
            const queue = this.getAll();
            if (queue.some(item => item.videoId === videoId)) {
                return false; // Already exists
            }
            queue.unshift({ videoId, addedAt: Date.now() });
            // Enforce max size
            if (queue.length > this.MAX_ENTRIES) {
                queue.pop();
            }
            return VLogUtils.storage.safeSet(this.STORAGE_KEY, queue);
        },

        /**
         * Remove a video from watch later
         * @param {number} videoId - The video ID
         * @returns {boolean} True if removed
         */
        remove(videoId) {
            const queue = this.getAll();
            const newQueue = queue.filter(item => item.videoId !== videoId);
            if (newQueue.length !== queue.length) {
                return VLogUtils.storage.safeSet(this.STORAGE_KEY, newQueue);
            }
            return false;
        },

        /**
         * Check if a video is in watch later
         * @param {number} videoId - The video ID
         * @returns {boolean} True if in queue
         */
        has(videoId) {
            return this.getAll().some(item => item.videoId === videoId);
        },

        /**
         * Get all watch later video IDs
         * @returns {Array<{videoId: number, addedAt: number}>} Array of queue items
         */
        getAll() {
            return VLogUtils.storage.safeGet(this.STORAGE_KEY, []);
        },

        /**
         * Get just the video IDs (for API bulk fetch)
         * @returns {Array<number>} Array of video IDs
         */
        getVideoIds() {
            return this.getAll().map(item => item.videoId);
        },

        /**
         * Toggle a video in watch later
         * @param {number} videoId - The video ID
         * @returns {{inQueue: boolean, success: boolean}} Status object indicating new state and write success
         */
        toggle(videoId) {
            if (this.has(videoId)) {
                const success = this.remove(videoId);
                return { inQueue: false, success };
            } else {
                const success = this.add(videoId);
                return { inQueue: success, success }; // Only in queue if add succeeded
            }
        },

        /**
         * Remove stale video IDs that no longer exist
         * @param {Array<number>} validIds - Array of valid video IDs
         */
        cleanupStale(validIds) {
            const queue = this.getAll();
            const validSet = new Set(validIds);
            const newQueue = queue.filter(item => validSet.has(item.videoId));
            if (newQueue.length !== queue.length) {
                VLogUtils.storage.safeSet(this.STORAGE_KEY, newQueue);
            }
        }
    },

    /**
     * User preferences utility for persisting UI settings
     * Stores preferences in localStorage
     */
    preferences: {
        STORAGE_KEY: 'vlog_preferences',

        /**
         * Get a preference value
         * @param {string} key - Preference key
         * @param {*} defaultValue - Default value if not set
         * @returns {*} Preference value
         */
        get(key, defaultValue = null) {
            const prefs = VLogUtils.storage.safeGet(this.STORAGE_KEY, {});
            return prefs.hasOwnProperty(key) ? prefs[key] : defaultValue;
        },

        /**
         * Set a preference value
         * @param {string} key - Preference key
         * @param {*} value - Value to set
         * @returns {boolean} True if successful
         */
        set(key, value) {
            const prefs = VLogUtils.storage.safeGet(this.STORAGE_KEY, {});
            prefs[key] = value;
            return VLogUtils.storage.safeSet(this.STORAGE_KEY, prefs);
        },

        /**
         * Get all preferences
         * @returns {Object} All preferences
         */
        getAll() {
            return VLogUtils.storage.safeGet(this.STORAGE_KEY, {});
        },

        /**
         * Clear a specific preference
         * @param {string} key - Preference key to clear
         */
        clear(key) {
            const prefs = VLogUtils.storage.safeGet(this.STORAGE_KEY, {});
            delete prefs[key];
            VLogUtils.storage.safeSet(this.STORAGE_KEY, prefs);
        },

        /**
         * Reset all preferences to defaults
         */
        reset() {
            VLogUtils.storage.safeSet(this.STORAGE_KEY, {});
        }
    }
};
