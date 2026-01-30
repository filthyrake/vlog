/**
 * Type declarations for @alpinejs/csp
 */

declare module '@alpinejs/csp' {
  interface Alpine {
    data: (name: string, callback: () => object) => void;
    start: () => void;
  }
  const alpine: Alpine;
  export default alpine;
}
