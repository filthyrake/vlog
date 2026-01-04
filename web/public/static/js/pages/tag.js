/**
 * Tag page Alpine.js component
 * Handles tag video listing and search
 */

const MAX_SEARCH_LENGTH = 200;
const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function tagPage() {
    return {
        tag: null,
        videos: [],
        _filteredVideos: [],
        loading: true,
        error: null,
        announcement: '', // For screen reader announcements
        mobileNavOpen: false,
        previousFocus: null, // For focus restoration
        searchQuery: '',
        watchLaterIds: new Set(), // Track watch later IDs for UI state
        watchProgressMap: {}, // Map of videoId -> percentage watched
        // Display settings from API
        showViewCounts: true,
        showTagline: true,
        tagline: '',

        get resultCount() {
            return this._filteredVideos.length;
        },

        async init() {
            // Load display config
            this.loadDisplayConfig();

            // Watch for changes to searchQuery and videos to update cached filteredVideos
            this.$watch('searchQuery', () => this.updateFilteredVideos());
            this.$watch('videos', () => this.updateFilteredVideos());

            // Load watch later IDs from storage
            this.watchLaterIds = new Set(VLogUtils.watchLater.getVideoIds());

            // Load watch progress map for showing progress bars
            this.watchProgressMap = VLogUtils.watchHistory.getProgressMap();

            const slug = window.location.pathname.split('/').pop();
            if (!slug || !SLUG_PATTERN.test(slug)) {
                this.error = 'Invalid tag';
                this.loading = false;
                return;
            }

            try {
                const [tagRes, videosRes] = await Promise.all([
                    VLogUtils.fetchWithTimeout(`/api/tags/${encodeURIComponent(slug)}`, {}, 10000),
                    VLogUtils.fetchWithTimeout(`/api/videos?tag=${encodeURIComponent(slug)}`, {}, 10000)
                ]);

                if (!tagRes.ok) {
                    this.error = tagRes.status === 404 ? 'Tag not found' : 'Failed to load tag';
                    this.loading = false;
                    return;
                }

                this.tag = await tagRes.json();
                if (videosRes.ok) {
                    const data = await videosRes.json();
                    this.videos = data.videos || [];
                    this.announcement = `Tag ${this.tag.name} with ${this.videos.length} video${this.videos.length === 1 ? '' : 's'}`;
                } else {
                    console.error('Failed to load videos:', videosRes.status);
                    this.videos = [];
                    this.announcement = 'Failed to load videos';
                }
                document.title = `#${this.tag.name} - Damen's VLog`;
            } catch (e) {
                console.error('Failed to load tag:', e);
                this.error = 'Failed to load tag';
                this.announcement = 'Failed to load tag';
            } finally {
                this.loading = false;
            }
        },

        updateFilteredVideos() {
            if (!this.searchQuery) {
                this._filteredVideos = this.videos;
            } else {
                const query = this.searchQuery.slice(0, MAX_SEARCH_LENGTH).toLowerCase();
                this._filteredVideos = this.videos.filter(v =>
                    (v.title?.toLowerCase() || '').includes(query) ||
                    (v.description?.toLowerCase() || '').includes(query)
                );
            }
        },

        filterVideos() {
            this.updateFilteredVideos();
            this.announcement = `${this.resultCount} video${this.resultCount === 1 ? '' : 's'} found`;
        },

        async loadDisplayConfig() {
            try {
                const res = await VLogUtils.fetchWithTimeout('/api/config/display', {}, 5000);
                if (res.ok) {
                    const config = await res.json();
                    this.showViewCounts = config.show_view_counts !== false;
                    this.showTagline = config.show_tagline !== false;
                    this.tagline = config.tagline || '';
                }
            } catch (e) {
                console.debug('Failed to load display config, using defaults');
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

        clearSearch() {
            this.searchQuery = '';
            document.getElementById('search-input')?.focus();
        },

        formatDuration(seconds) {
            return VLogUtils.formatDuration(seconds);
        },

        formatDate(dateStr) {
            return VLogUtils.formatDate(dateStr);
        },

        formatViewCount(count) {
            return VLogUtils.formatViewCount(count);
        },

        getWatchProgress(videoId) {
            return this.watchProgressMap[videoId] || 0;
        },

        isInWatchLater(videoId) {
            return this.watchLaterIds.has(videoId);
        },

        toggleWatchLater(videoId) {
            const result = VLogUtils.watchLater.toggle(videoId);
            if (result.success) {
                if (result.inQueue) {
                    this.watchLaterIds.add(videoId);
                    this.announcement = 'Added to Watch Later';
                } else {
                    this.watchLaterIds.delete(videoId);
                    this.announcement = 'Removed from Watch Later';
                }
                // Force reactivity update
                this.watchLaterIds = new Set(this.watchLaterIds);
            } else {
                this.announcement = 'Unable to save - storage unavailable or full';
                console.error('Watch Later toggle failed for video', videoId);
            }
        }
    };
}
