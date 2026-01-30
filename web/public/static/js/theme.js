/**
 * VLog Theme and Branding Loader
 * Issue #214: UI theme customization and branding
 *
 * Loads theme configuration from API and applies CSS variable overrides
 * for colors, and provides site branding data for Alpine.js components.
 */
window.VLogTheme = {
    /**
     * Theme configuration cache
     */
    config: null,

    /**
     * Default theme values (used before API response)
     */
    defaults: {
        site_name: 'VLog',
        logo_path: null,
        favicon_path: null,
        footer_text: null,
        footer_links: [],
        primary_color: '#3B82F6',
        secondary_color: '#1E40AF',
        accent_color: '#60A5FA',
        mode: 'auto',
        custom_css: null,
        homepage_style: 'grid',
        videos_per_page: 24,
        grid_columns: 4,
        show_sidebar: true,
        show_related_videos: true
    },

    /**
     * Fetch timeout in milliseconds
     */
    fetchTimeout: 5000,

    /**
     * Fetch theme configuration from API
     * @returns {Promise<Object>} Theme configuration
     */
    async fetchConfig() {
        try {
            // Use AbortController for fetch timeout
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), this.fetchTimeout);

            const response = await fetch('/api/v1/config/theme', {
                signal: controller.signal
            });

            clearTimeout(timeoutId);

            if (!response.ok) {
                console.warn('Failed to fetch theme config, using defaults');
                return this.defaults;
            }
            const config = await response.json();
            this.config = { ...this.defaults, ...config };
            return this.config;
        } catch (e) {
            if (e.name === 'AbortError') {
                console.warn('Theme config fetch timed out, using defaults');
            } else {
                console.warn('Error fetching theme config:', e);
            }
            this.config = this.defaults;
            return this.defaults;
        }
    },

    /**
     * Apply theme colors as CSS custom properties
     * @param {Object} config - Theme configuration
     */
    applyColors(config) {
        const root = document.documentElement;

        if (config.primary_color) {
            root.style.setProperty('--vlog-primary', config.primary_color);
            root.style.setProperty('--vlog-focus-ring-color', config.primary_color);
            root.style.setProperty('--vlog-player-progress', config.primary_color);
        }

        if (config.secondary_color) {
            root.style.setProperty('--vlog-primary-hover', config.secondary_color);
        }

        if (config.accent_color) {
            root.style.setProperty('--vlog-primary-light', config.accent_color);
        }

        // Handle dark mode preference
        if (config.mode === 'light') {
            root.classList.add('theme-light');
            root.classList.remove('theme-dark');
        } else if (config.mode === 'dark') {
            root.classList.add('theme-dark');
            root.classList.remove('theme-light');
        }
        // 'auto' uses system preference (default behavior)
    },

    /**
     * Apply custom CSS if provided
     * @param {Object} config - Theme configuration
     */
    applyCustomCSS(config) {
        if (!config.custom_css) return;

        // Remove any existing custom CSS
        const existing = document.getElementById('vlog-custom-css');
        if (existing) {
            existing.remove();
        }

        // Create and inject style element
        const style = document.createElement('style');
        style.id = 'vlog-custom-css';
        style.textContent = config.custom_css;
        document.head.appendChild(style);
    },

    /**
     * Update favicon if custom one is configured
     * @param {Object} config - Theme configuration
     */
    updateFavicon(config) {
        if (!config.favicon_path) return;

        // Find existing favicon or create new one
        let link = document.querySelector("link[rel*='icon']");
        if (!link) {
            link = document.createElement('link');
            link.rel = 'icon';
            document.head.appendChild(link);
        }
        link.href = '/api/v1/branding/favicon';
    },

    /**
     * Update page title with site name
     * @param {Object} config - Theme configuration
     * @param {string} pageTitle - Optional page-specific title
     */
    updateTitle(config, pageTitle = null) {
        const siteName = config.site_name || this.defaults.site_name;
        if (pageTitle) {
            document.title = `${pageTitle} - ${siteName}`;
        } else {
            document.title = siteName;
        }
    },

    /**
     * Initialize theme on page load
     * @param {string} pageTitle - Optional page-specific title
     * @returns {Promise<Object>} Theme configuration
     */
    async init(pageTitle = null) {
        const config = await this.fetchConfig();
        this.applyColors(config);
        this.applyCustomCSS(config);
        this.updateFavicon(config);
        this.updateTitle(config, pageTitle);
        return config;
    },

    /**
     * Get current theme config (synchronous, uses cached value)
     * @returns {Object} Theme configuration or defaults
     */
    getConfig() {
        return this.config || this.defaults;
    },

    /**
     * Get site name from config
     * @returns {string} Site name
     */
    getSiteName() {
        const config = this.getConfig();
        return config.site_name || this.defaults.site_name;
    },

    /**
     * Get logo URL if configured
     * @returns {string|null} Logo URL or null
     */
    getLogoUrl() {
        const config = this.getConfig();
        return config.logo_path ? '/api/v1/branding/logo' : null;
    },

    /**
     * Get footer text
     * @returns {string|null} Footer text or null
     */
    getFooterText() {
        const config = this.getConfig();
        return config.footer_text;
    },

    /**
     * Get footer links
     * @returns {Array} Footer links array
     */
    getFooterLinks() {
        const config = this.getConfig();
        return config.footer_links || [];
    },

    /**
     * Get homepage layout style
     * @returns {string} Layout style (grid, list, featured)
     */
    getHomepageStyle() {
        const config = this.getConfig();
        return config.homepage_style || 'grid';
    },

    /**
     * Get videos per page setting
     * @returns {number} Videos per page
     */
    getVideosPerPage() {
        const config = this.getConfig();
        return config.videos_per_page || 24;
    },

    /**
     * Get grid columns setting
     * @returns {number} Grid columns (desktop)
     */
    getGridColumns() {
        const config = this.getConfig();
        return config.grid_columns || 4;
    }
};
