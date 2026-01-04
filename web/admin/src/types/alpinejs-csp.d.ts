/**
 * Type declarations for @alpinejs/csp
 *
 * The CSP build of Alpine.js has the same API as the regular build,
 * but uses a different internal implementation that avoids eval().
 */
declare module '@alpinejs/csp' {
    import Alpine from 'alpinejs';
    const alpine: typeof Alpine;
    export default alpine;
}
