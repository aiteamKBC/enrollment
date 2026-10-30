// The browser build of mammoth, which is the only entry point this app
// imports. Importing the package root as well made Vite prebundle mammoth
// twice -- once for the node entry and once for this one -- so every call
// site uses this spelling and the declaration has to cover what they call.
declare module 'mammoth/mammoth.browser' {
  export function convertToHtml(input: { arrayBuffer: ArrayBuffer }): Promise<{ value: string; messages: unknown[] }>;
  export function extractRawText(input: { arrayBuffer: ArrayBuffer | Uint8Array }): Promise<{ value: string; messages: unknown[] }>;
}
