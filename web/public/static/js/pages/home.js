/**
 * Home page Alpine.js component
 * Handles video listing, search, categories, and continue watching
 */

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
        // Display settings from API
        showViewCounts: true,
        showTagline: true,
        tagline: '',

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
                this.categories = await res.json();
            } catch (e) {
                console.error('Failed to load categories:', e);
                this.categories = [];
            }
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
                this.videos = data.videos || [];
                // Announce results to screen readers
                this.announcement = `${this.videos.length} video${this.videos.length === 1 ? '' : 's'} found`;
            } catch (e) {
                console.error('Failed to load videos:', e);
                this.videos = [];
                this.announcement = 'Failed to load videos';
            } finally {
                this.loading = false;
            }
        },

        setViewMode(mode) {
            this.viewMode = mode;
            VLogUtils.preferences.set('viewMode', mode);
            this.announcement = mode === 'grid' ? 'Grid view' : 'List view';
        },

        async loadFeaturedVideo() {
            try {
                const res = await VLogUtils.fetchWithTimeout('/api/videos?featured=true&limit=1', {}, 10000);
                if (!res.ok) {
                    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
                }
                const data = await res.json();
                const videos = data.videos || [];
                this.featuredVideo = videos.length > 0 ? videos[0] : null;
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
                this.continueWatching = watchedItems
                    .filter(item => videoMap.has(item.videoId))
                    .map(item => ({
                        video: videoMap.get(item.videoId),
                        position: item.position,
                        duration: item.duration,
                        percentage: item.percentage
                    }));
                this.continueWatchingLoading = false;
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
                    console.error('Continue Watching unavailable - all retries failed');
                }
            }
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
