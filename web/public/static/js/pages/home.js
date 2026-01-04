/**
 * Home page Alpine.js component
 * Handles video listing, search, categories, and continue watching
 */
'use strict';

(function() {
    const MAX_SEARCH_LENGTH = 200;

    function app() {
        return {
        videos: [],
        categories: [],
        loading: true,
        searchQuery: '',
        selectedCategory: null,
        announcement: '', // For screen reader announcements
        mobileNavOpen: false,
        previousFocus: null, // For focus restoration
        featuredVideo: null, // Hero section featured video
        continueWatching: [], // Continue watching videos with metadata
        continueWatchingLoading: false, // Loading state for continue watching section
        continueWatchingError: false, // Error state for retry failures
        watchLaterIds: new Set(), // Track watch later IDs for UI state
        watchProgressMap: {}, // Map of videoId -> percentage watched
        // Filter/sort state (Issue #413 Phase 3)
        sortBy: 'date-desc',
        durationFilter: '',
        viewMode: 'grid',
        // Precomputed view mode classes for Alpine CSP
        _videoGridClass: 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6',
        _videoCardClass: '',
        _showFilterBar: true, // Show filter bar when videos exist or loading
        _hasCategories: false, // Precomputed for Alpine CSP
        _hasVideos: false, // Precomputed for Alpine CSP
        _showVideoGrid: false, // Precomputed for Alpine CSP (!loading && videos.length > 0)
        _showEmptyState: false, // Precomputed for Alpine CSP (!loading && videos.length === 0)
        _showContinueWatching: false, // Precomputed for Alpine CSP
        // Display settings from API
        showViewCounts: true,
        showTagline: true,
        tagline: '',
        // Precomputed current year for footer (Alpine CSP)
        _currentYear: new Date().getFullYear(),
        _showFooterTagline: false, // Precomputed for Alpine CSP
        _emptyStateTitle: 'No videos found',
        _emptyStateMessage: 'Check back soon for new content!',
        // Precomputed arrays for skeleton loaders (Alpine CSP)
        _skeletonArray4: [1, 2, 3, 4],
        _skeletonArray8: [1, 2, 3, 4, 5, 6, 7, 8],
        // Precomputed UI state for Alpine CSP
        _searchResultText: '',
        _mobileNavAriaExpanded: 'false',
        _allCategoryButtonClass: 'bg-blue-600 text-white',
        _allCategoryAriaPressed: 'true',
        _videosSectionTitle: 'Latest Videos',
        _gridViewToggleClass: 'filter-bar__toggle--active',
        _gridViewAriaPressed: 'true',
        _listViewToggleClass: '',
        _listViewAriaPressed: 'false',
        // More precomputed UI state for search/nav
        _searchCountClass: '',
        _searchClearClass: '',
        _showSearchCount: false,
        _mobileNavClass: '',
        _showContinueWatchingLoading: false,
        _showContinueWatchingError: false,

        async init() {
            // Load display config
            this.loadDisplayConfig();
            // Check for search param from URL (e.g., from watch page search)
            const urlParams = new URLSearchParams(window.location.search);
            const searchParam = urlParams.get('search') || urlParams.get('q');
            if (searchParam) {
                // Limit search length for safety
                this.searchQuery = searchParam.slice(0, MAX_SEARCH_LENGTH);
                // Clean URL without reloading
                window.history.replaceState({}, '', '/');
            }

            // Load watch later IDs from storage
            this.watchLaterIds = new Set(VLogUtils.watchLater.getVideoIds());

            // Load watch progress map for showing progress bars
            this.watchProgressMap = VLogUtils.watchHistory.getProgressMap();

            // Load saved preferences
            this.sortBy = VLogUtils.preferences.get('sortBy', 'date-desc');
            this.durationFilter = VLogUtils.preferences.get('durationFilter', '');
            this.viewMode = VLogUtils.preferences.get('viewMode', 'grid');
            this.updateViewModeClasses();

            // Watch for search changes to update visibility and empty state text
            this.$watch('searchQuery', () => {
                this.updateContinueWatchingVisibility();
                this.updateEmptyStateText();
            });

            // Watch for mobile nav changes
            this.$watch('mobileNavOpen', (val) => {
                this._mobileNavAriaExpanded = val ? 'true' : 'false';
                this._mobileNavClass = val ? 'mobile-nav--open' : '';
            });

            // Watch for search/loading changes to update search UI
            this.$watch('searchQuery', () => this.updateSearchUIState());
            this.$watch('loading', () => this.updateSearchUIState());

            // Watch for continue watching state changes
            this.$watch('continueWatchingLoading', () => this.updateContinueWatchingState());
            this.$watch('continueWatchingError', () => this.updateContinueWatchingState());

            // Load main content
            await Promise.all([
                this.loadCategories(),
                this.loadVideos(),
                this.loadFeaturedVideo()
            ]);

            // Load continue watching after main content (progressive loading)
            this.loadContinueWatching();
        },

        openMobileNav() {
            this.previousFocus = document.activeElement;
            this.mobileNavOpen = true;
            document.body.style.overflow = 'hidden';
            // Focus close button after drawer opens
            this.$nextTick(() => {
                this.$refs.closeBtn?.focus();
            });
        },

        closeMobileNav() {
            this.mobileNavOpen = false;
            document.body.style.overflow = '';
            // Restore focus to menu button
            this.$nextTick(() => {
                if (this.previousFocus) {
                    this.previousFocus.focus();
                    this.previousFocus = null;
                }
            });
        },

        async loadCategories() {
            try {
                const res = await VLogUtils.fetchWithTimeout('/api/categories', {}, 10000);
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
                }
                const cats = await res.json();
                // Enrich categories with precomputed display values
                this.categories = cats.map(c => this.enrichCategory(c));
                this._hasCategories = this.categories.length > 0;
            } catch (e) {
                console.error('Failed to load categories:', e);
                this.categories = [];
                this._hasCategories = false;
            }
        },

        // Re-enrich categories when selection changes
        updateCategoryDisplay() {
            this.categories = this.categories.map(c => this.enrichCategory(c));
        },

        async loadVideos() {
            this.loading = true;
            try {
                let url = '/api/videos?';
                if (this.selectedCategory) {
                    url += `category=${this.selectedCategory}&`;
                }
                if (this.searchQuery) {
                    // Limit search length
                    const query = this.searchQuery.slice(0, MAX_SEARCH_LENGTH);
                    url += `search=${encodeURIComponent(query)}&`;
                }
                // Add sort parameter
                const [sortField, sortOrder] = this.sortBy.split('-');
                url += `sort=${sortField}&order=${sortOrder}&`;
                // Save sort preference
                VLogUtils.preferences.set('sortBy', this.sortBy);

                // Add duration filter
                if (this.durationFilter) {
                    if (this.durationFilter === 'short') {
                        url += 'duration_max=300&'; // <5 minutes
                    } else if (this.durationFilter === 'medium') {
                        url += 'duration_min=300&duration_max=1200&'; // 5-20 minutes
                    } else if (this.durationFilter === 'long') {
                        url += 'duration_min=1200&'; // >20 minutes
                    }
                    VLogUtils.preferences.set('durationFilter', this.durationFilter);
                } else {
                    VLogUtils.preferences.set('durationFilter', '');
                }

                const res = await VLogUtils.fetchWithTimeout(url, {}, 10000);
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
                }
                const data = await res.json();
                // Enrich videos with precomputed display values for Alpine CSP compatibility
                this.videos = (data.videos || []).map(v => this.enrichVideo(v));
                // Announce results to screen readers
                this.announcement = `${this.videos.length} video${this.videos.length === 1 ? '' : 's'} found`;
            } catch (e) {
                console.error('Failed to load videos:', e);
                this.videos = [];
                this.announcement = 'Failed to load videos';
            } finally {
                this.loading = false;
                this._hasVideos = this.videos.length > 0;
                this._showFilterBar = this._hasVideos || this.loading;
                this._showVideoGrid = !this.loading && this._hasVideos;
                this._showEmptyState = !this.loading && !this._hasVideos;
                this.updateEmptyStateText();
                this._searchResultText = this.videos.length + ' result' + (this.videos.length === 1 ? '' : 's');
                this._videosSectionTitle = this.selectedCategory ? 'Videos' : 'Latest Videos';
                this.updateAllCategoryState();
            }
        },

        setViewMode(mode) {
            this.viewMode = mode;
            this.updateViewModeClasses();
            VLogUtils.preferences.set('viewMode', mode);
            this.announcement = mode === 'grid' ? 'Grid view' : 'List view';
        },

        // CSP-compatible version: reads mode from data attribute on clicked element
        setViewModeFromData() {
            const mode = this.$el.dataset.mode;
            if (mode) {
                this.setViewMode(mode);
            }
        },

        updateViewModeClasses() {
            if (this.viewMode === 'list') {
                this._videoGridClass = 'flex flex-col gap-4';
                this._videoCardClass = 'video-card--list';
                this._gridViewToggleClass = '';
                this._gridViewAriaPressed = 'false';
                this._listViewToggleClass = 'filter-bar__toggle--active';
                this._listViewAriaPressed = 'true';
            } else {
                this._videoGridClass = 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6';
                this._videoCardClass = '';
                this._gridViewToggleClass = 'filter-bar__toggle--active';
                this._gridViewAriaPressed = 'true';
                this._listViewToggleClass = '';
                this._listViewAriaPressed = 'false';
            }
        },

        updateContinueWatchingVisibility() {
            this._showContinueWatching = !this.continueWatchingLoading &&
                !this.continueWatchingError &&
                this.continueWatching.length > 0 &&
                !this.searchQuery;
        },

        updateAllCategoryState() {
            if (this.selectedCategory === null) {
                this._allCategoryButtonClass = 'bg-blue-600 text-white';
                this._allCategoryAriaPressed = 'true';
            } else {
                this._allCategoryButtonClass = 'bg-dark-800 text-dark-300 hover:bg-dark-700';
                this._allCategoryAriaPressed = 'false';
            }
        },

        updateSearchUIState() {
            this._showSearchCount = this.searchQuery && !this.loading;
            this._searchCountClass = this.searchQuery ? 'site-header__search-count--has-clear' : '';
            this._searchClearClass = this.searchQuery ? 'site-header__search-clear--visible' : '';
        },

        updateContinueWatchingState() {
            this._showContinueWatchingLoading = this.continueWatchingLoading && !this.searchQuery;
            this._showContinueWatchingError = this.continueWatchingError && !this.searchQuery;
            this.updateContinueWatchingVisibility();
        },

        updateEmptyStateText() {
            if (this.searchQuery) {
                this._emptyStateTitle = 'No videos match your search';
                this._emptyStateMessage = 'Try adjusting your search terms or browse all videos.';
            } else {
                this._emptyStateTitle = 'No videos found';
                this._emptyStateMessage = 'Check back soon for new content!';
            }
        },

        getMobileNavAriaExpanded() {
            return this.mobileNavOpen ? 'true' : 'false';
        },

        getGridViewToggleClass() {
            return this.viewMode === 'grid' ? 'filter-bar__toggle--active' : '';
        },

        getGridViewAriaPressed() {
            return this.viewMode === 'grid' ? 'true' : 'false';
        },

        getListViewToggleClass() {
            return this.viewMode === 'list' ? 'filter-bar__toggle--active' : '';
        },

        getListViewAriaPressed() {
            return this.viewMode === 'list' ? 'true' : 'false';
        },

        // Enrich a video object with precomputed display values for Alpine CSP
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
                _category: video.category_name || 'Uncategorized',
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

        // Enrich a category object with precomputed display values
        enrichCategory(cat) {
            const isSelected = this.selectedCategory === cat.slug;
            return {
                ...cat,
                _href: '/category/' + cat.slug,
                _name: cat.name,
                _buttonClass: isSelected ? 'bg-blue-600 text-white' : 'bg-dark-800 text-dark-300 hover:bg-dark-700',
                _ariaPressed: isSelected ? 'true' : 'false',
                _count: '(' + cat.video_count + ')'
            };
        },

        async loadFeaturedVideo() {
            try {
                const res = await VLogUtils.fetchWithTimeout('/api/videos?featured=true&limit=1', {}, 10000);
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
                }
                const data = await res.json();
                const videos = data.videos || [];
                if (videos.length > 0) {
                    const v = videos[0];
                    this.featuredVideo = {
                        ...v,
                        _href: '/watch/' + v.slug,
                        _ariaLabel: 'Featured: ' + v.title,
                        _category: v.category_name || 'Uncategorized',
                        _duration: VLogUtils.formatDuration(v.duration),
                        _viewCount: VLogUtils.formatViewCount(v.view_count),
                        _showViewCount: this.showViewCounts && v.view_count > 0
                    };
                } else {
                    this.featuredVideo = null;
                }
            } catch (e) {
                console.error('Failed to load featured video:', e);
                this.featuredVideo = null;
            }
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
                // Use defaults on error
                console.debug('Failed to load display config, using defaults');
            }
        },

        async loadContinueWatching(retryCount = 0) {
            const MAX_RETRIES = 2;
            const RETRY_DELAYS = [0, 1000, 3000]; // Exponential backoff

            // Set loading state only on first attempt, reset error
            if (retryCount === 0) {
                this.continueWatchingLoading = true;
                this.continueWatchingError = false;
            }

            try {
                // Get partially watched videos from localStorage
                const watchedItems = VLogUtils.watchHistory.getContinueWatching(10);
                if (watchedItems.length === 0) {
                    this.continueWatching = [];
                    this.continueWatchingLoading = false;
                    this.updateContinueWatchingVisibility();
                    return;
                }

                // Fetch video metadata using bulk endpoint
                const ids = watchedItems.map(item => item.videoId).join(',');
                const res = await VLogUtils.fetchWithTimeout(`/api/videos/bulk?ids=${ids}`, {}, 10000);
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
                }
                const videos = await res.json();

                // Create a map for quick lookup
                const videoMap = new Map(videos.map(v => [v.id, v]));

                // Clean up stale IDs (videos that no longer exist)
                const validIds = videos.map(v => v.id);
                VLogUtils.watchHistory.cleanupStale(validIds);

                // Merge watch progress with video metadata, preserving order
                // Enrich with precomputed display values for Alpine CSP
                this.continueWatching = watchedItems
                    .filter(item => videoMap.has(item.videoId))
                    .map(item => {
                        const video = videoMap.get(item.videoId);
                        return {
                            video: video,
                            position: item.position,
                            duration: item.duration,
                            percentage: item.percentage,
                            _href: '/watch/' + video.slug + '?t=' + Math.floor(item.position),
                            _ariaLabel: video.title + ', ' + VLogUtils.formatDuration(video.duration) + ', ' + Math.round(100 - item.percentage) + '% remaining',
                            _thumbnail: video.thumbnail_url,
                            _title: video.title,
                            _duration: VLogUtils.formatDuration(video.duration),
                            _progressClass: 'video-card__progress-bar--' + Math.max(5, Math.min(100, Math.round(item.percentage / 5) * 5)),
                            _remaining: VLogUtils.formatDuration(video.duration - item.position),
                            _category: video.category_name || 'Uncategorized'
                        };
                    });
                this.continueWatchingLoading = false;
                this.updateContinueWatchingVisibility();
            } catch (e) {
                console.error(`Failed to load continue watching (attempt ${retryCount + 1}/${MAX_RETRIES + 1}):`, e);

                if (retryCount < MAX_RETRIES) {
                    const delay = RETRY_DELAYS[retryCount + 1];
                    console.log(`Retrying in ${delay}ms...`);
                    setTimeout(() => this.loadContinueWatching(retryCount + 1), delay);
                } else {
                    // All retries exhausted - show error state
                    this.continueWatching = [];
                    this.continueWatchingLoading = false;
                    this.continueWatchingError = true;
                    this.updateContinueWatchingVisibility();
                    console.error('Continue Watching unavailable - all retries failed');
                }
            }
        },

        getSearchResultText() {
            return this.videos.length + ' result' + (this.videos.length === 1 ? '' : 's');
        },

        getVideosSectionTitle() {
            return this.selectedCategory ? 'Videos' : 'Latest Videos';
        },

        getEmptyStateTitle() {
            return this.searchQuery ? 'No videos match your search' : 'No videos found';
        },

        getEmptyStateMessage() {
            return this.searchQuery ? 'Try adjusting your search terms or browse all videos.' : 'Check back soon for new content!';
        },

        // Category helpers
        clearCategoryFilter() {
            this.selectedCategory = null;
            this.updateCategoryDisplay();
            this.loadVideos();
        },

        getAllCategoryButtonClass() {
            if (this.selectedCategory === null) {
                return 'bg-blue-600 text-white';
            }
            return 'bg-dark-800 text-dark-300 hover:bg-dark-700';
        },

        getAllCategoryAriaPressed() {
            return this.selectedCategory === null ? 'true' : 'false';
        },

        selectCategory(cat) {
            this.selectedCategory = cat.slug;
            this.updateCategoryDisplay();
            this.loadVideos();
        },

        // CSP-compatible version: reads slug from data attribute on clicked element
        selectCategoryBySlug() {
            const slug = this.$el.dataset.slug;
            if (slug) {
                this.selectedCategory = slug;
                this.updateCategoryDisplay();
                this.loadVideos();
            }
        },

        getCategoryButtonClass(cat) {
            if (this.selectedCategory === cat.slug) {
                return 'bg-blue-600 text-white';
            }
            return 'bg-dark-800 text-dark-300 hover:bg-dark-700';
        },

        getCategoryAriaPressed(cat) {
            return this.selectedCategory === cat.slug ? 'true' : 'false';
        },

        getCategoryName(cat) {
            return cat.name;
        },

        getCategoryCount(cat) {
            return '(' + cat.video_count + ')';
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

        // Continue Watching helpers (avoid nested property access in templates)
        getContinueWatchingHref(item) {
            return '/watch/' + item.video.slug + '?t=' + Math.floor(item.position);
        },

        getContinueWatchingLabel(item) {
            return item.video.title + ', ' + this.formatDuration(item.video.duration) + ', ' + Math.round(100 - item.percentage) + '% remaining';
        },

        getContinueWatchingThumbnail(item) {
            return item.video.thumbnail_url;
        },

        getContinueWatchingTitle(item) {
            return item.video.title;
        },

        getContinueWatchingDuration(item) {
            return this.formatDuration(item.video.duration);
        },

        getContinueWatchingProgressStyle(item) {
            return 'width:' + item.percentage + '%';
        },

        getContinueWatchingRemaining(item) {
            return this.formatDuration(item.video.duration - item.position);
        },

        getContinueWatchingCategory(item) {
            return item.video.category_name || 'Uncategorized';
        },

        // Video card helpers (avoid nested property access in templates)
        getVideoHref(video) {
            return '/watch/' + video.slug;
        },

        getVideoAriaLabel(video) {
            let label = video.title + ', ' + this.formatDuration(video.duration);
            if (this.showViewCounts && video.view_count > 0) {
                label += ', ' + this.formatViewCount(video.view_count);
            }
            return label;
        },

        getVideoThumbnail(video) {
            return video.thumbnail_url;
        },

        getVideoTitle(video) {
            return video.title;
        },

        getVideoDuration(video) {
            return this.formatDuration(video.duration);
        },

        getVideoCategory(video) {
            return video.category_name || 'Uncategorized';
        },

        shouldShowViewCount(video) {
            return this.showViewCounts && video.view_count > 0;
        },

        getVideoViewCount(video) {
            return this.formatViewCount(video.view_count);
        },

        getVideoPublishedDate(video) {
            return this.formatDate(video.published_at);
        },

        getWatchProgress(video) {
            return this.watchProgressMap[video.id] || 0;
        },

        getWatchProgressStyle(video) {
            return 'width:' + (this.watchProgressMap[video.id] || 0) + '%';
        },

        isInWatchLater(video) {
            return this.watchLaterIds.has(video.id);
        },

        getWatchLaterLabel(video) {
            return this.watchLaterIds.has(video.id) ? 'Remove from Watch Later' : 'Add to Watch Later';
        },

        getWatchLaterPressed(video) {
            return this.watchLaterIds.has(video.id) ? 'true' : 'false';
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
                // Force reactivity update
                this.watchLaterIds = new Set(this.watchLaterIds);
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
        },

        clearAllWatchHistory() {
            VLogUtils.storage.remove('vlog_watch_history');
            this.continueWatching = [];
            this.watchProgressMap = {};
            this.announcement = 'Watch history cleared';
        },

        clearSearch() {
            this.searchQuery = '';
            this.loadVideos();
            document.getElementById('search-input')?.focus();
        }
    };
    }

    // Register with Alpine.js when it initializes
    document.addEventListener('alpine:init', () => {
        Alpine.data('app', app);
    });
})();
