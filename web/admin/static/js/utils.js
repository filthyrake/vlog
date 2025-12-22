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
    }
};
