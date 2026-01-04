/**
 * Category page Alpine.js component
 * Handles category video listing and search
 *
 * NOTE: This uses Alpine.js CSP build which cannot parse complex expressions.
 * All display values are precomputed on data objects (prefixed with _).
 */
'use strict';

(function() {
    const MAX_SEARCH_LENGTH = 200;
    const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

    function categoryPage() {
        return {
        category: null,
        videos: [],
        _filteredVideos: [],
        loading: true,
        error: null,
        announcement: '', // For screen reader announcements
        mobileNavOpen: false,
        _mobileNavExpanded: 'false', // Precomputed for CSP compatibility
        _mobileNavClass: '', // Precomputed for Alpine CSP
        _searchCountClass: '', // Precomputed for Alpine CSP
        _searchClearClass: '', // Precomputed for Alpine CSP
        _showSearchCount: false, // Precomputed for Alpine CSP
        previousFocus: null, // For focus restoration
        searchQuery: '',
        _searchResultText: '', // Precomputed search result text
        watchLaterIds: new Set(), // Track watch later IDs for UI state
        watchProgressMap: {}, // Map of videoId -> percentage watched
        // Display settings from API
        showViewCounts: true,
        showTagline: true,
        tagline: '',
        // Precomputed current year for footer (Alpine CSP)
        _currentYear: new Date().getFullYear(),
        _showFooterTagline: false, // Precomputed for Alpine CSP
        // Precomputed category display values
        _categoryName: '',
        _categoryDescription: '',
        _videoCountText: '',
        _showVideoGrid: false, // Precomputed for Alpine CSP
        _emptyStateTitle: 'No videos in this category yet',
        _emptyStateMessage: 'Check back soon for new content!',
        // Precomputed arrays for skeleton loaders (Alpine CSP)
        _skeletonArray8: [1, 2, 3, 4, 5, 6, 7, 8],

        get resultCount() {
            return this._filteredVideos.length;
        },

        async init() {
            // Load display config
            this.loadDisplayConfig();

            // Watch for changes to searchQuery and videos to update cached filteredVideos
            this.$watch('searchQuery', () => {
                this.updateFilteredVideos();
                this.updateSearchUIState();
            });
            this.$watch('videos', () => this.updateFilteredVideos());
            this.$watch('mobileNavOpen', (val) => {
                this._mobileNavExpanded = val ? 'true' : 'false';
                this._mobileNavClass = val ? 'mobile-nav--open' : '';
            });
            this.$watch('loading', () => this.updateSearchUIState());

            // Load watch later IDs from storage
            this.watchLaterIds = new Set(VLogUtils.watchLater.getVideoIds());

            // Load watch progress map for showing progress bars
            this.watchProgressMap = VLogUtils.watchHistory.getProgressMap();

            const slug = window.location.pathname.split('/').pop();
            if (!slug || !SLUG_PATTERN.test(slug)) {
                this.error = 'Invalid category';
                this.loading = false;
                return;
            }

            try {
                const [catRes, videosRes] = await Promise.all([
                    VLogUtils.fetchWithTimeout(`/api/categories/${encodeURIComponent(slug)}`, {}, 10000),
                    VLogUtils.fetchWithTimeout(`/api/videos?category=${encodeURIComponent(slug)}`, {}, 10000)
                ]);

                if (!catRes.ok) {
                    this.error = catRes.status === 404 ? 'Category not found' : 'Failed to load category';
                    this.loading = false;
                    return;
                }

                this.category = await catRes.json();
                this._categoryName = this.category.name || '';
                this._categoryDescription = this.category.description || '';

                if (videosRes.ok) {
                    const data = await videosRes.json();
                    this.videos = (data.videos || []).map(v => this.enrichVideo(v));
                    const count = this.videos.length;
                    this.announcement = this.category.name + ' category with ' + count + ' video' + (count === 1 ? '' : 's');
                } else {
                    console.error('Failed to load videos:', videosRes.status);
                    this.videos = [];
                    this.announcement = 'Failed to load videos';
                }
                this.updateVideoCountText();
                document.title = this.category.name + " - Damen's VLog";
            } catch (e) {
                console.error('Failed to load category:', e);
                this.error = 'Failed to load category';
                this.announcement = 'Failed to load category';
            } finally {
                this.loading = false;
            }
        },

        // Enrich video object with precomputed display values for CSP compatibility
        enrichVideo(video) {
            const progress = this.watchProgressMap[video.id] || 0;
            const inWatchLater = this.watchLaterIds.has(video.id);
            let ariaLabel = video.title + ', ' + VLogUtils.formatDuration(video.duration);
            if (this.showViewCounts && video.view_count > 0) {
                ariaLabel += ', ' + VLogUtils.formatViewCount(video.view_count);
            }
            return {
                ...video,
                _href: '/watch/' + video.slug,
                _ariaLabel: ariaLabel,
                _duration: VLogUtils.formatDuration(video.duration),
                _publishedDate: VLogUtils.formatDate(video.published_at),
                _viewCount: VLogUtils.formatViewCount(video.view_count),
                _showViewCount: this.showViewCounts && video.view_count > 0,
                _hasProgress: progress > 0,
                _progressClass: 'video-card__progress-bar--' + Math.max(5, Math.min(100, Math.round(progress / 5) * 5)),
                _inWatchLater: inWatchLater,
                _watchLaterClass: inWatchLater ? 'video-card__action--active' : '',
                _watchLaterLabel: inWatchLater ? 'Remove from Watch Later' : 'Add to Watch Later',
                _watchLaterPressed: inWatchLater ? 'true' : 'false'
            };
        },

        // Re-enrich all videos (call after watch later changes)
        refreshVideoEnrichment() {
            this.videos = this.videos.map(v => this.enrichVideo(v));
            this.updateFilteredVideos();
        },

        updateVideoCountText() {
            const count = this.searchQuery ? this.resultCount : (this.category?.video_count || 0);
            this._videoCountText = count + ' video' + (count === 1 ? '' : 's');
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
            this._searchResultText = this.resultCount + ' result' + (this.resultCount === 1 ? '' : 's');
            this.updateVideoCountText();
            this._showVideoGrid = !this.loading && this._filteredVideos.length > 0;
            this.updateEmptyStateText();
        },

        updateSearchUIState() {
            this._showSearchCount = this.searchQuery && !this.loading;
            this._searchCountClass = this.searchQuery ? 'site-header__search-count--has-clear' : '';
            this._searchClearClass = this.searchQuery ? 'site-header__search-clear--visible' : '';
        },

        updateEmptyStateText() {
            if (this.searchQuery) {
                this._emptyStateTitle = 'No videos match your search';
                this._emptyStateMessage = 'Try adjusting your search terms.';
            } else {
                this._emptyStateTitle = 'No videos in this category yet';
                this._emptyStateMessage = 'Check back soon for new content!';
            }
        },

        filterVideos() {
            this.updateFilteredVideos();
            this.announcement = this.resultCount + ' video' + (this.resultCount === 1 ? '' : 's') + ' found';
        },

        async loadDisplayConfig() {
            try {
                const res = await VLogUtils.fetchWithTimeout('/api/config/display', {}, 5000);
                if (res.ok) {
                    const config = await res.json();
                    this.showViewCounts = config.show_view_counts !== false;
                    this.showTagline = config.show_tagline !== false;
                    this.tagline = config.tagline || '';
                    this._showFooterTagline = this.showTagline && this.tagline;
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

        // Empty state helpers (no arguments needed)
        getEmptyStateTitle() {
            return this.searchQuery ? 'No videos match your search' : 'No videos in this category yet';
        },

        getEmptyStateMessage() {
            return this.searchQuery ? 'Try adjusting your search terms.' : 'Check back soon for new content!';
        },

        toggleWatchLater(video) {
            const videoId = video.id || video;
            const result = VLogUtils.watchLater.toggle(videoId);
            if (result.success) {
                if (result.inQueue) {
                    this.watchLaterIds.add(videoId);
                    this.announcement = 'Added to Watch Later';
                } else {
                    this.watchLaterIds.delete(videoId);
                    this.announcement = 'Removed from Watch Later';
                }
                // Force reactivity update and re-enrich videos
                this.watchLaterIds = new Set(this.watchLaterIds);
                this.refreshVideoEnrichment();
            } else {
                this.announcement = 'Unable to save - storage unavailable or full';
                console.error('Watch Later toggle failed for video', videoId);
            }
        },

        // CSP-compatible version: reads video ID from data attribute
        toggleWatchLaterById() {
            const videoId = parseInt(this.$el.dataset.videoId, 10);
            if (videoId) {
                this.toggleWatchLater(videoId);
            }
        }
    };
    }

    // Register with Alpine.js when it initializes
    document.addEventListener('alpine:init', () => {
        Alpine.data('categoryPage', categoryPage);
    });
})();
