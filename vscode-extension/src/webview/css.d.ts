// esbuild resolves a CSS import to a bundled stylesheet; TypeScript needs to be
// told the module exists at all.
declare module '*.css';
