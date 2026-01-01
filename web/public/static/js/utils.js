/**
 * Shared utility functions for VLog frontend
 */
window.VLogUtils = {
    /**
     * Default timeout for API requests in milliseconds
     */
    DEFAULT_TIMEOUT: 10000,

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
            try {
                const history = this.getAll();
                history[videoId] = {
                    position,
                    duration,
                    percentage: duration > 0 ? (position / duration) * 100 : 0,
                    timestamp: Date.now()
                };
                // Prune old entries if exceeding max
                const entries = Object.entries(history);
                if (entries.length > this.MAX_ENTRIES) {
                    entries.sort((a, b) => b[1].timestamp - a[1].timestamp);
                    localStorage.setItem(this.STORAGE_KEY,
                        JSON.stringify(Object.fromEntries(entries.slice(0, this.MAX_ENTRIES))));
                } else {
                    localStorage.setItem(this.STORAGE_KEY, JSON.stringify(history));
                }
            } catch (e) {
                console.warn('Failed to save watch history:', e);
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
            try {
                return JSON.parse(localStorage.getItem(this.STORAGE_KEY)) || {};
            } catch (e) {
                return {};
            }
        },

        /**
         * Clear watch history for a specific video
         * @param {number} videoId - The video ID to clear
         */
        clear(videoId) {
            try {
                const history = this.getAll();
                delete history[videoId];
                localStorage.setItem(this.STORAGE_KEY, JSON.stringify(history));
            } catch (e) {
                console.warn('Failed to clear watch history:', e);
            }
        }
    }
};
