/**
 * Shared utility functions for VLog frontend
 */
window.VLogUtils = {
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
